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
from conftest import CARD_BODY, RESEARCH, SETTLED_NOTE, SHORT_ASK, Answers, explained, Chrome, Drawing, Heard, Jev, Judge, MetaLaunches, Thread, WatchProcess, Withdrawals
from drive import (
    brief,
    card,
    cli,
    github,
    install,
    limits,
    orca,
    picture,
    place,
    reads,
    render,
    served,
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
DESIGN_TITLE = "Where should store_copy_at in supervisor/copies.rs run each store copy?"
SECOND_OPTION = (
    "- store_copy_at runs on a separate worker that reads each store from the bucket and copies it there\n"
    "  A new worker in `supervisor/worker.rs` reads each store from the team's bucket and calls store_copy_at there, so the primary"
    " serves only queries; each cluster runs one more process.\n"
)
DESIGN_RESEARCH = (
    f"DESIGN {DESIGN_TITLE}\n"
    "store_copy_at copies one store, and it can run on the primary or on a separate worker that reads the bucket.\n"
    "Recommended: on the primary; held until you pick, so nothing ships meanwhile.\n"
    "- store_copy_at runs on the primary, beside the queries it serves, copying each store in list order (recommended)\n"
    "  The primary calls store_copy_at in `supervisor/copies.rs` for each store in list order between the queries it serves, writing"
    " each copy to the team's bucket for a restore to read; each query waits behind a running copy.\n"
    f"{SECOND_OPTION}Findings:\n{ROOT_CAUSE}{RESEARCH.removeprefix('DONE researched the store copy\n')}"
)
DESIGNED = f"""DESIGN the store copy
Design: one copy per store, in the order the stores list
{ROOT_CAUSE}Settled: "copy one store"
Native: none: he never sees it
Exists: none: ccx code search "store copy" and rg store_copy found nothing
Change: copy each store in list order
Fork: none: one layout fits
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
First principles: The journal engine attaches the record
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


def test_drive_start_starts_the_meta_session_beside_the_others_once_until_it_binds(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta_launches: MetaLaunches
) -> None:
    out = run("start", "demo", "--role", "communicator", "--channel", "D123")[1]
    assert "\n\nstarted the meta session, which holds drive tooling, in Orca terminal term_meta1\ncommunicator: " in out
    assert (meta_launches.started, state.load("demo")["launched"]) == ([("demo", str(tmp_path))], {"meta": "term_meta1"})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "coordinator-session")
    assert "\n\nthe meta session starts in Orca terminal term_meta1, which an earlier drive start opened\n" in run("start", "demo", "--role", "coordinator")[1]
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
    for project in ("A/B", "A B", "Root", "Iris 2", "Handler", "Memory"):
        run("order", "add", "--title", "Short title", "--project", project, "--workstream", "W", f"work for {project}")
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
    run("order", "add", "--title", "Short title", "--project", "A/B", "--workstream", "W", "more work")
    posted_lanes.add("a-b")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ab-2")
    assert run("start", "demo", "--role", "coordinator", "--project", "A/B")[0] == 0
    bound = state.load("demo")["subcoordinators"]
    assert ({project: held["session"] for project, held in bound.items()}, bound["A/B"]["from"]) == ({"A/B": "ab-2"}, first)


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
    assert data["slack"]["channel"] == "D123"
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
    assert "Open orders you run: 1, a line each; drive status <id> prints one whole, with its jobs, PRs and questions:" in text
    assert "build o1" not in text and "fix the invoice" not in text
    assert "fix the invoice" in run("status", "o2")[1]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", top)
    orders, agentless, text = shown()
    assert (orders, agentless) == (["o2"], ["j2"])
    assert "ship the run page" not in text and "fix the watch" not in text
    assert set(json.loads(run("status", "--json")[1])["orders"]) == {"o1", "o2", "o3"}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "bound-to-none")
    assert shown()[:2] == (["o1", "o2", "o3"], ["j1", "j2", "j3"])


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
    ]
    permalink = f"https://acme.slack.com/archives/C0BUGS1/p{bug_reply.replace('.', '')}?thread_ts={bug_root}&cid=C0BUGS1"
    assert run(*filed, "and the dashboard too", "--from", f"slack:C0BUGS1/{bug_root}/{bug_reply}", "--thread", bug_root) == (0, "o3")
    assert run(*filed, "and the card", "--from", permalink) == (0, "o4")
    assert run(*filed, "and the list", "--from", f"slack:C0BUGS1/{bug_root}/{bug_reply}", "--thread", f"slack:C0BUGS1/{bug_root}") == (0, "o5")
    assert len(reads) == 2
    data = state.load("demo")
    assert {order_id: (order["from"], order["thread"], state.promise_thread(order)) for order_id, order in data["orders"].items()} == {
        "o1": (f"slack:D123/{reply}", ["D123", root], ("D123", root)),
        "o2": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], ("C0BUGS1", bug_root)),
        "o3": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], ("C0BUGS1", bug_root)),
        "o4": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], ("C0BUGS1", bug_root)),
        "o5": (f"slack:C0BUGS1/{bug_reply}", ["C0BUGS1", bug_root], ("C0BUGS1", bug_root)),
    }
    assert state.message_order(data, "C0BUGS1", bug_root, bug_reply) == "o2"
    assert cli.thread_refusal(data, "o1", ("D123", root), {}) is None
    assert cli.main([*filed, "look at this", "--from", "slack:C0GONE1/1794341700.000300"]) == 2
    assert (
        "--from slack:C0GONE1/1794341700.000300: refused, Slack cannot read the message it names: cc-slack can't read this conversation (channel_not_found)"
        in capsys.readouterr().err
    )
    assert set(state.load("demo")["orders"]) == {"o1", "o2", "o3", "o4", "o5"}


def test_order_set_from_points_an_order_at_his_dm_post_while_its_lines_stay_in_the_alert_thread(run: Run, demo: str, slack_thread: Thread) -> None:
    alert, reply, dm_post = "1794493064.110609", "1794495071.669329", "1794495097.793329"
    filed = ["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling"]
    assert run(*filed, "unpause the release", "--from", f"slack:C0822AHFY3G/{alert}/{reply}") == (0, "o1")
    assert slack_thread.reads == []
    order = state.load("demo")["orders"]["o1"]
    assert (order["from"], order["thread"]) == (f"slack:C0822AHFY3G/{reply}", ["C0822AHFY3G", alert])
    dm_link, alert_link = (f"https://acme.slack.com/archives/{channel}/p{ts.replace('.', '')}" for channel, ts in (("D123", dm_post), ("C0822AHFY3G", alert)))
    assert run("order", "set", "o1", "--from", dm_link, "--thread", alert_link)[0] == 0
    data = state.load("demo")
    assert (data["orders"]["o1"]["from"], state.promise_thread(data["orders"]["o1"])) == (f"slack:D123/{dm_post}", ("C0822AHFY3G", alert))
    assert state.message_order(data, "D123", dm_post, dm_post) == "o1"
    assert run("order", "set", "o1", "--from", f"slack:D123/{dm_post}")[0] == 0
    assert state.load("demo")["orders"]["o1"]["thread"] == ["C0822AHFY3G", alert]
    assert slack_thread.reads == [("--channel", "C0822AHFY3G", "--thread", alert, "--limit", "1")]


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
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/7", "--job", "j1")[0] == 0
    assert run("job", "set", "j1", "--waits", wait)[0] == 0
    assert state.load("demo")["jobs"]["j1"]["waits"] == [wait]


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
        f"stored {render.when(order['next_due'])} behind the line already owed for {due}: that time keeps firing DUE until the next"
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
            'j6 on "fix": start its agent now, its brief ending Drive: demo, order o4, job j6\n'
            'j7 on "first check": start its agent now, its brief ending Drive: demo, order o4, job j7\n'
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
    assert cli.main(["order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slower", "--joins", "o3"]) == 2
    assert "--joins o3: name an open incident order; o3 is open, not an incident" in capsys.readouterr().err


def test_an_order_becomes_an_incident_only_once_a_job_owns_its_fix(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "runs fail for one team", "--parts", "first check")
    assert cli.main(["order", "set", "o1", "--incident"]) == 2
    assert 'o1: refused, an incident order starts its fix job at the alert, beside diagnosis: give it a "fix" part' in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["incident"] is False
    run("order", "set", "o1", "--parts", "first check", "fix")
    run("job", "add", "codex", "--order", "o1", "--goal", "fix: runs fail", "--part", "fix", "--agent", "a-1")
    assert run("order", "set", "o1", "--incident")[0] == 0
    assert state.load("demo")["orders"]["o1"]["incident"] is True


RECORD = "sandsql on plat: release 60 cannot restore databases, since 9:16 PM"
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
    assert run("incident", "open", words, "--release", "--record", RECORD, *FILED) == (
        0,
        (
            "o1 record 5f6b2b5\n"
            'j1 on "fix": start its agent now, its brief ending Drive: demo, order o1, job j1\n'
            'j2 on "first check": start its agent now, its brief ending Drive: demo, order o1, job j2\n'
            f"{state.INCIDENT_BRIEF}"
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


def test_an_incident_records_the_monitor_or_sentry_issue_of_each_alert_and_refuses_any_other_link(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    monitor, lag, sentry = "https://app.datadoghq.com/monitors/315028134?group=forge_cluster%3Aplat", "https://us5.datadoghq.com/monitors/330850820", "https://forge-ai.sentry.io/issues/6012345678/"
    run("incident", "open", "SandSQL error logs on plat", "--alert", monitor, "--record", RECORD, *FILED)
    run("order", "add", "--title", "Short title", *FILED, "SandSQL replication lag on plat", "--joins", "o1", "--alert", sentry)
    run("order", "set", "o1", "--alert", lag, "--alert", monitor)
    assert cli.main(["order", "set", "o1", "--alert", "https://app.datadoghq.com/dashboard/abc-123"]) == 2
    assert "--alert https://app.datadoghq.com/dashboard/abc-123: refused, name the alert by its Datadog monitor link" in capsys.readouterr().err
    orders = state.load("demo")["orders"]
    assert (orders["o1"]["alerts"], orders["o2"]["alerts"]) == ([monitor, lag], [sentry])


def test_incident_stop_gives_an_open_incident_one_running_stop_job(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", "--record", RECORD, *FILED)
    assert run("incident", "stop", "o1") == (0, f'j4 on "stop": start its agent now, its brief ending Drive: demo, order o1, job j4\n{state.INCIDENT_BRIEF}')
    data = state.load("demo")
    assert (list(data["orders"]["o1"]["parts"]), data["jobs"]["j4"]["kind"]) == (["fix", "rollback", "cause", "stop"], "codex")
    assert cli.main(["incident", "stop", "o1"]) == 2
    assert "o1: refused, its stop runs as j4; resume that job instead" in capsys.readouterr().err
    run("order", "add", "--title", "Short title", "the dashboard is slow", *FILED)
    assert cli.main(["incident", "stop", "o2"]) == 2
    assert "o2: refused, it is no open incident order" in capsys.readouterr().err


def test_an_incident_jobs_brief_carries_the_rule_its_kind_follows_on_a_choice_on_its_incident_line_once(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.reads, "run", recorded([]))
    run("incident", "open", "table moves rerun on every write", "--record", RECORD, *FILED)
    run("job", "add", "builder", "--order", "o1", "--part", "fix", "--goal", "ship the fix the codex lane built")
    where = ("--serves", "his order", "--done-when", "fixed", "--where", "Forge-AI/monorepo dev")
    run("job", "brief", "j1", *where, "--incident", "record 5f6b2b5")
    run("job", "brief", "j4", *where)
    jobs = state.load("demo")["jobs"]
    assert f"\nIncident: record 5f6b2b5; {state.INCIDENT_CHOICE['codex']}\n" in Path(jobs["j1"]["brief"]).read_text()
    assert f"\nIncident: {state.INCIDENT_CHOICE['builder']}\n" in Path(jobs["j4"]["brief"]).read_text()
    run("job", "brief", "j1", "--incident", f"record 5f6b2b5; {state.INCIDENT_CHOICE['codex']}")
    assert Path(state.load("demo")["jobs"]["j1"]["brief"]).read_text().count(state.INCIDENT_CHOICE["codex"]) == 1


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
    assert [question["card"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == [DESIGN_CARD]
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the dashboard is slow")
    run("job", "add", "builder", "--order", "o2", "--goal", "speed it up")
    assert run("job", "set", "j3", "--card", DESIGN_CARD)[0] == 0


def test_order_add_json(run: Run, demo: str) -> None:
    code, out = run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "words", "--json")
    assert code == 0
    assert json.loads(out)["id"] == "o1"


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


def test_a_drive_stored_with_milestones_loads_without_them(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "ship the fix", "--project", "Platform", "--workstream", "SandSQL")
    stored = json.loads(state.state_path("demo").read_text())
    stored["orders"]["o1"]["milestone"] = "Dashboard parity"
    state.state_path("demo").write_text(json.dumps(stored))
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
    assert state.owed_step(state.load("demo")["prs"][with_pr]) == "card"
    code, out = run("order", "set", "o2", "--rework", PR)
    assert (code, out.splitlines()[-2:]) == (0, [f"{with_pr} reworked to o2: its card stays down until its builder's READY says how his words are met", f"withdrew his card {CARDED}: {REWORKING}"])
    assert withdrawals.asked == [(PR, REWORKING)]
    pr = state.load("demo")["prs"][with_pr]
    assert (state.owed_step(pr), pr["rework"]["by"]) == ("reworking", "o2")


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
    assert state.owed_step(state.load("demo")["prs"][with_pr]) == "card"


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


def test_job_set_waits_on_his_pr_card_by_its_slack_link_or_merge_waits_id_until_he_taps_it(
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


PINNED_CHATS_ASK = explained("""ASK Where should pinned chats draw in the Iris sidebar?
A row's menu gains Pin and Unpin, and the sidebar draws pinned chats and agents ahead of unpinned ones. The choice is which spot counts as the top.
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
    for argv in (["job", "ask", "--check", str(block)], ["job", "ask", asking, str(block)]):
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


def test_job_ask_check_says_ready_or_every_fix_its_words_and_card_need_with_no_drive_and_keeps_only_its_picture_verdicts(
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
    assert [path.name for path in state_root.iterdir()] == [picture.JUDGED]
    assert cli.main(["job", "ask"]) == 2
    assert "drive job ask <j> <file>; drive job ask --check <file> only checks its shape" in capsys.readouterr().err


def test_job_ask_reads_a_readers_design_return_as_the_ask_its_first_line_asks(run: Run, asking: str, tmp_path: Path) -> None:
    block = tmp_path / "design.md"
    block.write_text(DESIGN_RESEARCH)
    assert run("job", "ask", "--check", str(block)) == (0, "ready")
    code, out = run("job", "ask", asking, str(block))
    assert (code, Path(out).read_text()) == (0, f"ASK {DESIGN_RESEARCH.removeprefix('DESIGN ')}")
    assert state.load("demo")["jobs"]["j1"]["said"] == DESIGN_TITLE


def test_job_ask_check_refuses_a_summary_answering_none_of_his_next_questions_unless_its_findings_say_none_follows(
    state_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "ask.md"
    bare = explained("ASK Which way should this go?\nTwo ways to copy.\n- server-side copy (recommended)\n- copy in batches\nFindings:\nRoot cause: none: a design pick\nKept on disk.\n")
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
        explained(
            "ASK How do leases stop leaking?\nA lease outlives the run that took it, so the next run waits on it.\n"
            "- reclaim orphaned leases every 10 minutes (recommended)\n- one owner holds the lease until it ends\n"
            "Findings:\nRoot cause: none: a design pick\nTwo runs held one lease.\n"
        )
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


TEAMMATE_FIX = explained("""ASK A teammate's PR for the missing update button landed first: keep it, add our download status to it, or replace it?
A teammate's PR landed at 2:12 PM today: every locked chat shows an Update sand button that downloads and installs in one click, even mid-download. Ours shows the button once the download finishes and says downloading until then. They conflict. Ours waits unlanded meanwhile.
- Keep theirs, close ours: the button is always there and nothing needs a terminal, but the card never says it is downloading. (recommended)
- Add our status line beside their always-on button: the card says checking, downloading or downloaded; ours is rebuilt and reviewed again.
- Replace theirs with ours: the button appears once downloaded, terminal steps when a download fails; it undoes a teammate's landed PR.
Findings:
Root cause: none: two landed designs of one button, a pick between them with no defect behind it
- Theirs merged 2:12 PM Nov 10; ours now conflicts with dev.
""")
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
    landing = explained("- Land the snapshot writer now, then backfill old runs from a pass box: opens in 3 requests\n")
    block.write_text(SHORT_ASK.replace("- A map file beside each journal file", f"{landing}- A map file beside each journal file"))
    assert run("job", "ask", asking, str(block))[0] == 0


LANDING_ASK = explained("""ASK Land your run history change so the run page change you approved can land?
<https://github.com/acme/app/pull/12|The run page change> is built on <https://github.com/acme/app/pull/11|your run history change>, which is green and approved but has no tap of yours.
I recommend landing both together, run history first; meanwhile both stay open.
- Land both now, run history first (recommended)
- Move the run page change onto dev without run history, and leave that one with you

Findings:
Root cause: none: landing two PRs, with no defect behind it
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
    for argv in (["job", "ask", "--check", str(block)], ["job", "ask", asking, str(block)], ["job", "card", str(block), "--waits", "demo:j1-7"]):
        assert cli.main(argv) == 2
        assert capsys.readouterr().err == (
            'drive: the Show me picture "1 · One snapshot file per finished run, the requests its page makes", a ```http fence,'
            " does not draw: Parse error on line 7: Expecting 'SOLID_OPEN_ARROW', got 'NEWLINE'\n"
        )
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["questions"]) == ("running", [])
    assert not list((state_root / "demo").glob("asks/*.md"))
    chrome.refusal = None
    assert run("job", "ask", "--check", str(block)) == (0, "ready")
    code, kept = run("job", "ask", asking, str(block))
    assert code == 0
    assert Path(kept).read_text() == SHORT_ASK
    assert [kind for kind, _ in chrome.drawn] == ["code:http", "code:http", "code:http", "code:http"]
    assert run("job", "card", str(block), "--waits", "demo:j1-7")[0] == 0
    assert len(chrome.drawn) == 4


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
    reordered = unasked | {"waits": "demo:hier-3", "options": unasked["options"][::-1]}
    shown = [open_card(n, card) for n, card in enumerate([*map(json.loads, printed), unasked, unanswerable, reordered])]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": shown, "pr_cards": [], "closed": []})
    jobs = state.load("demo")["jobs"]
    assert cli.main(["cards", "redraw"]) == 2
    out, err = capsys.readouterr()
    assert out.splitlines() == [*map(in_place, printed), in_place(json.dumps(unasked))]
    assert "demo:hier-2: the ASK is out of shape" in err and "0 options; an ASK has" in err
    assert "demo:hier-3: the ASK is out of shape" in err and "is not first: put it first" in err
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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, in_place(printed))
    rebuilt = state_root / "demo" / "asks" / "j1-8.md"
    assert limits.redrawn(rebuilt.read_text()).title == json.loads(printed)["title"]
    assert rebuilt.read_text().endswith("\nFindings:\n" + SHORT_ASK.split("Findings:\n", 1)[1])
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (str(rebuilt), "demo:j1-8", shown["url"])
    ]
    assert run("cards", "redraw", "--waits", "demo:j1-8") == (0, in_place(printed))


def test_job_card_refuses_a_kept_ask_edited_by_hand_until_drive_job_ask_keeps_the_new_words(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    kept = Path(run("job", "ask", asking, "-")[1])
    assert run("job", "card", str(kept), "--waits", "demo:j1-7")[0] == 0
    trimmed = SHORT_ASK.replace("GET 2 page ranges\n", "")
    kept.write_text(trimmed)
    for argv in (["job", "card", asking], ["job", "card", str(kept), "--waits", "demo:j1-7"]):
        assert cli.main(argv) == 2
        assert f"{kept} no longer holds the words drive job ask kept there, so it draws no card" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(trimmed))
    rekept = run("job", "ask", asking, "-")[1]
    assert Path(rekept).name == f"j1-{state.ask_hash(trimmed)}.md"
    assert run("job", "card", asking, "--waits", "demo:j1-7")[0] == 0


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
    closed: list[dict] = []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(shown), "pr_cards": [], "closed": list(closed)})
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
    answered = shown.pop(0)
    closed.append({"waits": "demo:j1-0", "title": titles[0], "prs": [], "asked": answered["asked"], "url": links[0], "answer": {"label": "Yes", "at": answered["asked"]}, "tapped": "", "replaced_by": ""})
    status = run("status", asking)[1]
    assert f'card {links[0]} answered "Yes" at {render.when(answered["asked"])} (demo:j1-0)' in status
    assert f"asks him: Where should a finished run's files be laid out so its page opens without…; card {links[1]} open since {render.when(shown[0]['asked'])} (demo:j1-1)" in status
    assert f"never drawn, kept in {kept[2]}" in status
    assert f"asks him: When should; card {links[2]} open since {render.when(shown[1]['asked'])} (demo:j1-2)" in status


def test_a_redrawn_card_moves_to_the_question_that_redoes_it_and_a_card_never_posted_holds_nothing(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
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
    code, out = run("job", "ask", asking, "--drop", kept[0])
    assert (code, out.endswith("a later ask of it replaced its question, so only the file is deleted, so no ask record may name it")) == (0, True)
    assert not Path(kept[0]).exists() and Path(kept[1]).exists()


def test_a_question_asked_again_after_he_answered_its_card_goes_on_a_card_of_its_own(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed = [shown]
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    listed.clear()
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "Yes", link)
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")))
    again = run("job", "ask", asking, "-")[1]
    assert json.loads(run("job", "card", again, "--waits", "demo:j1-9")[1])["waits"] == "demo:j1-9"
    assert [(question["file"], question["waits"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [
        (first, "demo:j1-7"),
        (again, "demo:j1-9"),
    ]


def test_a_question_asked_again_after_its_card_was_withdrawn_redraws_that_card_under_its_waits_id_on_any_job(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    listed, closed = [shown], []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(listed), "pr_cards": [], "closed": list(closed)})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    listed.clear()
    withdrawn = {"label": "his order replaced it", "at": state.stamp(), "withdrawn": True}
    closed.append({"waits": "demo:j1-7", "title": "How should?", "prs": [], "asked": shown["asked"], "url": link, "answer": withdrawn, "tapped": "", "replaced_by": ""})
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("withdrawn", withdrawn["label"], None)
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 3 calls")))
    again = json.loads(run("job", "ask", asking, "-", "--json")[1])
    assert again["waits"] == "demo:j1-7"
    assert json.loads(run("job", "card", again["question"], "--waits", "demo:j1-9")[1])["waits"] == "demo:j1-7"
    assert [(question["file"], question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [(again["question"], "demo:j1-7", link)]
    assert f"card {link} withdrawn at {render.when(withdrawn['at'])}: his order replaced it (demo:j1-7)" in run("status", asking)[1]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("withdrawn", withdrawn["label"], None)
    run("job", "add", "builder", "--order", "o1", "--goal", "design it again")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("opens in 3 requests", "opens in 2 calls")))
    assert json.loads(run("job", "ask", "j2", "-", "--json")[1])["waits"] == "demo:j1-7"
    assert [(question["waits"], question["card"]) for question in state.load("demo")["jobs"]["j2"]["questions"]] == [("demo:j1-7", link)]


def test_job_ask_drop_throws_away_a_near_copy_kept_with_no_card_so_nothing_counts_or_sends_it(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK))
    first = run("job", "ask", asking, "-")[1]
    run("job", "card", first, "--waits", "demo:j1-7")
    run("job", "set", asking, "--card", link)
    near_copy = "ASK How should a finished run's files be laid out so its page opens with no guessing?"
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace(SHORT_ASK.split("\n")[0], near_copy)))
    copy = run("job", "ask", asking, "-")[1]
    assert f"never drawn, kept in {copy}" in run("status", asking)[1]
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
    assert settled.ask_refusal(["demo"], {"ask": copy, "job": "j1"}, near_copy).startswith(f"refused: {copy} keeps no question of j1")
    assert json.loads(run("job", "card", asking)[1])["title"] == job["said"]


def test_job_ask_refuses_a_question_another_jobs_open_card_asks_naming_the_wait_on_that_card(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(picture, "visual_png", lambda kind, source, out: out.write_bytes(b"\x89PNG\r\n"))
    link = "https://acme.slack.com/archives/D123/p1794300000000100"
    shown = {"question": "How should?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(), "url": link, "card": {"title": "How should?", "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
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
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
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
    jev.settles("a2")
    monkeypatch.setattr("sys.stdin", io.StringIO(SHORT_ASK.replace("ASK How should", "ASK When should")))
    code, settled_now = run("job", "ask", asking, "-")
    assert code == 0
    assert ' keeps j1\'s question settled, no card: his recorded answer a2 already decides this question. Asked "' in settled_now
    assert settled_now.endswith('he said "One snapshot file". Go ahead on it. If his words decide something else, ask again with --unsettled "a2: <why>". j1 builds it now')
    assert sorted((answer["id"], answer["said"]) for request in jev.asked[-2:] for answer in request["state"]["answers"]) == [
        ("a1", "its one snapshot file, just wait for the copy"),
        ("a2", "One snapshot file"),
    ]
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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": list(asking_him), "pr_cards": [], "closed": []})
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


def test_job_ask_hands_jev_his_messages_in_its_orders_thread_and_refuses_the_one_it_reads_as_deciding_until_unsettled_names_its_permalink(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    moment = state.now()
    settles = slack_thread.post(moment, from_user=True, text="keep one snapshot file per finished run, no guessing")
    slack_thread.post(moment + timedelta(seconds=1), from_user=True, text="ok go ahead")
    slack_thread.post(moment + timedelta(seconds=2), from_claude=True, text="building one snapshot file per finished run")
    key = re.search(r"/(p\d+)\?", settles)[1]
    jev.settles(key)
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    code, settled_now = run("job", "ask", "j1", str(block))
    assert code == 0
    assert (
        f'his message in this order\'s thread {settles} already decides this question. Asked "design the run page",'
        ' he said "keep one snapshot file per finished run, no guessing".'
    ) in settled_now
    assert [(question["closed"]["how"], question["closed"]["answer"], question["closed"]["ruling"]) for question in state.load("demo")["jobs"]["j1"]["questions"]] == [("answered", key, None)]
    assert [(answer["id"], answer["asked"]) for answer in jev.asked[0]["state"]["answers"]] == [(key, "design the run page")]
    assert ("--channel", "D123", "--thread", "1794200000.000100", "--limit", "1000") in slack_thread.reads
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    why = f"{settles}: he picked the file layout, not where its map sits"
    assert run("job", "ask", "j1", str(block), "--unsettled", why)[0] == 0


def test_job_ask_refuses_a_question_offering_no_tap_for_an_amount_his_words_in_its_orders_thread_name(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    words = "You didn't give me a tap for keeping each finished snapshot 30 days, how many times do I have to say this"
    his = slack_thread.post(state.now(), from_user=True, text=words)
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    why = f"{his}: he set how long a snapshot is kept, not its layout"
    assert cli.main(["job", "ask", "j1", str(block), "--unsettled", why]) == 2
    refusal = capsys.readouterr().err
    assert "refused: his words on this order name an amount this question offers no tap for" in refusal
    assert f'- 30 day: {his} "{words}"' in refusal.splitlines()
    block.write_text(SHORT_ASK.replace("written when it ends, old runs", "written when it ends, kept 30 days, old runs"))
    assert cli.main(["job", "ask", "j1", str(block), "--unsettled", why]) == 0


def test_job_ask_counts_an_amount_of_his_only_in_a_clause_sharing_the_questions_words(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    words = "keep one snapshot file per finished run, ok if we're capped at under 2gb that feels ok right, also look at that 21GB db and why its so big"
    his = slack_thread.post(state.now(), from_user=True, text=words)
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("files be laid out", "files be capped and laid out"))
    why = f"{his}: he set one snapshot file and its cap, not how the page opens it"
    assert cli.main(["job", "ask", "j1", str(block), "--unsettled", why]) == 2
    assert [line for line in capsys.readouterr().err.splitlines() if line.startswith("- ")] == [f'- 2 gb: {his} "{words}"']
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "j1", str(block), "--unsettled", why]) == 0


def test_job_ask_counts_an_amount_only_on_the_order_his_words_are_about_never_another_order_sharing_its_thread(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    words = "ok so for 1, lrts mske eveyhting standard on signal or 30 seconds both for 2 lfor mac and cloud each decide wherther `deliverSteers` or turn boundary is best"
    slack_thread.post(state.now() - timedelta(minutes=1), from_user=True, text=words)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", f'any harm in making it uniform? Then, 5:06 AM, same thread: "{words}"', "--thread", "1794200000.000100")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "an answer he reads states only what was read in code or measured", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "pick when the inbox is read")
    run("job", "add", "builder", "--order", "o2", "--goal", "check what a post read")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK.replace("so the browser guesses", "so the cloud reader guesses"))
    assert cli.main(["job", "ask", "j1", str(block)]) == 2
    assert [line.split(' "')[0] for line in capsys.readouterr().err.splitlines() if line.startswith("- ")] == ["- 30 second: o1's own words"]
    assert cli.main(["job", "ask", "j2", str(block)]) == 0


def test_job_ask_counts_only_his_quoted_words_in_an_order_the_drive_wrote(
    run: Run, meta: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    words = 'Drive tooling: the finished snapshot page waited 10 minutes for its card, and he ruled "keep each finished snapshot 30 days"'
    run("order", "add", "--title", "Short title", words)
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "j1", str(block)]) == 2
    assert [line.split(' "')[0] for line in capsys.readouterr().err.splitlines() if line.startswith("- ")] == ["- 30 day: o1's own words"]
    block.write_text(SHORT_ASK.replace("written when it ends, old runs", "written when it ends, kept 30 days, old runs"))
    assert cli.main(["job", "ask", "j1", str(block)]) == 0


def test_job_ask_prints_the_answer_jev_reads_as_deciding_and_an_amount_he_named_in_one_refusal(
    run: Run, demo: str, slack_thread: Thread, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], recorded: Answers, jev: Jev
) -> None:
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS | {"team_url": "https://acme.slack.com/"})
    view.refresh_cards("demo")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page", "--thread", "1794200000.000100")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    recorded.found = [FLAT_RULING]
    his = slack_thread.post(state.now(), from_user=True, text="keep one snapshot file per finished run for 30 days")
    jev.settles("ac61348")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    assert cli.main(["job", "ask", "j1", str(block)]) == 2
    refusal = capsys.readouterr().err.removeprefix("drive: ")
    assert [line.split(".")[0] for line in refusal.splitlines() if line.startswith("refused: ")] == [
        "refused: his recorded answer ac61348 already decides this question",
        "refused: his words on this order name an amount this question offers no tap for",
    ]
    assert f"- 30 day: {his}" in refusal
    block.write_text(SHORT_ASK.replace("written when it ends, old runs", "written when it ends, kept 30 days, old runs"))
    assert cli.main(["job", "ask", "j1", str(block), "--unsettled", "ac61348: it lays out the files, not how long they stay"]) == 0
    assert len(jev.asked) == 2


def test_an_amount_names_a_unit_never_a_word_the_count_check_skips() -> None:
    assert settled.amounts("step 2 was skipped, keep 3 this week, 1 its own, 30-day bonding under 2gb") == {"30 day", "2 gb"}


FLAT_RULING = {
    "id": "ac6134849ff8a392c9f26294d4303635d1c27a58",
    "title": "Owner Nov 9: lay out every finished run's files flat, without asking\nsecond line",
    "body": "Lay every finished run's files out flat, one snapshot file each.",
    "tags": ["scope:durable"],
}
PAGE_RULING = {
    "id": "33eaad946c1f36dc438a3a04d443eb7d71573815",
    "title": "Where do a finished run's files go once its page opens?",
    "body": "Beside the run, in its own folder.",
    "tags": ["scope:durable"],
}


def test_job_ask_settles_a_question_jev_reads_his_recorded_answer_as_deciding_so_its_job_builds_on_his_words_with_no_card(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers, jev: Jev, settled_notes: list[tuple[str, str]]
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [FLAT_RULING]
    jev.settles("ac61348")
    kept = state.drive_dir("demo") / "asks" / f"j1-{state.ask_hash(SHORT_ASK)}.md"
    assert run("job", "ask", asking, str(block)) == (
        0,
        f"{kept} keeps j1's question settled, no card: his recorded answer ac61348 already decides this question. Asked \"Owner Nov 9: lay out every"
        " finished run's files flat, without asking second line\", he said \"Lay every finished run's files out flat, one snapshot file each.\". Go ahead"
        ' on it. If his words decide something else, ask again with --unsettled "ac61348: <why>". j1 builds it now',
    )
    assert recorded.asked[0].startswith("How should a finished run's files be laid out")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], kept.read_text(), [(question["file"], question["refused"], question["closed"]["ruling"]) for question in job["questions"]]) == (
        "running",
        SHORT_ASK,
        [(str(kept), None, "ac61348")],
    )
    assert settled_notes == [("j1", "ac61348")]
    assert "refused, so no card draws" not in run("status", asking)[1]
    assert cli.main(["job", "ask", asking, str(block), "--unsettled", "it lays out files, not how its page opens them"]) == 2
    assert 'ask again with --unsettled "ac61348: <why>"' in capsys.readouterr().err
    why = "ac61348: it lays out the files; this asks how the page opens them"
    assert run("job", "ask", asking, str(block), "--unsettled", why)[0] == 0
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], [(question["unsettled"], question["refused"]) for question in job["questions"]][-1]) == ("waiting", (why, None))
    assert len(jev.asked) == 1


def test_a_settled_question_tells_the_session_holding_its_order_in_one_record_that_wakes_nobody(
    run: Run, asking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["#9"])
    SETTLED_NOTE("demo", state.load("demo"), asking, "How should a finished run's files be laid out?", {"id": "ac61348"})
    assert sent == [
        (
            "builder",
            [
                {
                    "kind": "evidence",
                    "to": ["root@demo"],
                    "attr": {"job": "j1", "answer": "ac61348"},
                    "text": 'j1 builds on his answer ac61348, which already decides its question "How should a finished run\'s files be laid out?", so no card draws for it',
                }
            ],
        )
    ]


BLOB_STORE_RULING = {
    "id": "f061aac4ba019956eb3e1fcdfafa152600cad4f0",
    "title": "Owner 8:14 PM Nov 10: where do a new-tree run's files go, and what do files and values blobs build on?",
    "body": "Each journal file sits in the team's bucket as LTX, and a finished run's page reads them there.",
    "tags": ["scope:durable", "drive:demo"],
}


def test_job_ask_hands_jev_the_rulings_of_its_drive_nearest_to_its_whole_block_and_refuses_the_one_it_reads_as_deciding(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers, jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [FLAT_RULING]
    recorded.nearest = [BLOB_STORE_RULING, FLAT_RULING]
    jev.settles("f061aac")
    code, settled_now = run("job", "ask", asking, str(block))
    assert code == 0
    assert recorded.near == [(settled.read(SHORT_ASK), ("--nearest", "--label", "drive:demo"))]
    assert "LTX files" in recorded.near[0][0]
    assert sorted(answer["id"] for request in jev.asked for answer in request["state"]["answers"]) == ["ac61348", "f061aac"]
    assert "settled, no card: his recorded answer f061aac already decides this question." in settled_now
    why = "f061aac: it says where a run's files go, not how they are laid out"
    assert run("job", "ask", asking, str(block), "--unsettled", why)[0] == 0


def test_job_ask_sends_the_question_to_him_when_jev_picks_none_is_less_sure_than_eight_in_ten_or_gives_no_answer(
    run: Run, asking: str, tmp_path: Path, recorded: Answers, jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    recorded.found = [FLAT_RULING]
    for title, read, answer in (("How should", {}, Jev().answer), ("Where should", {"ac61348": {"none": 0.21, "picks": 0.4, "orders": 0.39}}, Jev().answer), ("When should", {}, None)):
        jev.reads, jev.answer = read, answer
        block.write_text(SHORT_ASK.replace("ASK How should", f"ASK {title}"))
        assert run("job", "ask", asking, str(block))[0] == 0
    assert [question["refused"] for question in state.load("demo")["jobs"]["j1"]["questions"]] == [None, None, None]
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"


def test_job_ask_asks_jev_again_only_when_a_reworded_asks_title_changes(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers, jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [FLAT_RULING]
    jev.settles("ac61348")
    assert "his recorded answer ac61348 already decides this question" in run("job", "ask", asking, str(block))[1]
    recorded.found = [PAGE_RULING]
    jev.settles("33eaad9")
    where = SHORT_ASK.replace("ASK How should", "ASK Where should")
    block.write_text(where)
    assert cli.main(["job", "ask", asking, str(block), "--unsettled", "ac61348: it lays out the files; this asks where they go"]) == 2
    assert "his recorded answer 33eaad9 already decides this question" in capsys.readouterr().err
    jev.reads = {}
    block.write_text(where.replace("about 180 small files", "180 files"))
    why = "33eaad9: where the files go once the page opens, not where they are laid out"
    assert run("job", "ask", asking, str(block), "--unsettled", why)[0] == 0
    assert len(jev.asked) == 2
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], [(question["unsettled"], question["refused"]) for question in job["questions"]], job["may_settle"]) == ("waiting", [(None, None), (why, None)], None)


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
    assert recorded.cwds == [checkout, checkout]


def test_job_ask_reads_a_remote_lanes_report_when_given_no_file(run: Run, asking: str, state_root: Path) -> None:
    assert run("job", "ask", asking)[0] == 2
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(where="remote", report=SHORT_ASK)
    assert run("job", "ask", asking) == (0, str(state_root / "demo" / "asks" / f"j1-{hashlib.sha256(SHORT_ASK.encode()).hexdigest()[:8]}.md"))


def test_job_report_keeps_a_jobs_whole_return_from_stdin_and_prints_it_back(
    run: Run, demo: str, state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    kept_proof(tmp_path, "j1")
    assert run("job", "report", "j1")[0] == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("READY https://github.com/yasyf/cc-skills/pull/1\nSERVES: his order\nDESIGN: none\n"))
    assert run("job", "report", "j1", "-") == (0, str(state_root / "demo" / "jobs" / "j1.replies.jsonl"))
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
    assert run("job", "report", "j1") == (0, f"{restated}\n{earlier[1]}\n{agents}\n{earlier[0]}\n{blocked}".rstrip("\n"))
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
    assert run("job", "report", "j2") == (0, f"DONE the lane's latest\n\n{earlier}\nDONE graded two PRs\nCHECK: the diffs")


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
    assert "o2 is a build order, so its builder starts only once an Opus reader has kept a Researched: line" in capsys.readouterr().err
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
    assert "o1 is a build order, so its design starts only once an Opus reader has kept a Researched: line" in capsys.readouterr().err
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")[0] == 0


def test_a_build_orders_designer_and_builder_are_added_waiting_on_its_reader_still_at_work(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: topologies", "--kind", "build",
        "--parts", "answer", "control freak", "design options",
    )
    run("job", "add", "reader", "--order", "o1", "--part", "answer", "--goal", "research: topologies")
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "design options", "--goal", "cards"]) == 2
    assert "while j1 reads, add this design now waiting on it with --waits j1" in capsys.readouterr().err
    added = run("job", "add", "design", "--order", "o1", "--part", "design options", "--goal", "cards", "--waits", "j1")
    assert added == (0, "j2\nj2 waits on j1: start no agent for it until the watch's FREE line says its last wait ended")
    assert run("job", "add", "builder", "--order", "o1", "--part", "control freak", "--goal", "build it", "--waits", "j1")[0] == 0
    jobs = state.load("demo")["jobs"]
    assert [(jobs[job_id]["state"], jobs[job_id]["waits"], jobs[job_id]["agent"]) for job_id in ("j2", "j3")] == [("waiting", ["j1"], None)] * 2
    refusals = {
        "--waits j2: names no reader of o1 still at work; wait on j1": ["builder", "--part", "control freak", "--waits", "j2"],
        "only a build order's designer, builder or codex lane is added waiting": ["reader", "--part", "answer", "--waits", "j1"],
        "--waits records a job whose agent starts on the watch's FREE line": ["builder", "--part", "control freak", "--waits", "j1", "--agent", "a"],
    }
    for refusal, words in refusals.items():
        assert cli.main(["job", "add", *words, "--order", "o1", "--goal", "x"]) == 2
        assert refusal in capsys.readouterr().err
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert cli.main(["job", "add", "builder", "--order", "o1", "--part", "control freak", "--goal", "x", "--waits", "j1"]) == 2
    assert "--waits j1: j1 already kept o1's research, so this builder starts now: add it with no --waits" in capsys.readouterr().err


def test_a_builder_is_added_waiting_on_another_builders_change_until_that_builder_opens_its_pr(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built = "Forge-AI/monorepo#7"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", RESEARCH)
    assert run("job", "add", "builder", "--order", "o1", "--goal", "copy the store")[0] == 0
    code, out = run("job", "add", "builder", "--order", "o1", "--goal", "read the copy", "--waits", "j2")
    assert (code, out.splitlines()[:2]) == (0, ["j3", "j3 waits on j2: start no agent for it until the watch's FREE line says its last wait ended"])
    assert run("pr", "add", state.pr_url(built), "--job", "j2")[0] == 0
    assert state.load("demo")["jobs"]["j3"]["waits"] == [built]
    assert run("job", "add", "builder", "--order", "o1", "--goal", "dropped")[0] == 0
    assert run("job", "set", "j4", "--state", "stopped")[0] == 0
    refusals = {
        "j2": f"--waits j2: j2 already opened {built}, so wait on the PR this job builds on, as --waits {built}",
        "j4": "--waits j4: j4 is stopped with no PR, so no change of it is coming to wait on",
    }
    for waits, refusal in refusals.items():
        assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "x", "--waits", waits]) == 2
        assert capsys.readouterr().err.strip().endswith(f"refused, no job added: {refusal}")


def test_a_jobs_clock_is_a_time_to_come(run: Run, meta: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store")
    run("job", "add", "codex", "--order", "o1", "--goal", "first check")
    assert cli.main(["job", "set", "j1", "--due", "2026-01-01T00:00:00+00:00"]) == 2
    assert "j1: refused, no clock set:" in capsys.readouterr().err
    assert state.load("demo")["jobs"]["j1"]["due"] is None
    assert run("job", "set", "j1", "--due", "+10m")[0] == 0
    assert timedelta(minutes=9) < state.parse_time(state.load("demo")["jobs"]["j1"]["due"]) - state.now() <= timedelta(minutes=10)


def test_a_readers_design_holds_its_builder_until_its_kept_question_is_on_his_card_or_settled_with_no_design_job(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    one_option = DESIGN_RESEARCH.replace(SECOND_OPTION, "")
    for refused in (one_option, DESIGN_RESEARCH.replace("(recommended)", "")):
        code, err = keep(monkeypatch, capsys, "j1", refused)
        assert (code, "j1: refused, nothing kept: a reader's DESIGN return is a choice for him" in err) == (2, True)
    assert run("job", "report", "j1")[0] == 2
    assert keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH) == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "done"
    asked = str(state.drive_dir("demo") / "asks" / "j1-1377896a.md")
    kept = state.new_question(asked, "Where does the copy run?", state.stamp())
    answered = state.new_question(asked, "Where does the copy run?", state.stamp(), card="https://acme.slack.com/archives/D123/p1794480049653959")
    jevs = state.new_question(asked, "Where does the copy run?", state.stamp(), refused="refused: his recorded answer 32164d3 already decides this question.")
    others = state.new_question(asked, "Where does the copy run?", state.stamp(), refused="refused: his words on this order name an amount this question offers no tap for.")
    replays = {
        "no question kept": ([], "j1 returned DESIGN, a choice for him its research found, and keeps it as no question of his: drive job ask j1"),
        "j1656 before his ruling": ([kept], f'j1 returned DESIGN, and its question "Where does the copy run?" kept in {asked} is on no card of his yet'),
        "refused by an amount": ([others], "j1 returned DESIGN, and drive job ask refused its question \"Where does the copy run?\" kept in"),
        "j1654, his card up": ([kept | {"waits": "demo:j1-671973", "card": answered["card"]}], None),
        "j1651, his card answered": ([answered], None),
        "j1656, settled by his ruling": ([kept | {"closed": state.new_close("answered", None, None, seq="671985", ruling="32164d3")}], None),
        "decided by his answer": ([jevs | {"closed": state.new_close("answered", None, None, answer="32164d3")}], None),
    }
    for case, (questions, refusal) in replays.items():
        with state.edit("demo") as data:
            data["jobs"]["j1"]["questions"] = questions
        code = cli.main(["job", "add", "builder", "--order", "o1", "--goal", "build it"])
        assert (case, code, refusal and refusal in capsys.readouterr().err) == (case, 2 if refusal else 0, refusal and True), case
    assert not [job for job in state.load("demo")["jobs"].values() if job["kind"] == "design"]
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = []
    assert run("job", "add", "design", "--order", "o1", "--goal", "cards: where the copy runs")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--goal", "build it")[0] == 0


def test_his_ruling_settling_a_readers_ask_before_its_card_draws_is_kept_on_its_question_and_frees_its_builder(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH)
    asked = str(state.drive_dir("demo") / "asks" / "j1-1377896a.md")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(asked, "Where does the copy run?", state.stamp())]
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "build it"]) == 2
    assert "is on no card of his yet" in capsys.readouterr().err
    posted: list[dict] = []
    real = subprocess.run

    def cci(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["cci", "show"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"kind": "ask", "fields": {"attrs": {"ask": asked, "job": "j1"}}}), "")
        if argv[:2] == ["cci", "post"]:
            posted.extend(json.loads(str(kwargs["input"])))
            return subprocess.CompletedProcess(argv, 0, "671985\n", "")
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
    assert (len(posted), posted[0]["re"], posted[0]["resolves"], posted[0]["attr"]) == (1, ["671975"], ["671975"], {"job": "j1", "ruling": "32164d3"})
    job = state.load("demo")["jobs"]["j1"]
    assert (view.open_questions(job), job["questions"][0]["closed"]["ruling"]) == ([], "32164d3")
    assert run("job", "add", "builder", "--order", "o1", "--goal", "build it")[0] == 0


def test_the_immediate_fix_starts_at_once_past_the_research_the_design_and_options_only(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build",
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


def test_a_follow_up_he_orders_to_a_pr_gets_its_designer_at_once_with_no_reader_or_wait(run: Run, meta: str, capsys: pytest.CaptureFixture[str]) -> None:
    run(
        "order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: land the card fix, then a follow-up that is not hacky",
        "--kind", "build", "--parts", "the card fix", "follow-up",
    )
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "the card fix", "--goal", "design it"]) == 2
    capsys.readouterr()
    assert run("job", "add", "reader", "--order", "o1", "--part", "the card fix", "--goal", "research: the card fix") == (0, "j1")
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "follow-up", "--waits", "j1", "--goal", "design the follow-up"]) == 2
    assert 'a "follow-up" design starts now, beside landing the PR it follows' in capsys.readouterr().err
    assert cli.main(["job", "add", "design", "--order", "o1", "--part", "follow-up", "--part", "the card fix", "--goal", "both"]) == 2
    assert 'the "follow-up" part is a job of its own: its design starts at once' in capsys.readouterr().err
    assert run("job", "add", "design", "--order", "o1", "--part", "follow-up", "--goal", "design the follow-up") == (0, "j2")


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
    run("incident", "open", "SandSQL mint endpoint failing", "--record", RECORD, *FILED)
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
    assert (code, "its FIX line names o9, no order or PR the drive tracks" in err) == (2, True)
    assert keep(monkeypatch, capsys, "j3", traced + "FIX o2\n") == (0, "")
    orders = state.load("demo")["orders"]
    assert [(orders[order_id]["joins"], state.hurts_now(orders[order_id])) for order_id in ("o2", "o3")] == [("o1", True), (None, False)]
    assert state.incident_orders(state.load("demo"), "o1") == ["o1", "o2"]


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


def test_a_readers_design_report_keeps_its_ask_in_the_same_call_or_keeps_nothing(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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
    assert out == f"{state.replies_path('demo', 'j1')}\nits ASK is kept in {question['file']}, the path its ask record names"
    assert Path(question["file"]).read_text().startswith(f"ASK {DESIGN_TITLE}\n")


def test_a_readers_design_report_after_its_own_ask_keeps_that_asks_unsettled_and_writes_its_report(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch
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


def test_a_design_lanes_design_names_on_each_fork_line_a_question_it_kept(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    unkept = state.drive_dir("demo") / "asks" / "j1-0123abcd.md"
    code, err = keep(monkeypatch, capsys, "j1", DESIGNED.replace("Fork: none: one layout fits", f"Fork: {unkept}"))
    assert (code, f"j1: refused, nothing kept: its Fork: line names {unkept}, which keeps no question of j1 his card can draw" in err) == (2, True)
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    block = tmp_path / "fork.md"
    block.write_text(SHORT_ASK)
    asked = run("job", "ask", "j1", str(block))[1]
    assert keep(monkeypatch, capsys, "j1", DESIGNED.replace("Fork: none: one layout fits", f"Fork: {asked}")) == (0, "")
    assert state.load("demo")["jobs"]["j1"]["state"] == "waiting"


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
        " a Change: line, a Fork: line, a Facts: line"
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


@pytest.mark.parametrize(
    ("exists", "refusal"),
    [
        ("none: nothing like it", 'its Exists: line "none: nothing like it" names no search that found nothing'),
        ("supervisor/copies.rs:40 store_copy_at copies one store", 'its Exists: entry "supervisor/copies.rs:40 store_copy_at copies one store" does not cite'),
        ("store_copy_at copies one store, extended by change 1", 'its Exists: entry "store_copy_at copies one store, extended by change 1" does not cite'),
        ("supervisor/copies.rs:40 store_copy_at, extended by change 2", 'its Exists: entry "supervisor/copies.rs:40 store_copy_at, extended by change 2" does not cite'),
        ("supervisor/copies.rs:40 store_copy_at, extended by change 1; lib/drive/state.py:18 the store list, reused by change 1", None),
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
    code, err = keep(monkeypatch, capsys, "j2", DESIGNED)
    assert code == 2
    assert "its Exists: line says none, but j1's research found what already does part of this: \"supervisor/copies.rs store_copy_at copies one store\"" in err
    owned = DESIGNED.replace('none: ccx code search "store copy" and rg store_copy found nothing', "supervisor/copies.rs:40 store_copy_at, extended by change 1")
    assert keep(monkeypatch, capsys, "j2", owned) == (0, "")


def test_a_design_of_several_changes_makes_each_a_part_of_its_order_waiting_on_each_earlier_change_its_line_names(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store", "--parts", "copy one store", "the reads through it")
    run("job", "add", "design", "--order", "o1", "--part", "copy one store", "--goal", "cards: the store copy")
    code, err = keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES.replace("(waits on change 2)", "(waits on change 1 and change 3)"))
    assert code == 2
    assert 'its Change: line "Change: the writes through it (waits on change 1 and change 3)" waits on a change its list does not hold' in err
    assert keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES.replace("(waits on change 2)", "(waits on change 1 and change 2 live on every cluster)")) == (0, "")
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert {part: (held["design"], held["after"]) for part, held in parts.items()} == {
        "copy one store": (None, []),
        "the reads through it": ("j1", []),
        "the moved-table interface": ("j1", []),
        "the writes through it": ("j1", ["the moved-table interface", "the reads through it"]),
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
    assert run("job", "add", "builder", "--order", "o1", "--part", "the reads through ssql-blobstore", "--goal", "build the reads")[1] == "j2"


def test_order_set_parts_keeps_a_designs_changes_and_refuses_a_list_renaming_one_no_builder_delivered(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    assert keep(monkeypatch, capsys, "j1", DESIGNED_IN_CHANGES) == (0, "")
    designed = state.load("demo")["orders"]["o1"]["parts"]
    names = list(designed)
    assert cli.main(["order", "set", "o1", "--parts", "copy one store", "the interface", "the reads through it", "the writes through it"]) == 2
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
        " rulings:, exists: and open: lines, each filled or none, which its builder's brief names; it lacks rulings:, exists:, open:"
    ) in err
    assert keep(monkeypatch, capsys, "j1", RESEARCH.replace("open: none\n", "open:\n"))[1].endswith("it lacks open:\n")
    assert keep(monkeypatch, capsys, "j1", RESEARCH) == (0, "")


def test_a_readers_design_starts_from_the_root_cause_and_counts_only_its_options(
    run: Run, meta: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: copy one store", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the copy")
    code, err = keep(monkeypatch, capsys, "j1", DESIGN_RESEARCH.replace(ROOT_CAUSE, ""))
    assert code == 2
    assert "j1: refused, nothing kept: a reader's DESIGN starts from the root cause and recommends the option that removes it: no `Root cause:` line" in err
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
    assert keep(monkeypatch, capsys, "j2", f'DONE limits.py and cli.py never set "hints" (0 grep hits)\n{check}') == (0, "")
    for evidence in (
        f'DEFECT: ccx vcs gh refuses every read on this Sprite: "{refusal}"',
        f'Measured: stack-enqueue read the queue under its "GitHub rate limit floor" of 500 calls, then its `1:20 PM` read was refused: "{refusal}"',
        f'Measured: gh pr view was refused 3 times between `1:10 PM` and `1:24 PM`, each "{refusal}"',
    ):
        assert keep(monkeypatch, capsys, "j2", f"DONE the copy finished\n{check}{evidence}\n") == (0, "")


def test_a_job_report_typing_when_past_work_happened_is_refused_naming_the_jobs_stamp(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy the bucket")
    run("job", "add", "builder", "--order", "o1", "--goal", "copy it")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["created"] = state.stamp(state.now().replace(hour=4, minute=50, second=21, microsecond=0))
    check = "CHECK: https://github.com/yasyf/cc-skills/actions/runs/12\n"
    code, err = keep(monkeypatch, capsys, "j1", f"DONE j1 started at 4:55 AM and the copy finished\n{check}")
    assert code == 2
    assert 'j1: refused, nothing kept: "4:55 AM" is a time typed for work already done' in err
    assert "j1 was created at 4:50:21 AM, which {started:j1} says" in err
    assert state.load(demo)["jobs"]["j1"]["state"] == "running"
    assert keep(monkeypatch, capsys, "j1", f"DONE j1 started at {{started:j1}} and the copy finished\n{check}") == (0, "")


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
    read |= {"title": "Fix: land it", "author": "yasyf", "labels": [], "native": [], "required_checks": ["buildkite/test"]}
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


def test_a_change_against_his_words_is_refused_at_its_ready_and_its_serves_and_a_head_checked_once_is_not_judged_again(
    run: Run, with_pr: str, on_github: dict, judge: Judge, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    judge.against = [{"order": "o1", "words": "land it", "change": "api/run.ts keeps the staff flag"}]
    refused = f'{with_pr} go against his words, judged from each PR\'s title and diff: o1 "land it": api/run.ts keeps the staff flag. Change the PRs'
    code, err = keep(monkeypatch, capsys, "j1", f"READY {PR}\n")
    assert (code, err.startswith(f"drive: j1: refused, nothing kept: {refused}")) == (2, True)
    assert cli.main(["pr", "set", PR, "--serves", "o1", "--design", "none"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"drive: {with_pr}: refused, nothing set: {refused}")
    assert err.rstrip().endswith("Send it back by a fresh builder on j1 briefed with this refusal")
    pr = state.load("demo")["prs"][with_pr]
    assert (pr.get("serves"), pr["checked"], state.load("demo")["jobs"]["j1"]["state"]) == (None, None, "running")
    assert 'o1, ' in judge.asked[0][0] and '"land it"' in judge.asked[0][0]
    judge.against = []
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\n") == (0, "")
    assert state.load("demo")["prs"][with_pr]["checked"]["orders"] == ["o1"]
    assert run("pr", "set", PR, "--serves", "o1", "--design", "none")[0] == 0
    assert len(judge.asked) == 3
    with state.edit("demo") as data:
        data["prs"][with_pr]["head"] = "ccccccc333"
    assert run("pr", "set", PR, "--serves", "o1", "--design", "none")[0] == 0
    assert (len(judge.asked), state.load("demo")["prs"][with_pr]["checked"]["head"]) == (4, "ccccccc333")


DOORS = {
    "id": "953a8c8f00",
    "title": "Should Directory::get stop returning records now, or once Env::file_of reads env_id?",
    "body": "Close every door once Env::file_of reads env_id (recommended)\nOptions: Once Env::file_of reads env_id | Now\nNotes: the coordinator's reading, never his",
    "tags": [],
    "created_at": "2026-11-12T11:33:00Z",
}


def test_a_pr_of_a_two_part_job_naming_one_part_is_judged_against_that_part_and_the_answers_its_design_cites(
    run: Run, demo: str, judge: Judge, recorded: Answers, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "one abstraction everything goes through", "--parts", "moved-table abstraction", "doors close", "partitioning")
    run("job", "add", "builder", "--order", "o1", "--part", "moved-table abstraction", "--part", "doors close", "--goal", "route every caller through Env")
    run("pr", "add", PR, "--job", "j1")
    run("pr", "add", STACKED, "--job", "j1")
    recorded.shown["953a8c8"] = DOORS
    assert cli.main(["pr", "set", PR, "--part", "partitioning"]) == 2
    assert 'its job j1 delivers no part partitioning; its parts are "moved-table abstraction", "doors close"' in capsys.readouterr().err
    code, out = run("pr", "set", PR, "--tap", "--serves", "o1", "--design", "asked:953a8c8", "--part", "moved-table abstraction")
    assert (code, out.rstrip().endswith("delivers moved-table abstraction")) == (0, True)
    said, changes = judge.asked[-1]
    assert list(changes) == ["Forge-AI/monorepo#42", "Forge-AI/monorepo#43"]
    assert "this change delivers: moved-table abstraction\n  other changes deliver: doors close; partitioning\n" in said
    answered = said.rpartition("his answers the PRs cite for the design they make:\n")[2]
    assert answered.startswith("953a8c8, ") and answered.endswith(f'asked "{DOORS["title"]}": "Close every door once Env::file_of reads env_id (recommended)\nOptions: Once Env::file_of reads env_id | Now"')
    assert "the coordinator's reading" not in said
    pr = state.load(demo)["prs"]["Forge-AI/monorepo#42"]
    assert (pr["parts"], pr["design"], pr["checked"]["orders"]) == (["moved-table abstraction"], "asked:953a8c8", ["o1"])


def test_a_ready_is_judged_against_the_answers_its_design_line_names(
    run: Run, with_pr: str, on_github: dict, judge: Judge, recorded: Answers, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded.shown["953a8c8"] = DOORS
    assert keep(monkeypatch, capsys, "j1", f"READY {PR}\nDESIGN: the doors stay open until Env::file_of reads env_id, by 953a8c8\n") == (0, "")
    said = judge.asked[-1][0]
    assert said.startswith('o1, ') and f'asked "{DOORS["title"]}": "Close every door once Env::file_of reads env_id (recommended)' in said


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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", kind, "--order", "o1", "--goal", "ship")
    kept_proof(tmp_path, "j1")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship again")
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


def test_a_report_takes_a_waiting_or_built_job_and_is_refused_on_a_stopped_one_keeping_nothing(
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


def test_a_waiting_line_names_what_frees_it_and_each_pr_and_release_it_names_is_a_wait(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    run("pr", "add", PR, "--job", "j1")
    code, err = keep(monkeypatch, capsys, "j1", "WAITING release: #54575 merged after the newest release was cut\n")
    assert (code, "j1: refused, nothing kept: its WAITING names nothing the watch frees it on: name the release as release:<service>" in err) == (2, True)
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"
    assert keep(monkeypatch, capsys, "j1", "WAITING release:api:Forge-AI/monorepo#42 and release:api:Forge-AI/monorepo#9\n") == (0, "")
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["waits"]) == ("waiting", ["release:api:Forge-AI/monorepo#42"])


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


def test_a_design_job_ends_once_a_builder_takes_its_part_or_one_of_its_changes(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "copy one store")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store copy")
    assert keep(monkeypatch, capsys, "j1", DESIGNED) == (0, "")
    assert cli.main(["job", "set", "j1", "--state", "done"]) == 2
    assert "drive: j1: refused, nothing changed: --state sets only stopped" in capsys.readouterr().err
    run("job", "add", "builder", "--order", "o1", "--goal", "build the copy")
    run("job", "add", "design", "--order", "o1", "--goal", "cards: the store list")
    assert keep(monkeypatch, capsys, "j3", DESIGNED) == (0, "")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "move the tables", "--parts", "move the tables", "the old copy")
    run("job", "add", "design", "--order", "o2", "--part", "move the tables", "--goal", "cards: the moved tables")
    run("job", "add", "builder", "--order", "o2", "--part", "the old copy", "--goal", "drop the old copy")
    assert keep(monkeypatch, capsys, "j4", DESIGNED_IN_CHANGES) == (0, "")
    assert state.load("demo")["jobs"]["j4"]["state"] == "waiting"
    run("job", "set", "j5", "--part", "the reads through it")
    jobs = state.load("demo")["jobs"]
    assert {job_id: (jobs[job_id]["state"], jobs[job_id]["said"]) for job_id in ("j1", "j3", "j4")} == {
        "j1": ("done", "carried by builder j2"),
        "j3": ("done", "carried by builder j2"),
        "j4": ("done", "carried by builder j5"),
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
    assert run("job", "report", "j1") == (0, f"DONE installed 1.0.76\nCHECK: drive --version\n\n{earlier}\nDONE installed 1.0.75\nCHECK: drive --version")


@pytest.fixture
def with_pr(run: Run, demo: str) -> str:
    run("order", "add", "--title", "land it", "--project", "The drive", "--workstream", "Drive tooling", "land it")
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


def test_pr_add_waits_on_his_tap_unless_it_is_a_port_exempt_drive_tooling_outside_forge_ai_or_untapped_for_a_reason(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    key = "Forge-AI/monorepo#42"
    assert cli.main(["pr", "add", PR, "--job", "j1", "--no-tap"]) == 2
    assert "--no-tap needs --why <reason>" in capsys.readouterr().err
    assert cli.main(["pr", "add", PR, "--job", "j1", "--no-tap", "--why", "a teammate lands it"]) == 2
    assert "only his tap or an exempt class lands a Forge-AI PR" in capsys.readouterr().err
    assert key not in state.load("demo")["prs"]
    assert run("pr", "add", "https://github.com/yasyf/cc-skills/pull/8", "--job", "j1", "--no-tap", "--why", "a teammate lands it")[0] == 0
    assert run("pr", "add", PR, "--job", "j1", "--exempt", "test-only")[0] == 0
    prs = state.load("demo")["prs"]
    assert (prs["yasyf/cc-skills#8"]["wants_tap"], state.owed_step(prs["yasyf/cc-skills#8"])) == (False, None)
    assert (prs[key]["wants_tap"], prs[key]["exempt"], state.owed_step(prs[key])) == (True, "test-only", "landing")
    with state.edit("demo") as data:
        data["prs"].pop(key)
        data["orders"]["o1"]["text"] = "Drive tooling: the watch draws his card"
    assert run("pr", "add", PR, "--job", "j1")[0] == 0
    assert run("pr", "add", "https://github.com/yasyf/cc-skills/pull/7", "--job", "j1")[0] == 0
    prs = state.load("demo")["prs"]
    assert (prs[key]["wants_tap"], prs["yasyf/cc-skills#7"]["wants_tap"]) == (True, False)


def test_pr_add_and_set_refuse_a_pr_someone_else_opened_unless_the_order_it_serves_names_it(
    run: Run, demo: str, on_github: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "Forge-AI/monorepo#42"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["author"] = {"login": "rowan", "is_bot": False}
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert f"{key}: refused, nothing added: {state.foreign(key, 'rowan', [])}" in capsys.readouterr().err
    assert key not in state.load("demo")["prs"]
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = "land #42 once it is green"
    assert run("pr", "add", PR, "--job", "j1")[0] == 0
    assert state.load("demo")["prs"][key]["author"] == "rowan"
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = "land it"
    assert cli.main(["pr", "set", PR, "--exempt", "test-only"]) == 2
    assert f"{key}: refused, nothing set: {state.foreign(key, 'rowan', [])}" in capsys.readouterr().err
    assert state.load("demo")["prs"][key]["exempt"] is None


def test_a_yasyf_pr_starts_one_lander_once_its_builders_ready_is_taken_with_its_proof_file_kept(
    run: Run, meta: str, tmp_path: Path, on_github: dict, landers: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    url = "https://github.com/yasyf/cc-skills/pull/7"
    assert run("pr", "add", url, "--job", "j1") == (0, "yasyf/cc-skills#7")
    assert landers == []
    kept_proof(tmp_path, "j1")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"READY {url}\n"))
    code, out = run("job", "report", "j1", "-")
    assert (code, out.splitlines()[1].startswith("yasyf/cc-skills#7: merging it with gh pr merge --squash --admin, then installing")) == (0, True)
    assert landers == [("yasyf/cc-skills#7",)]
    assert state.load("demo")["prs"]["yasyf/cc-skills#7"]["lander"]["head"] == "abc"
    assert run("pr", "add", url, "--job", "j1")[1] == f"yasyf/cc-skills#7: not merging it: lander pid {os.getpid()} already merges it"
    on_github["baseRefName"] = "j1-below"
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#7"]["lander"]["outcome"] = "not landed: gh pr merge --squash --admin refused it: conflict"
    assert run("pr", "add", url, "--job", "j1")[1] == (
        "yasyf/cc-skills#7: not merging it: it sits on j1-below, not main; register it again once the PR below it merges"
    )
    assert landers == [("yasyf/cc-skills#7",)]


def test_pr_add_of_a_port_takes_the_pure_port_exemption_and_no_tap(run: Run, demo: str, on_github: dict) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    on_github["title"] = "Port: land it"
    assert run("pr", "add", PR, "--job", "j1", "--port-of", "51234")[0] == 0
    pr = state.load("demo")["prs"]["Forge-AI/monorepo#42"]
    assert (pr["port_of"], pr["exempt"], pr["wants_tap"], state.owed_step(pr)) == (51234, "pure port", False, "landing")


def test_pr_add_on_a_public_repo_takes_demo_pictures(run: Run, demo: str, on_github: dict, visibilities: dict) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
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
    run("pr", "add", PR, "--job", "j1")
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
    run("pr", "add", PR, "--job", "j1")
    crossed = "o1 is a drive tooling order and o3 is an order of his feature work, and one job or PR never carries both: start a job on o3 with drive job add <kind> --order o3"
    assert cli.main(["job", "set", "j1", "--carries", "o3"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: j1 --carries o3: refused, {crossed}"
    assert cli.main(["pr", "set", PR, "--serves", "o3"]) == 2
    assert capsys.readouterr().err.strip() == f"drive: Forge-AI/monorepo#42 --serves o3: refused, {crossed}"
    assert state.load("demo")["jobs"]["j1"]["carries"] == {}
    assert run("job", "set", "j1", "--carries", "o2")[0] == 0
    assert state.load("demo")["jobs"]["j1"]["carries"] == {"o2": ["Drive tooling: fix ask"]}
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
    assert cli.main(["pr", "add", PR, "--job", "j1", "--port-of", "51234"]) == 2
    refused = capsys.readouterr().err
    assert f"it changes {table}, how its data is stored, so it is no pure port of #51234" in refused and "modelng the data" in refused
    assert "Forge-AI/monorepo#42" not in state.load("demo")["prs"]
    monkeypatch.setattr(served, "change", lambda key: {"title": "Port: land it", "head": "abc", "diff": diff.replace(table, "dashboard/src/visuals.ts").encode()})
    assert run("pr", "add", PR, "--job", "j1", "--port-of", "51234")[0] == 0


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
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: Forge-AI/monorepo#42: refused, its title "move strip" does not open Fix:, New:, Cleanup: or Port:, the kind of change'
        ' his card names it by: retitle it with gh pr edit 42 -R Forge-AI/monorepo --title "<Fix|New|Cleanup|Port>: move strip", then run this again'
    )
    assert state.load("demo")["prs"] == {}
    for title in ("Port: move strip", "store: 🐛 move strip", "fix(store): move strip"):
        on_github["title"] = title
        assert run("pr", "add", PR, "--job", "j1")[0] == 0


def test_pr_add_refuses_a_body_whose_shown_sections_speak_of_him_as_he_or_his(run: Run, demo: str, on_github: dict, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Land it", "--project", "The drive", "--workstream", "Releases", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land it")
    on_github["body"] = CARD_BODY.replace("once your tap comes in", "once his tap comes in")
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert capsys.readouterr().err.strip() == (
        'drive: Forge-AI/monorepo#42: refused, nothing added: its card shows him these sections, which speak of him as he or his: summary: "`land.py` lands the PR once his tap comes in.".'
        ' Say you and your instead, as in "Your Nov 11 words narrowed it", edit the body with gh pr edit 42 -R Forge-AI/monorepo --body-file <file>, then run this again'
    )
    assert state.load("demo")["prs"] == {}
    on_github["body"] = CARD_BODY.replace("once your tap comes in", 'once your tap comes in, as `he` and "his tap" say')
    assert run("pr", "add", PR, "--job", "j1")[0] == 0


def test_pr_add_refuses_a_show_me_fence_that_does_not_draw_and_adds_it_once_it_draws(run: Run, demo: str, on_github: dict, chrome: Chrome, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Move the strip", "--project", "The drive", "--workstream", "Releases", "move the strip")
    run("job", "add", "builder", "--order", "o1", "--goal", "move it")
    on_github["body"] = CARD_BODY + '\n## Show me\nThe map\n```arch\n{"arch": 1}\n```\n\n![before](https://example.com/before.png)\n'
    chrome.refusal = '"name" is required'
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert capsys.readouterr().err.strip() == 'drive: Forge-AI/monorepo#42: refused, nothing added: its Show me arch does not draw: "name" is required'
    assert state.load("demo")["prs"] == {}
    chrome.refusal = None
    assert run("pr", "add", PR, "--job", "j1")[0] == 0
    assert [kind for kind, _ in chrome.drawn] == ["arch"]


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
    assert cli.main(["pr", "add", PR, "--job", "j1", "--exempt", "incident fix"]) == 2
    refusal = capsys.readouterr().err
    assert 'refused, it reverts a change, and a revert never lands as an "incident fix"' in refusal
    assert f"Add it waiting on his tap with drive pr add {PR} --job j1. Draw his tap card with drive pr set {PR} --tap --serves o1 --design none" in refusal
    assert state.load("demo")["prs"] == {}
    assert run("pr", "add", PR, "--job", "j1")[0] == 0
    assert state.load("demo")["prs"]["Forge-AI/monorepo#42"]["wants_tap"] is True
    on_github["body"] = CARD_BODY
    assert run("pr", "add", PR, "--job", "j1")[0] == 0


def test_job_add_records_a_local_builder_past_the_macs_cap_waiting_its_turn_with_its_place_and_refuses_a_monorepo_one_while_drive_place_says_remote(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.95)
    monkeypatch.setattr(place, "limit", lambda: 0.8)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "land the strip")
    assert cli.main(["job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "Forge-AI/monorepo"]) == 2
    assert capsys.readouterr().err.strip() == (
        "drive: refused, no job added: this Mac is 95% busy, at or over its 80% limit, so this builder starts remote: add --remote,"
        " or wait until drive place says local"
    )
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "Forge-AI/monorepo", "--remote")[0] == 0
    for _ in range(place.LOCAL_BUILDERS - 1):
        assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills", "--agent", "a")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills")[0] == 0
    assert state.load(demo)["jobs"]["j9"]["state"] == "running"
    with state.edit("demo") as data:
        data["jobs"]["j9"].update(state.local_lane("dispatch-9"))
    code, added = run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills")
    added_id, waits, *_ = added.splitlines()
    assert (code, added_id) == (0, "j10")
    assert waits.startswith("j10 waits its turn for a builder slot on this Mac, 1 of 1 in line, since 8 builders already run on this Mac, its cap of 8 (j2 on o1")
    assert waits.endswith("; j9 on o1, its Orca lane dispatch-9, not ended): start no agent for it until the watch's FREE line names it")
    queued = state.load(demo)["jobs"]["j10"]
    assert (queued["state"], queued["waits"], queued["agent"]) == ("waiting", [state.SLOT_WAIT], None)
    with state.edit("demo") as data:
        data["jobs"]["j2"]["state"] = "done"
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "yasyf/cc-skills")[1].splitlines()[1] == (
        "j11 waits its turn for a builder slot on this Mac, 2 of 2 in line, since builders already wait their turn for a slot on this Mac"
        " ahead of it, earliest due first (j10): start no agent for it until the watch's FREE line names it"
    )
    assert place.waiting_builders(state.load(demo)) == ["j10", "j11"]
    shown = run("status")[1]
    assert "j10 builder waiting: land; waits on a builder slot on this Mac, 1 of 2 in line" in shown
    assert "j11 builder waiting: land; waits on a builder slot on this Mac, 2 of 2 in line" in shown
    assert json.loads(run("status", "--json")[1])["jobs"]["j11"]["in_line"] == "2 of 2 in line"
    assert run("job", "add", "reader", "--order", "o1", "--goal", "read")[0] == 0


def test_job_add_records_an_agent_already_running_past_the_macs_cap_and_its_limit(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.95)
    monkeypatch.setattr(place, "limit", lambda: 0.8)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "land the strip")
    for _ in range(place.LOCAL_BUILDERS):
        assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--agent", "a")[0] == 0
    assert run("job", "add", "builder", "--order", "o1", "--goal", "land", "--repo", "Forge-AI/monorepo", "--agent", "a-9")[0] == 0
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
        f' `attrs: {{"thread": "{thread}", "order": "o1", "job": "j1", "progress": "true"}}`; then keep your full report and return as usual\n'
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
    run("pr", "add", PR, "--job", "j1")
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
    code, out = run("status", "o1")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders"
    assert 'Forge-AI/monorepo#42 "Fix: the watched title" open, checks pass, review approved' in out
    assert json.loads(run("status", "--json")[1])["prs"][with_pr]["title"] == "Fix: the watched title"


def test_status_prints_an_open_pr_while_the_installed_cc_slack_lists_no_closed_cards(run: Run, with_pr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": []})
    code, out = run("status")
    assert code == 0
    assert out.splitlines()[0] == "demo: 1 open of 1 orders"
    assert json.loads(run("status", "--json")[1])["prs"][with_pr]["card_state"] is None


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


def test_job_add_prints_each_live_job_already_carrying_its_part_under_its_id(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "three spikes", "--parts", "spike 1", "spike 2", "spike 3")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix   the\nwatch")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix", "--agent", "a")
    data = state.load("demo")
    assert list(data["orders"]["o1"]["parts"]) == ["fix the watch"]
    assert data["jobs"]["j1"]["parts"] == ["fix the watch"]
    assert data["jobs"]["j1"]["session"] == os.environ["CLAUDE_CODE_SESSION_ID"]


def test_setting_parts_keeps_what_a_kept_part_recorded(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, slack_thread: "Thread"
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "two answers", "--parts", "why", "how")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back, find the cause", "--thread", "1794200000.000100", "--due", "+10m", "--parts", "stop the harm", "rollback", "cause")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "stop the harm, roll back", "--thread", "1794200000.000100", "--parts", "stop the harm", "rollback")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how fast can restore go?", "--thread", "1794100000.000100")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--thread", "1794100000.000100", "--due", "+30m")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship web search", "--thread", "1794100000.000100")
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "tell the stopped people", "--thread", "1794100000.000100", "--due", "+30m", "--parts", *parts)
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    monkeypatch.setattr(cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": {"part": "first check", "incident": "true"}}}))
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
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    slack_thread.messages.append({"ts": "1794200000.000100", "thread_ts": "1794200000.000100", "from_user": True})
    line = slack_thread.post(state.now(), from_claude=True)
    assert cli.main(["order", "posted", "o1", line]) == 2
    assert (
        "when his message at the root of that thread is another alert on this incident, that alert is its own order, which the coordinator"
        ' records with drive order add "<his alert>" --title "<a few words>" --from slack:D123/1794200000.000100 --thread 1794200000.000100 --joins o1'
        ' --project "The drive" --workstream "Drive tooling", and this line goes on that order'
    ) in capsys.readouterr().err
    alert = "SandSQL replica lanes unproven on staging"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", alert, "--from", "slack:D123/1794200000.000100", "--thread", "1794200000.000100", "--joins", "o1")
    assert run("order", "posted", "o2", line) == (0, f"o2 progress line, no part delivered: {line}")
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")


def test_a_line_in_the_thread_of_an_order_outside_the_incident_is_refused_naming_that_order_and_no_join(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "SandSQL errors on plat", "--thread", "1794100000.000100", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the copy stalls", "--thread", "1794200000.000100")
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
        state.carry(data, "j1", "o2", list(data["orders"]["o2"]["parts"]))
        state.drop_carries(data, "j1")
    line = slack_thread.post(state.now() + timedelta(seconds=1), from_claude=True)
    assert run("order", "posted", "o2", line) == (0, f"o2 progress line, no part delivered: {line}")
    alert_order = state.load("demo")["orders"]["o2"]
    assert (alert_order["kept"], state.owed(alert_order), alert_order["last_line"]) == (None, False, state.stamp(slack.sent_at(slack_thread.messages[-1])))


def test_an_alert_saved_before_joins_was_kept_loads_joined_to_the_incident_its_owner_job_carries_it_for(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL errors on plat", "--incident", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL error log count", "--joins", "o1")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "SandSQL restore runs retrying", "--joins", "o2")
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
        "23": ({"close": "o2", "part": state.whole_order(fix)}, moment + timedelta(seconds=30)),
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


def test_a_reporter_the_bot_cannot_reach_is_told_in_their_group_dm_with_him_and_only_his_words_take_a_reporter_off(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix it", "--reporter", "C0REPORTS01/1794400100.000200"]) == 2
    assert "refused, name the bug report by its Slack permalink or as slack:<channel>/<its thread's ts>" in capsys.readouterr().err
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
    assert state.load("demo")["orders"]["o2"]["reporter"] is None


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
        ' a part a post of the drive already answered is marked from the session holding o1 with drive order posted o1 <that post\'s link> --part "<part>"'
    ) in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["parts"]["why?"]["posted"] is None


def report_records(monkeypatch: pytest.MonkeyPatch, attrs: dict[str, dict], lane: str = "root") -> None:
    monkeypatch.setattr(
        cli.reads, "run", lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": lane, "at": "2026-11-11T09:08:16.896Z", "fields": {"attrs": attrs[argv[2]]}})
    )


def test_a_part_is_delivered_only_by_the_report_record_that_names_it_and_a_post_with_none_is_a_progress_line(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the unfurl", "--thread", "1794200000.000100", "--parts", "fix")
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
    assert f'marked from the session holding o1 with drive order posted o1 {line} --part "fix", posting nothing' in refused
    assert run("order", "posted", "o1", line) == (0, f"o1 progress line, no part delivered: {line}")
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] is None
    assert run("order", "posted", "o1", line, "--record", "661300") == (0, f'o1 "fix": posted {line}')
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] == line


def test_no_post_delivers_an_open_incidents_fix_until_the_watch_records_it_live_but_the_incidents_close(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "the journal will not open", "--thread", "1794200000.000100", "--parts", "fix", "first check")
    with state.edit("demo") as data:
        data["orders"]["o1"]["incident"] = True
    line = slack_thread.post(state.now(), from_claude=True)
    assert cli.main(["order", "posted", "o1", line, "--part", "fix"]) == 2
    assert '--part "fix": refused, o1\'s "fix" is the fix of an open incident, which a post delivers only once the fix runs where the incident hurt' in capsys.readouterr().err
    run("start", "demo", "--role", "communicator")
    with state.edit("demo") as data:
        data["roles"]["communicator"]["session"] = os.environ["CLAUDE_CODE_SESSION_ID"]
        data["roles"]["coordinator"]["session"] = "elsewhere"
    report_records(monkeypatch, {"674088": {"order": "o1", "incident": "true", "part": "o1:fix"}, "674089": {"part": "fix;o1:first check"}, "674090": {"close": "o1"}})
    assert run("order", "posted", "o1", line, "--record", "674088") == (0, f"o1 progress line, no part delivered: {line}")
    assert run("order", "posted", "o1", line, "--record", "674089") == (0, f'o1 "first check": posted {line}')
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] is None
    assert run("order", "posted", "o1", line, "--record", "674090") == (0, f'o1 "fix", "first check": posted {line}')
    with state.edit("demo") as data:
        data["orders"]["o1"]["parts"]["fix"]["posted"] = None
        data["orders"]["o1"]["fixed"] = "release 13990 ships #54336 on sand"
    assert run("order", "posted", "o1", line, "--record", "674088") == (0, f'o1 "fix": posted {line}')


def test_the_session_holding_an_order_closes_a_part_by_the_drives_post_that_already_answered_it(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "why does the search worker OOM?", "--thread", "1794200000.000100", "--parts", "cause", "fix")
    answered = slack_thread.post(state.now(), from_claude=True)
    his = slack_thread.post(state.now() + timedelta(seconds=1))
    assert cli.main(["order", "posted", "o1", his, "--part", "cause"]) == 2
    assert f"{his} is not the drive's post" in capsys.readouterr().err
    assert run("order", "posted", "o1", answered, "--part", "cause") == (0, f'o1 "cause": posted {answered}, the post that answered it')
    parts = state.load("demo")["orders"]["o1"]["parts"]
    assert (parts["cause"]["posted"], parts["fix"]["posted"]) == (answered, None)
    assert run("order", "posted", "o1", answered, "--part", "fix", "--record", "670354") == (0, f'o1 "fix": posted {answered}, the post that answered it')
    assert state.load("demo")["orders"]["o1"]["parts"]["fix"]["posted"] == answered


def test_the_holder_marks_no_part_by_a_post_whose_words_say_it_is_not_done(
    run: Run, demo: str, slack_thread: "Thread", capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "why does the search worker OOM?", "--thread", "1794200000.000100", "--parts", "cause", "fix")
    unconfirmed = slack_thread.post(state.now(), from_claude=True, text="Entries past 1024 tokens OOM the worker; the cause is not confirmed and no fix shipped.")
    jev.unsaid = {"cause"}
    assert cli.main(["order", "posted", "o1", unconfirmed, "--part", "cause"]) == 2
    assert (
        f'--part "cause": refused, Jev reads the words of {unconfirmed} as not saying "cause" is done, so it delivers nothing;'
        " mark it by the drive's post whose words do"
    ) in capsys.readouterr().err
    assert state.load("demo")["orders"]["o1"]["parts"]["cause"]["posted"] is None
    assert jev.asked[-1]["state"]["post"] == "Entries past 1024 tokens OOM the worker; the cause is not confirmed and no fix shipped."


STATUS, CAP = "status of the fixes", "the intern cap is sized to the pod's memory, its own PR"
FIXES = "whats the status of our fixes, and size the intern cap to the pod's memory as its own PR"
RETRO = "the self-retro: why the row-id change also changed the ids' printed format"
STATUS_ANSWER = (
    "Where each fix stands: the spill-file PR is approved but its test build failed; the child-table PR conflicts with dev and"
    " lost its approval; the intern cap is being built now as a small PR. None has shipped yet."
)
RETRO_ANSWER = (
    "Retro on the id format: the switch to UUIDv7 row ids also changed how each id prints, from 32 hex characters to the dashed"
    " form, though nothing asked for it. The lesson: a change keeps each printed form it was not asked to change."
)


@pytest.mark.parametrize(
    ("words", "parts", "answers", "post", "part", "owed", "delivers"),
    [
        pytest.param(FIXES, [STATUS, CAP], [STATUS], STATUS_ANSWER, STATUS, 0.11, True, id="status answer"),
        pytest.param("do a self-retro on the id format, and the PR judge refuses what was not asked", [RETRO, "the PR judge"], [RETRO], RETRO_ANSWER, RETRO, 0.20, True, id="retro"),
        pytest.param(FIXES, [STATUS, CAP], [STATUS], "Checking where each fix stands now; I will post it by 1:30 PM.", STATUS, 0.77, False, id="a promise"),
        pytest.param(FIXES, [STATUS, CAP], [STATUS], STATUS_ANSWER, CAP, 0.88, False, id="being built now"),
        pytest.param("why does the search worker OOM?", ["cause", "fix"], [], "Entries past 1024 tokens OOM the worker; the cause is not confirmed and no fix shipped.", "cause", 0.89, False, id="no fix shipped"),
        pytest.param(FIXES, [STATUS, CAP], [STATUS], "The intern cap is live: its PR merged and shipped, sizing the index to an eighth of the pod's memory.", CAP, 0.07, True, id="merged and live"),
    ],
)
def test_a_post_delivers_a_part_he_reads_by_giving_it_whatever_it_says_and_work_only_once_it_says_it_is_done(
    run: Run,
    demo: str,
    slack_thread: "Thread",
    capsys: pytest.CaptureFixture[str],
    jev: Jev,
    words: str,
    parts: list[str],
    answers: list[str],
    post: str,
    part: str,
    owed: float,
    delivers: bool,
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", words, "--thread", "1794200000.000100", "--parts", *parts, *(["--answers", *answers] if answers else []))
    link = slack_thread.post(state.now(), from_claude=True, text=post)
    jev.owed = {part: owed}
    kind = "answer" if part in answers else "build"
    code = cli.main(["order", "posted", "o1", link, "--part", part])
    assert jev.asked[-1]["questions"] == {"part0": {"binary": {"instructions": settled.OWED_QUESTIONS[kind].format(part=part)}}}
    assert (code, state.load("demo")["orders"]["o1"]["parts"][part]["posted"]) == ((0, link) if delivers else (2, None))
    refused = capsys.readouterr().err
    if delivers:
        return
    if kind == "answer":
        assert f'--part "{part}": refused, Jev reads the words of {link} as not giving "{part}", so it delivers nothing; mark it by the drive\'s post that gives it' in refused
        return
    marked = " ".join(f'"{each}"' for each in parts if each in answers or each == part)
    assert f'Jev reads the words of {link} as not saying "{part}" is done' in refused
    assert f"when it is words he reads, such as his question's answer, a retro or a status, which a post gives whatever it says, mark it so first with drive order set o1 --answers {marked}" in refused


def test_each_part_is_filed_as_words_he_reads_or_work_and_order_set_answers_replaces_which_are(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def kinds() -> dict[str, str]:
        return {part: held["kind"] for part, held in state.load("demo")["orders"]["o1"]["parts"].items()}

    filing = ("--title", "Short title", "--project", "p", "--workstream", "w", FIXES, "--parts", STATUS, CAP)
    assert cli.main(["order", "add", *filing, "--answers", "status"]) == 2
    assert f'o1 has no part status; its parts are "{STATUS}", "{CAP}"' in capsys.readouterr().err
    assert state.load("demo")["orders"] == {}
    run("order", "add", *filing, "--answers", STATUS)
    assert kinds() == {STATUS: "answer", CAP: "build"}
    run("order", "set", "o1", "--parts", STATUS, CAP, RETRO)
    assert kinds() == {STATUS: "answer", CAP: "build", RETRO: "build"}
    run("order", "set", "o1", "--answers", STATUS, RETRO)
    assert kinds() == {STATUS: "answer", CAP: "build", RETRO: "answer"}
    run("order", "set", "o1", "--answers")
    assert kinds() == {STATUS: "build", CAP: "build", RETRO: "build"}
    run("order", "set", "o1", "--kind", "answer")
    assert kinds() == {STATUS: "answer", CAP: "answer", RETRO: "answer"}
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "which page shows a run's history?", "--kind", "answer")
    assert [held["kind"] for held in state.load("demo")["orders"]["o2"]["parts"].values()] == ["answer"]


def test_job_set_parts_replaces_the_parts_a_job_delivers_so_a_done_builder_delivers_the_part_it_built(
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
    assert (data["jobs"]["j1"]["parts"], state.delivered(data, "o1", main), state.delivered(data, "o1", state.IMMEDIATE_FIX)) == ([main], True, False)


def test_one_post_delivers_each_part_its_report_names_on_every_order_an_item_names(
    run: Run, demo: str, slack_thread: "Thread", monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the search worker", "--thread", "1794200000.000100", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "raise its RAM", "--thread", "1794200000.000100", "--parts", "increase the RAM")
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
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "which page shows a run's history?", "--thread", "1794200000.000100", "--owner", "communicator")
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
    assert state.load("demo")["orders"]["o1"]["parts"]["which page shows a run's history?"]["posted"] is None
    report_records(monkeypatch, {"664350": {"close": "o1"}}, lane="communicator")
    assert run("order", "posted", "o1", line, "--record", "664350") == (0, f'o1 "which page shows a run\'s history?": posted {line}')


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
    assert f"orca terminal create --worktree path:{tmp_path} --title demo-coordinator" in refused
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
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
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
        saved = json.loads(state.state_path("demo").read_text())
        for field in ("options_only", "once"):
            del saved["orders"]["o1"][field]
        state.state_path("demo").write_text(json.dumps(saved))
    order = state.load("demo")["orders"]["o1"]
    assert (order["options_only"], order["once"]) == (False, False)


def test_a_repair_he_asked_to_run_once_puts_up_no_pr(run: Run, demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "rerun the backfill once", "--once")
    run("job", "add", "builder", "--order", "o1", "--goal", "run the backfill")
    assert cli.main(["pr", "add", PR, "--job", "j1"]) == 2
    assert "refused, o1 is a repair he asked to run once: run it, and put up no PR for it" in capsys.readouterr().err
    assert state.load("demo")["prs"] == {}
    run("order", "set", "o1", "--no-once")
    assert run("pr", "add", PR, "--job", "j1") == (0, "Forge-AI/monorepo#42")


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
    for mention in ("drive order add X --from slack: X / X --due X --project X --workstream X --title X", "drive order add --due", "drive job add --order X"):
        assert refusal(mention, leaves) is None


def test_drive_manual_prints_only_the_rows_a_word_names(run: Run) -> None:
    code, out = run("manual", "coordinator", "tap")
    assert code == 0
    assert [line.split("|")[1].strip() for line in out.splitlines()] == ["`TAP Approve …`", "`TAP Approve <pr> not landed <m> minutes after his tap: <why>`", "`TAP Hold …`"]
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
MATCHED, BACKFILL, ASK_JEV, UNSAID = settled.matched, settled.backfill, settled.jev, settled.unsaid


def cards_asking(monkeypatch: pytest.MonkeyPatch, *prs: str) -> list[tuple[str, ...]]:
    question = {"question": "Which topology?", "options": ["Tree", "Ring"], "recommended": "Tree", "asked": state.stamp(), "url": DESIGN_CARD, "card": {"title": "Which topology?", "waits": "demo:j2-7"}}
    tap = {"asked": state.stamp(), "url": TAP_CARD, "prs": list(prs), "card": {"title": "New: land it?", "waits": "merge:Forge-AI:monorepo:42"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [question], "pr_cards": [tap], "closed": []})
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
    assert f"  {designing}  tap  j1 o1  land it  {TAP_CARD}" in listed
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
    assert heard.checked == [("a drafted reading of his question", PICK), (WORDS, PICK)]


@pytest.mark.parametrize(
    ("words", "link", "said", "refusal"),
    [
        (WORDS, PICK, f"{WORDS}.", None),
        ("Iris proposes … use-when line", PICK, "<https://example.com|Iris> *proposes* with one new tool, guided by each topology's use-when line", None),
        ("Iris proposes [one way:] two new tools", PICK, WORDS, f'his words at {PICK} do not say "two new tools"'),
        ("Iris should propose topologies itself", PICK, WORDS, f'his words at {PICK} do not say "Iris should propose topologies itself"'),
        (WORDS, PICK, None, f"{PICK} is neither his own message nor a card he answered"),
        (WORDS, None, WORDS, f'no permalink names where he said "{WORDS}"'),
        ("Tree", DESIGN_CARD, "Tree", None),
        ("Tree", DESIGN_CARD, "withdrawn", f"{DESIGN_CARD} is neither his own message nor a card he answered"),
        ("Tree", DESIGN_CARD, "open", f"{DESIGN_CARD} is neither his own message nor a card he answered"),
    ],
)
def test_words_count_as_his_answer_only_when_his_message_or_card_answer_at_the_link_holds_each_piece(
    slack_thread: Thread, words: str, link: str | None, said: str | None, refusal: str | None
) -> None:
    card = {"waits": "demo:j2-7", "url": DESIGN_CARD, "answer": {"label": "Tree", **({"withdrawn": True} if said == "withdrawn" else {})}}
    found = view.NO_CARDS | {"closed": [] if said == "open" else [card]}
    slack_thread.messages += [
        {"ts": "1794300000.000100", "text": "Which topology?", "from_user": False},
        {"ts": "1794300000.000300", "thread_ts": "1794300000.000100", "text": said or "a line the drive posted", "from_user": said is not None},
    ]
    unsaid = UNSAID(words, link, found)
    assert (unsaid is None) if refusal is None else (unsaid.startswith(refusal) and unsaid.endswith(f"; {settled.HIS_ANSWER}"))


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


def test_the_settled_check_skips_his_taps_approving_a_pr(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert [answer["id"][:2] for answer in MATCHED("where do a run's files go", "/work")] == ["b2", "c3", "e5", "f6"]
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


UPLOAD_MINT = {"id": "61d71da", "asked": "Where is an upload's ID reserved before the write lands?", "said": "Clients journal mint time with each write reservation, no server change"}
ORDERED_FIX = {
    "id": "6a6edbf",
    "asked": "A line told him a PR's tap card was refused because nobody checked that PR against his words; what must such a line say, and who fixes the gap?",
    "said": (
        "> Its card was refused because nobody checked that PR against your words\n"
        "thats not acceptable to dsay without telling me how you will prevent it, and fix that meta issue, im not saying this agin\n"
        "Notes: his words Nov 12 2026 12:44 AM, quoting the handler's 11:53 PM answer. Two rulings: (1) any line to him reporting a failure,"
        " refusal or gap also says how it is prevented, naming the tool change and what carries it; (2) the meta issue is fixed in the drive's"
        " tooling: the journal format PR was green and approved from 7:14 PM Nov 11 with no words check, the watch refused its card at 7:40 PM"
        " and posted that refusal to the coordinator as evidence at 7:40, 10:22, 11:37 PM and 12:14 AM, and no session ran the check for five"
        " hours. Handler's reading, not his words: a PR at READY never sits without the check against his words; the tool that finds it"
        " unchecked runs the check, or starts the job that does, instead of only reporting it."
    ),
}
OTHER_CARDS_PICK = {
    "id": "b666347",
    "asked": "Should the say hook's judge rule on a check's finding by that finding's own rule, or by whether your words cover the post?",
    "said": (
        "1 and then if we kepe getting false rejects move to 3\n"
        'Notes: his own words, typed as Other on the say hook\'s card at 2:54 AM Nov 12. "1" and "3" are the card\'s first and third options,'
        " in the card's order; its ask file holds their words."
    ),
}
BUILDER_COST = {
    "id": "ea5f9f6",
    "asked": (
        "Builders spend 64% of their cost after the PR opens (CI fixes, review edits, landing, install), at 288k average context on Opus."
        " Your global routing says Opus for every code-diff deliverable. How should that phase run?"
    ),
    "said": "Fresh Opus fixer\nOptions: Fresh Sonnet fixer (Recommended) | Fresh Opus fixer | Keep one builder",
}
UNCHECKED = (
    "Who checks a green, approved PR against your words when no session runs the check?",
    [
        "Per-PR check the watch starts: a green, approved PR's own diff judged against its order; pass draws your card; one Opus run each",
        "Reader agent the watch starts: reads your answers, picks its order and design, runs today's whole-builder check; one agent session each; enabling PRs still refused",
        "Louder reminders only: the watch's unchecked and refused-card lines always reach the coordinator as work; free, but a session must still act",
    ],
)
ORDERS_THE_FIX = {"none": 0.14, "picks": 0.05, "orders": 0.81}
NUMBERS_ANOTHER_CARD = {"none": 0.26, "picks": 0.39, "orders": 0.35}
COSTS = {"none": 0.67, "picks": 0.15, "orders": 0.18}
JEV_CASES = {
    "tap": ("Which token should the CI review step sign in to GitHub with?", ["A reviewer GitHub App's installation token", "A personal access token"], [], {}),
    "mint": (
        "How does the upload worker keep each upload ID's mint time?",
        ["Each upload records its mint time in a new UploadReserved journal event", "Each upload keeps its mint time in memory until the write lands"],
        [{"id": "b26dd47", "asked": "Who mints upload IDs: the server or each client?", "said": "So do 1 but no server issuing IDs"}, UPLOAD_MINT],
        {"b26dd47": {"none": 0.73, "picks": 0.05, "orders": 0.22}, "61d71da": {"none": 0.17, "picks": 0.78, "orders": 0.05}},
    ),
    "poller": (
        "Who starts each queue poller the first time a queue is created?",
        ["The worker, at boot, for every queue it finds", "The first enqueue on a new queue"],
        [{"id": "e86f7ae", "asked": "Which queue store does every worker register on?", "said": "the shared queue store everywhere (Recommended)"}],
        {"e86f7ae": {"none": 0.97, "picks": 0.02, "orders": 0.01}},
    ),
    "autoscaler": (
        "Should each browser pool get its own autoscaler, or should one autoscaler serve each tier?",
        ["One autoscaler per pool", "One autoscaler per tier"],
        [
            {"id": "8653f0f", "asked": "Which browser does each tier run?", "said": "The light tier runs the small headless browser, the heavy tier the full one"},
            {"id": "1439f73", "asked": "Which tiers keep warm browsers ready?", "said": "Warm browsers for the normal tier only"},
        ],
        {"8653f0f": {"none": 0.84, "picks": 0.06, "orders": 0.1}, "1439f73": {"none": 0.89, "picks": 0.06, "orders": 0.05}},
    ),
    "journal": (
        "Where should the upload worker journal the write reservation that keeps an upload ID's mint time?",
        ["In a new UploadReserved run event", "In memory, beside the pending write"],
        [
            {
                "id": "ad564f4",
                "asked": "The log agent's broken-pipe warnings (about 60 a minute): fix them in the old deploy scripts or the new infrastructure code?",
                "said": "1, but should be in the new infrastructure code and again build a proper abstraction for it, not in the old scripts",
            },
            UPLOAD_MINT,
        ],
        {"ad564f4": {"none": 0.9, "picks": 0.05, "orders": 0.05}, "61d71da": {"none": 0.25, "picks": 0.55, "orders": 0.2}},
    ),
    "ordered": (*UNCHECKED, [ORDERED_FIX, OTHER_CARDS_PICK, BUILDER_COST], {"6a6edbf": ORDERS_THE_FIX, "b666347": NUMBERS_ANOTHER_CARD, "ea5f9f6": COSTS}),
    "unordered": (*UNCHECKED, [OTHER_CARDS_PICK, BUILDER_COST], {"b666347": NUMBERS_ANOTHER_CARD, "ea5f9f6": COSTS}),
}
SETTLED = {"mint": ("61d71da", "picks"), "ordered": ("6a6edbf", "orders")}




@pytest.mark.parametrize("case", JEV_CASES)
def test_jev_reads_each_answer_alone_and_one_settles_a_question_once_its_picks_and_orders_reach_eight_in_ten(case: str, jev: Jev) -> None:
    question, options, found, recorded = JEV_CASES[case]
    jev.reads = recorded
    picked = settled.decided(question, options, found)
    assert (picked and (picked["id"], picked["settles"])) == SETTLED.get(case)
    assert sorted(answer["id"] for request in jev.asked for answer in request["state"]["answers"]) == sorted(answer["id"] for answer in found)
    assert [len(request["state"]["answers"]) for request in jev.asked] == [1] * len(found)
    if picked:
        assert ("He ordered what this question asks about, so build the option his words leave that removes the root cause" in settled.decided_refusal(picked, None)) == (picked["settles"] == "orders")


@pytest.mark.skipif(os.environ.get("DRIVE_REAL_JEV") != "1", reason="replays these cases on Jev itself, which DRIVE_REAL_JEV=1 asks for")
@pytest.mark.parametrize("case", ["ordered", "unordered"])
def test_jev_itself_settles_the_unchecked_pr_question_by_his_order_in_three_runs_of_three_and_sends_it_to_him_without_that_order(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settled, "jev", ASK_JEV)
    question, options, found, _ = JEV_CASES[case]
    assert [(picked := settled.decided(question, options, found)) and picked["id"] for _ in range(3)] == [SETTLED.get(case, (None,))[0]] * 3


def test_jev_is_asked_through_spawnllm_decide_and_a_failed_call_or_a_declined_question_is_unheard_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict]] = []
    answer = {"type": "label", "choice": "none", "probabilities": {"none": 0.9}, "confidence": 0.9}
    results = [
        subprocess.CompletedProcess([], 0, json.dumps({"model": "jev-1.13.0", "answers": {"decides": answer}}), ""),
        subprocess.CompletedProcess([], 1, "", "Installed 15 packages in 5ms\nError: No such command 'decide'.\n"),
        subprocess.TimeoutExpired(settled.DECIDE, 60),
        subprocess.CompletedProcess([], 0, json.dumps({"model": "jev-1.13.0", "answers": {"decides": {"type": "refused"}}}), ""),
    ]

    def spawnllm(argv: list[str], input: str, **_: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, json.loads(input)))
        if isinstance(result := results.pop(0), Exception):
            raise result
        return result

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=spawnllm, TimeoutExpired=subprocess.TimeoutExpired))
    request = {"state": {"question": "Which store?"}, "questions": {"decides": {"label": {"instructions": "Which?", "options": {"none": "No"}}}}}
    assert ASK_JEV(request) == {"decides": answer}
    failures = []
    for _ in range(3):
        with pytest.raises(settled.Unheard) as unheard:
            ASK_JEV(request)
        failures.append(str(unheard.value))
    assert calls == [(settled.DECIDE, request)] * 4
    assert failures == ["Error: No such command 'decide'.", f"Command '{settled.DECIDE}' timed out after 60 seconds", "Jev declined the question"]


def test_job_ask_says_on_stderr_that_jev_gave_no_answer_and_sends_the_question_to_him(
    run: Run, asking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], recorded: Answers, jev: Jev
) -> None:
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    recorded.found = [FLAT_RULING]
    jev.answer = None
    assert cli.main(["job", "ask", asking, str(block)]) == 0
    unheard = capsys.readouterr().err
    assert (
        "drive job ask: Jev did not say whether his answer ac61348 decides this question, so it goes to him: Command 'spawnllm decide' timed out after 60 seconds"
        in unheard
    )
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
        return subprocess.CompletedProcess(argv, 0, json.dumps(stored) if argv[2] == "list" else "{}", "")

    monkeypatch.setattr("drive.settled.subprocess", types.SimpleNamespace(run=ccn, TimeoutExpired=subprocess.TimeoutExpired))
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
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j1")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/45", "--job", "j2")
    run("job", "add", "builder", "--order", "o1", "--goal", "shipped")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/46", "--job", "j3")
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
    assert (reworked["wants_tap"], reworked["tap"], state.owed_step(reworked), data["jobs"]["j1"]["state"]) == (True, None, "reworking", "running")
    assert reworked["rework"] | {"at": None} == {"by": "a1", "words": WORDS, "at": None}
    assert data["answers"]["a1"]["cards"]["Forge-AI/monorepo#45"] == {"mark": "reopened", "job": "j1", "card": None}


def test_an_answer_under_a_card_lists_only_its_job_and_orders_cards_and_prs_and_those_its_words_name(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the tool")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j3")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/45", "--job", "j3")
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


def test_an_answer_stays_unfinished_until_each_job_waiting_on_an_order_it_touches_is_sent_his_words_or_marked_untouched(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards_asking(monkeypatch, PR)
    run("job", "add", "builder", "--order", "o1", "--goal", "build the store")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the new tool")
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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
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
    for number in (44, 45, 46):
        run("pr", "add", f"https://github.com/Forge-AI/monorepo/pull/{number}", "--job", "j1")
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
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/44", "--job", "j1")
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
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    settled = another_order_asking(run, monkeypatch)
    jev.changes = {"j3-9": 0.9}
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    capsys.readouterr()
    assert cli.main(["answer", "a1", "--stands", designing, "--settled", "demo:j3-9"]) == 2
    told = capsys.readouterr().err
    assert f"demo:j3-9: its card {LAYOUT_CARD} now shows his answer a1 with no buttons, settling j3's question, so j3 builds on his words" in told
    assert "  j3 waits on o2 and has not heard a1: SendMessage it his words verbatim, or mark it untouched" in told
    assert run("answer", "a1", "--sent", "j3") == (
        0,
        "his answer a1 recorded: 1 standing, 1 settled, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
    )
    assert settled == [ANSWERED, ("card-answer", "--card", LAYOUT_CARD, "--label", WORDS, "--settled", "--other", "--via", PICK)]
    data = state.load("demo")
    assert data["answers"]["a1"]["cards"]["demo:j3-9"] == {"mark": "settled", "job": "j3", "card": LAYOUT_CARD}
    assert [question["closed"] and (question["closed"]["how"], question["closed"]["answer"]) for question in data["jobs"]["j3"]["questions"]] == [("answered", "a1")]


def test_settled_redraws_a_card_it_decides_in_part_to_the_ask_its_job_keeps_for_what_is_left_and_refuses_one_unkept_or_not_linking_his_answer(
    run: Run, designing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    another_order_asking(run, monkeypatch)
    jev.changes = {"j3-9": 0.5}
    sent: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(tools, "sent", lambda name, lane, records: sent.append((lane, records)) or ["#9"])
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    unlinked, narrowed, loose = tmp_path / "unlinked.md", tmp_path / "narrowed.md", tmp_path / "loose.md"
    unlinked.write_text(SHORT_ASK)
    narrowed.write_text(SHORT_ASK.replace("Meanwhile nothing is built on either layout.", f"Meanwhile nothing is built on either layout, by <{PICK}|his answer>."))
    with state.edit("demo") as data:
        data["jobs"]["j3"]["questions"] += [state.new_question(str(path), LAYOUT, state.stamp()) for path in (unlinked, narrowed)]
    capsys.readouterr()
    for mark, refusal in [
        (designing, f"{designing} is no question card a job of this drive asked, so --settled cannot mark it"),
        (f"demo:j3-9={loose}", f"{loose.resolve()} keeps no question of j3 drawn on no card yet"),
        (f"demo:j3-9={unlinked}", f"{unlinked.resolve()} does not link his answer {PICK} above its Findings:"),
    ]:
        assert cli.main(["answer", "a1", "--settled", mark]) == 2
        assert refusal in capsys.readouterr().err
    assert sent == []
    code, out = run("answer", "a1", "--stands", designing, "--settled", f"demo:j3-9={narrowed}", "--sent", "j3")
    redrawn = str(narrowed.resolve())
    assert (code, out.splitlines()) == (
        0,
        [
            "his answer a1 recorded: 1 standing, 1 redrawn, 0 superseded, 0 reopened by it; 1 waiting job sent his words, 0 untouched",
            f"demo:j3-9: its card {LAYOUT_CARD} is redrawn in place to {redrawn}, asking only what his answer a1 leaves open, by the ask record sent to communicator@demo",
        ],
    )
    assert sent == [("j3", [{"kind": "ask", "to": ["communicator@demo"], "attr": {"job": "j3", "ask": redrawn, "waits": "demo:j3-9"}, "path": redrawn}])]
    data = state.load("demo")
    kept = [(question["file"], question["waits"], question["card"]) for question in data["jobs"]["j3"]["questions"]]
    assert kept == [(str(unlinked), None, None), (str(narrowed), "demo:j3-9", LAYOUT_CARD)]
    assert data["answers"]["a1"]["cards"]["demo:j3-9"] == {"mark": "redrawn", "job": "j3", "card": LAYOUT_CARD, "ask": redrawn}


def test_an_answer_records_nothing_while_jev_cannot_read_it_against_his_open_cards(
    run: Run, designing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev
) -> None:
    another_order_asking(run, monkeypatch)
    jev.answer = None
    assert cli.main(["answer", "a1", "--words", WORDS, "--link", PICK]) == 2
    assert (
        "nothing recorded in the drive: Jev did not read his answer against his 2 open cards (Command 'spawnllm decide' timed out after 60 seconds)"
        in capsys.readouterr().err
    )
    assert "a1" not in state.load("demo")["answers"]


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
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    calls: list[tuple] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *args: calls.append(args) or {"answered": True})
    words, via = "We're already addressing this with the lazy + restate object", "https://acme.slack.com/archives/D123/p1794300000000999"
    code, line = run("cards", "withdraw", "demo:j1-7", "--words", words, "--via", via)
    assert (code, calls) == (0, [("card-answer", "--card", shown["url"], "--label", words, "--withdrawn", "--via", via)])
    assert line == f"withdrew his card {shown['url']} (demo:j1-7): it shows his words and a link to them, with no buttons; j1 asks again under demo:j1-7 once its work is back, which redraws this card"
    assert cli.main(["cards", "withdraw", "demo:j1-8", "--words", words, "--via", via]) == 2
    assert "demo:j1-8: not one of his open question cards" in capsys.readouterr().err


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
    path = state.state_path(demo)
    path.write_text(json.dumps(json.loads(path.read_text()) | {"saved_by": "9.9.9"}))
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
    assert capsys.readouterr().err.startswith(f"drive: drive.json of {demo} was saved by long-running 9.9.9")
