import fcntl
import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import LEAD, REAL_DOWN_MAC_BOXES, REAL_SCRATCH, edit, EnvsRelease, NamedRepo, provide
from drive import cli, github, install, picture, providers, reads, render, served, settled, stage, state
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


@pytest.mark.parametrize(
    ("variable", "folder", "moved"),
    [(None, None, False), ("XDG_STATE_HOME", ".local/state", False), ("XDG_STATE_HOME", "peek", True), ("LONG_RUNNING_STATE", "proof", True)],
)
def test_a_state_root_either_variable_moves_off_the_home_one_holds_a_scratch_drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str | None, folder: str | None, moved: bool
) -> None:
    monkeypatch.setattr(state, "scratch", REAL_SCRATCH)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LONG_RUNNING_STATE", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    if variable:
        monkeypatch.setenv(variable, str(tmp_path / folder))
    assert state.scratch() is moved


def make(name: str, session: str | None = None, started: str | None = None) -> None:
    data = state.new_drive(name, "coordinator", session, "/work")
    if started:
        data["roles"]["coordinator"]["started"] = started
    state.create(data)


def test_a_save_takes_down_the_mac_boxes_of_each_job_it_moves_out_of_running_or_into_done_failed_or_stopped_once(state_root: Path, mac_boxes_downed: list[str]) -> None:
    make("demo")
    with edit("demo") as data:
        data["jobs"] |= {job_id: state.new_job("o1", "builder", "use a Mac box", [], state.stamp()) for job_id in ("j1", "j2", "j3", "j4")}
    with edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
        data["jobs"]["j2"]["state"] = "waiting"
        data["jobs"]["j3"]["state"] = "built"
        data["jobs"]["j4"]["state"] = "failed"
    assert mac_boxes_downed == ["demo/j1", "demo/j4"]
    with edit("demo") as data:
        data["jobs"]["j1"]["state"] = "stopped"
        data["jobs"]["j2"]["state"] = "stopped"
        data["jobs"]["j3"]["goal"] = "use another Mac box"
        data["jobs"]["j4"]["goal"] = "use another Mac box"
    assert mac_boxes_downed == ["demo/j1", "demo/j4", "demo/j2"]
    with edit("demo") as data:
        data["jobs"]["j1"]["state"] = "running"
    with edit("demo") as data:
        data["jobs"]["j1"]["state"] = "done"
    assert mac_boxes_downed == ["demo/j1", "demo/j4", "demo/j2", "demo/j1"]
    with edit("demo") as data:
        data["jobs"]["j3"]["state"] = "running"
    with edit("demo") as data:
        data["jobs"]["j3"]["state"] = "waiting"
    assert mac_boxes_downed == ["demo/j1", "demo/j4", "demo/j2", "demo/j1", "demo/j3"]


def test_a_save_hands_a_stopped_jobs_mac_boxes_to_a_process_of_its_own_that_returns_them_to_the_pool_from_the_drives_checkout(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Callable[..., tuple[int, str]]
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "tools").mkdir(parents=True)
    (checkout / "tools" / "ci").write_text(f'#!/bin/sh\necho "$(pwd -P) $*" >> {tmp_path / "downs"}\n')
    (checkout / "tools" / "ci").chmod(0o755)
    (checkout / ".claude").mkdir()
    (checkout / ".claude" / "long-running.toml").write_text('[providers]\nmac_box = "Boxes"\n')

    class Boxes(providers.MacBoxProvider):
        def down(self, box: str, holder: str, finished: bool) -> int:
            self.run("tools/ci mac-box down --holder {holder} {box}", box=box, holder=holder)
            return 0

    provide(monkeypatch, checkout, mac_box=Boxes)
    state.create(state.new_drive("demo", "coordinator", None, str(checkout)))
    with edit("demo") as data:
        data["jobs"] |= {job_id: state.new_job("o1", "builder", "build it", [], state.stamp()) | {"state": "stopped"} for job_id in ("j1", "j2")}
    detached: list[tuple[str, list[str], str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: detached.append((name, args, log)) or 0)
    REAL_DOWN_MAC_BOXES("demo", state.load("demo"), ["j1", "j2"])
    REAL_DOWN_MAC_BOXES("demo", state.new_drive("demo", "coordinator", None, str(tmp_path / "elsewhere")), ["j3"])
    assert detached == [("demo", ["mac-boxes-down", "j1", "j2"], state.MAC_BOXES_LOG)]
    assert run("mac-boxes-down", "j1", "j2", "--drive", "demo")[0] == 0
    assert (tmp_path / "downs").read_text().splitlines() == [f"{checkout.resolve()} mac-box down --holder demo/j1 j1", f"{checkout.resolve()} mac-box down --holder demo/j2 j2"]


def test_edit_saves_atomically_and_leaves_no_temp(state_root: Path) -> None:
    make("demo")
    with edit("demo") as data:
        data["orders"]["o1"] = state.new_order("ship it", "terminal", None, {}, state.stamp())
    assert state.load("demo")["orders"]["o1"]["text"] == "ship it"
    assert [path.name for path in (state_root / "demo").iterdir() if path.name.endswith(".tmp")] == []


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
        (["bk", "build", "view", "16042", "-p", "monorepo", "-o", "json"], True),
        (["bk", "build", "list", "-p", "monorepo", "--branch", "dev"], True),
        (["bk", "build", "create", "-p", "monorepo"], False),
        (["tailscale", "status", "--self", "--peers=false", "--json"], True),
        (["tailscale", "whois", "--json", "100.64.0.1"], True),
        (["tailscale", "funnel", "--bg", "--yes", "--https=8443"], False),
        (["claude", "-p", "--model", "claude-opus-5-5", "--restricted", "--strict-mcp-config", "--tools", "", "--json-schema", "{}"], True),
        (["claude", "-p", "--model", "claude-opus-5-5", "--restricted", "--add-dir", "/tree", "--strict-mcp-config", "--tools", "Read,Grep,Glob"], False),
        (["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless=new", "--incognito", "--screenshot=/tmp/p.png", "file:///tmp/p.html"], True),
    ],
)
def test_a_dry_run_lets_only_a_command_that_reads_run(argv: list[str], reading: bool) -> None:
    assert state.reads_only(argv) is reading


PREVIOUS_RELEASE_SAVE = """
import sys
sys.path.insert(0, sys.argv[1])
from drive import state


def staled(data):
    data["slack"]["page_stale"] = "the previous release's save"


if hasattr(state, "write"):
    state.write("demo", staled)
else:
    with state.edit("demo") as data:
        staled(data)
"""


@pytest.fixture(scope="module")
def previous_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    plugin = Path(state.__file__).resolve().parents[2]
    origin = subprocess.run(["git", "-C", str(plugin), "remote", "get-url", "origin"], check=True, capture_output=True, text=True).stdout.strip()
    release = tmp_path_factory.mktemp("previous-release")
    subprocess.run(["git", "init", "-q", str(release)], check=True)
    subprocess.run(["git", "-C", str(release), "fetch", "-q", "--depth", "1", origin, f"refs/tags/long-running--v{state.VERSION}"], check=True, timeout=120)
    subprocess.run(["git", "-C", str(release), "archive", "-o", "release.tar", "FETCH_HEAD", "plugins/long-running/lib", "plugins/long-running/.claude-plugin"], check=True)
    shutil.unpack_archive(release / "release.tar", release, filter="data")
    return release / "plugins" / "long-running" / "lib"


def test_the_previous_release_reads_and_saves_a_drive_this_release_saved(run: Callable[..., tuple[int, str]], demo: str, previous_release: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship")
    run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/7", "--job", "j1", *LEAD)
    saved = state.load(demo)
    subprocess.run([sys.executable, "-c", PREVIOUS_RELEASE_SAVE, str(previous_release)], check=True, timeout=60)
    assert state.load(demo) == saved | {"slack": saved["slack"] | {"page_stale": "the previous release's save"}}


OTHER_PROCESS_SAVE = """
import sys
sys.path.insert(0, sys.argv[1])
from drive import state


def saved(data):
    data["terminal"] = "the other process's terminal"
    data["orders"].pop("o2")


state.write("demo", saved)
"""


def ordered(name: str, count: int) -> Path:
    make(name)
    with edit(name) as data:
        for number in range(count):
            data["orders"][f"o{number}"] = state.new_order(f"order {number}", "slack", None, {}, state.stamp()) | {"kind": "build"}
    return state.database_path(name)


def other_process_save() -> None:
    subprocess.run([sys.executable, "-c", OTHER_PROCESS_SAVE, str(Path(state.__file__).resolve().parents[1])], check=True, timeout=30)


@pytest.mark.parametrize("count", [1, 1000 if os.environ.get("FORGE_CI_AT_SCALE") == "1" else 100])
def test_a_load_after_another_processs_save_reads_only_the_rows_that_save_wrote_at_any_drive_size_and_matches_a_fresh_read(state_root: Path, count: int) -> None:
    path = ordered("demo", count + 2)
    state.load("demo")
    statements: list[str] = []
    state.WHOLE_READS[(path, path.stat().st_ino)].watcher.set_trace_callback(statements.append)
    other_process_save()
    loaded = state.load("demo")
    assert (loaded["terminal"], "o2" in loaded["orders"], len(loaded["orders"])) == ("the other process's terminal", False, count + 1)
    assert loaded == state.shaped("demo", state.stored("demo"))[0]
    assert [statement.partition(" WHERE ")[0] for statement in statements if " rows" in statement] == ["SELECT collection, key, place, body FROM (SELECT DISTINCT collection, key FROM changed"]



@pytest.mark.parametrize("collecting", [True, False])
def test_cold_cached_and_batched_loads_skip_cyclic_scans_and_preserve_caller_collection(state_root: Path, monkeypatch: pytest.MonkeyPatch, collecting: bool) -> None:
    path = ordered("demo", 3)
    state.WHOLE_READS.pop((path, path.stat().st_ino), None)
    original = state.marshal.loads
    scans: list[bool] = []

    def decode(body: bytes) -> dict:
        scans.append(gc.isenabled())
        return original(body)

    monkeypatch.setattr(state.marshal, "loads", decode)
    was_collecting = gc.isenabled()
    (gc.enable if collecting else gc.disable)()
    try:
        first = state.load("demo")
        assert scans and not any(scans)
        assert gc.isenabled() == collecting
        scans.clear()
        second = state.load("demo")
        assert scans and not any(scans)
        assert gc.isenabled() == collecting
        first["orders"]["o0"]["text"] = "this copy alone"
        assert second["orders"]["o0"]["text"] == "order 0"
        with state.batch("demo"):
            scans.clear()
            third = state.load("demo")
            assert scans and not any(scans)
            assert gc.isenabled() == collecting
            assert third == second
        with pytest.raises(DriveError, match="no drive named absent"):
            state.load("absent")
        assert gc.isenabled() == collecting
    finally:
        (gc.enable if was_collecting else gc.disable)()


@pytest.mark.parametrize("fields", [("owner",), ("orders", "subcoordinators"), ()])
def test_selected_loads_decode_only_requested_fields_and_keep_independent_copies(state_root: Path, monkeypatch: pytest.MonkeyPatch, fields: tuple[str, ...]) -> None:
    path = ordered("demo", 3)
    for mark in (state.drive_dir("demo") / "pictures" / picture.SOURCED, state.drive_dir("demo") / "asks" / state.REBUILT_ASKS):
        mark.parent.mkdir(exist_ok=True)
        mark.touch()
    whole = state.load("demo")
    cached = state.WHOLE_READS[(path, path.stat().st_ino)].shaped
    decoded: list[bytes] = []
    loads = state.marshal.loads

    def decode(body: bytes) -> dict:
        decoded.append(body)
        return loads(body)

    monkeypatch.setattr(state.marshal, "loads", decode)
    selected = state.load("demo", fields)
    assert selected == {field: whole[field] for field in fields}
    assert decoded == [cached[field] for field in fields]
    if "orders" in selected:
        selected["orders"]["o0"]["text"] = "only this caller"
        assert state.load("demo", fields)["orders"]["o0"]["text"] == "order 0"


def test_selected_loads_observe_other_processes_saves_and_batch_writes(state_root: Path) -> None:
    ordered("demo", 3)
    fields = ("terminal", "orders")
    assert "o2" in state.load("demo", fields)["orders"]
    other_process_save()
    selected = state.load("demo", fields)
    assert selected["terminal"] == "the other process's terminal"
    assert "o2" not in selected["orders"]
    assert selected == {field: state.load("demo")[field] for field in fields}
    with state.batch("demo"):
        with edit("demo") as data:
            data["terminal"] = "this batch's terminal"
        assert state.load("demo", ("terminal",)) == {"terminal": "this batch's terminal"}
    assert state.load("demo", ("terminal",)) == {"terminal": "this batch's terminal"}


def test_a_write_outside_any_save_is_read_by_the_next_load_even_once_the_changed_table_dropped_its_entries(state_root: Path) -> None:
    path = ordered("demo", 3)
    state.load("demo")
    with closing(sqlite3.connect(path)) as db, db:
        order = json.loads(db.execute("SELECT body FROM rows WHERE collection = 'orders' AND key = 'o1'").fetchone()[0])
        db.execute("UPDATE rows SET body = ? WHERE collection = 'orders' AND key = 'o1'", (json.dumps(order | {"text": "a write the list dropped"}),))
        dropped = db.execute("SELECT MAX(rev) FROM changed").fetchone()[0]
    other_process_save()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM changed WHERE rev <= ?", (dropped,))
    loaded = state.load("demo")
    assert (loaded["orders"]["o1"]["text"], loaded["terminal"], "o2" in loaded["orders"]) == ("a write the list dropped", "the other process's terminal", False)
    assert loaded == state.shaped("demo", state.stored("demo"))[0]


def test_a_write_waiting_past_its_budget_on_the_database_changes_nothing_and_says_so(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    monkeypatch.setattr(state, "WRITE_BUDGET_SECONDS", 0.3)

    def terminal(data: dict) -> None:
        data["terminal"] = "term-1"

    stuck = sqlite3.connect(state.database_path("demo"), isolation_level=None)
    stuck.execute("BEGIN IMMEDIATE")
    with pytest.raises(DriveError, match=r"demo's database is still locked after 0\.3s, so this changed nothing$"):
        state.write("demo", terminal)
    stuck.close()
    assert state.load("demo")["terminal"] is None
    state.write("demo", terminal)
    assert state.load("demo")["terminal"] == "term-1"


def test_a_write_moving_a_drive_out_of_drive_json_waits_for_the_migrate_lock_no_longer_than_its_budget(state_root: Path) -> None:
    state.drive_dir("demo").mkdir(parents=True)
    state.state_path("demo").write_text(json.dumps(state.new_drive("demo", "coordinator", None, "/work")))

    def terminal(data: dict) -> None:
        data["terminal"] = "term-1"

    with open(state.migrate_lock("demo"), "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(DriveError, match=r"demo's migrate lock is still held after 0\.3s, so this moved nothing: "):
            state.write("demo", terminal, budget=0.3)
    assert not state.database_path("demo").exists()
    state.write("demo", terminal, budget=0.3)
    assert (state.database_path("demo").exists(), state.load("demo")["terminal"]) == (True, "term-1")


def test_a_batch_holds_no_lock_while_its_writes_run_and_its_one_save_keeps_what_another_write_changed_meanwhile(state_root: Path) -> None:
    make("demo")
    created = state.stamp()
    with state.batch("demo"):
        with edit("demo") as data:
            data["terminal"] = "term-1"
            data["orders"]["o1"] = state.new_order("order o1", "terminal", None, {"order o1": state.new_part()}, created)
        free = sqlite3.connect(state.database_path("demo"), timeout=0, isolation_level=None)
        free.execute("BEGIN IMMEDIATE")
        free.close()
        saved = state.stored("demo")
        saved["orders"]["o2"] = state.new_order("order o2", "terminal", None, {"order o2": state.new_part()}, created)
        state.save("demo", saved | {"binder": "term-2"})
    data = state.load("demo")
    assert (data["terminal"], data["binder"], list(data["orders"])) == ("term-1", "term-2", ["o2", "o1"])
    with state.batch("demo"):
        with edit("demo") as data:
            data["orders"]["o1"]["jobs"].append("j1")
        saved = state.stored("demo")
        saved["orders"]["o1"]["jobs"] += ["j2", "j1"]
        state.save("demo", saved)
    assert state.load("demo")["orders"]["o1"]["jobs"] == ["j2", "j1"]
    with pytest.raises(DriveError, match=r"refused, nothing kept: another write changed orders\.o1\.text differently while this call ran, so send it again$"), state.batch("demo"):
        with edit("demo") as data:
            data["orders"]["o1"]["text"] = "order o1, here"
        saved = state.stored("demo")
        saved["orders"]["o1"]["text"] = "order o1, there"
        state.save("demo", saved)
    assert state.load("demo")["orders"]["o1"]["text"] == "order o1, there"
    with pytest.raises(DriveError, match=r"so send it again once you have checked what it started as it ran: true$"), state.batch("demo"):
        with edit("demo") as data:
            data["terminal"] = "term-3"
        subprocess.run(["true"], check=True)
        state.save("demo", state.stored("demo") | {"terminal": "term-4"})
    assert state.load("demo")["terminal"] == "term-4"


def elsewhere(fields: dict) -> None:
    """Save fields on the demo drive from a thread of its own, as a concurrent writer does, outside the write whose change calls this, which a write of the same drive inside it would refuse."""

    def other() -> None:
        with edit("demo") as data:
            data.update(fields)

    with ThreadPoolExecutor(1) as pool:
        pool.submit(other).result()


def test_an_update_another_write_raced_runs_again_on_the_drive_as_it_then_stands(state_root: Path) -> None:
    make("demo")
    runs: list[str | None] = []

    def keep(data: dict) -> str:
        runs.append(data["binder"])
        if len(runs) == 1:
            elsewhere({"terminal": "theirs", "binder": "theirs too"})
        data["terminal"] = f"mine over {data['terminal']}"
        return data["terminal"]

    assert state.write("demo", keep) == "mine over theirs"
    assert (runs, state.load("demo")["terminal"], state.load("demo")["binder"]) == ([None, "theirs too"], "mine over theirs", "theirs too")
    assert not (state.drive_dir("demo") / "locks.log").exists()


def test_an_update_raced_on_every_run_runs_on_as_a_write_naming_no_rows(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    monkeypatch.setattr(state, "UPDATE_TRIES", 2)
    runs: list[str] = []

    def keep(data: dict) -> None:
        runs.append(data["terminal"] or "")
        if len(runs) <= 2:
            elsewhere({"terminal": f"theirs {len(runs)}"})
        data["terminal"] = "mine"

    state.write("demo", keep)
    assert (runs, state.load("demo")["terminal"]) == (["", "theirs 1", "theirs 2"], "mine")


def test_an_update_changing_a_row_it_did_not_name_runs_again_as_a_write_naming_no_rows(state_root: Path) -> None:
    make("demo")

    def keep(data: dict) -> None:
        data["terminal"] = "term-1"
        data["binder"] = "term-2"

    state.write("demo", keep)
    assert (state.load("demo")["terminal"], state.load("demo")["binder"]) == ("term-1", "term-2")


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
    with edit("demo") as data:
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
    with edit("demo") as data:
        data["jobs"]["j1"].update(state="running", said=None)
    state.migrate("demo")
    assert state.load("demo")["jobs"]["j1"]["state"] == "running"


def test_migrate_reopens_once_the_question_jev_folded_into_another_prs_hotfix_question_his_answer_taken_off_its_job_and_recorded_under_the_question_he_answered(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo")
    monkeypatch.setattr(state, "JEV_FOLD", ("demo", "j1", "j1-ec812baf.md"))
    retitled: list[tuple[str, str, str]] = []
    monkeypatch.setattr(settled, "retitled", lambda answer, title, cwd: retitled.append((answer["id"], title, cwd)) or title)
    link = "https://acme.slack.com/archives/D123/p1794739468345719"
    noted = f"0a3bed1{'0' * 33}"
    answered = "Should the hotfix of this fix alone start now?"
    with edit("demo") as data:
        data["jobs"] |= {job_id: state.new_job("o1", "builder", "ship it", [], state.stamp()) for job_id in ("j1", "j2")}
        for job_id, file, title, replaced in (("j1", "/asks/j1-ec812baf.md", "Will you grant one exception to your release hold?", "/asks/j2-fc6d3e9d.md"), ("j2", "/asks/j2-fc6d3e9d.md", answered, None)):
            closed = state.new_close("answered", "Start it", link, seq="721471", noted=noted)
            data["jobs"][job_id]["questions"] = [state.new_question(file, title, state.stamp(), card=link) | {"waits": "demo:j1-1", "replaced": replaced, "closed": closed}]
        data["jobs"]["j1"]["undelivered"] = [state.kept_text("His answer on card", state.HIM, seq="721471"), state.kept_text("Ship it", state.HIM, seq="721470")]
    for _ in range(2):
        state.migrate("demo")
        jobs = state.load("demo")["jobs"]
        assert [(question["closed"] and question["closed"]["how"], question["replaced"], question["card"], question["waits"]) for job_id in ("j1", "j2") for question in jobs[job_id]["questions"]] == [
            (None, None, None, "demo:j1-7"),
            ("answered", None, link, "demo:j1-1"),
        ]
        assert [kept["seq"] for kept in jobs["j1"]["undelivered"]] == ["721470"]
        assert retitled == [(noted, answered, "/work")]


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
    with edit("demo") as data:
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
    with edit("demo") as drive:
        assert {job_id: [kept["seq"] for kept in job["undelivered"]] for job_id, job in drive["jobs"].items()} == {"j1": ["699068", "699111"], "j2": ["699111"], "j3": ["699111"]}
        assert drive["holds_owed"] is True


def test_a_drive_saved_with_his_message_repeated_in_an_answer_keeps_it_once_and_marks_sent_each_listed_job_given_it_once(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["messages_kept_once"]
    said, retro = "build it from scratch, reusing none of the existing HA code", "and do a self retro"
    data["jobs"] = {job_id: state.new_job("o1", "builder", "build it", [], "2026-11-15T03:00:00-08:00") | {"agent": f"agent-{job_id}"} for job_id in ("j1", "j2")}
    at = state.stamp()
    data["answers"] = {
        answer_id: {"words": words, "link": "https://acme.slack.com/archives/D1/p1794742432520639", "at": at, "said": at, "cards": {}, "jobs": {"j1": None, "j2": None}}
        for answer_id, words in (("4177bd8", " … ".join([said] * 3)), ("5a1e0f2", f"{said} … {retro}"))
    }
    state.create(data)
    (state.drive_dir("demo") / "jobs").mkdir(exist_ok=True)
    (state.drive_dir("demo") / "jobs" / "j1-2.md").write_text(f'His words, verbatim: "{said}"')
    drive = state.load("demo")
    assert ({answer_id: (kept["words"], kept["jobs"]) for answer_id, kept in drive["answers"].items()}, drive["messages_kept_once"]) == (
        {"4177bd8": (said, {"j1": {"mark": "sent"}, "j2": None}), "5a1e0f2": (f"{said} … {retro}", {"j1": None, "j2": None})},
        True,
    )

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
    with edit("demo") as drive:
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


def test_j6083_a_drive_saved_with_unstarted_jobs_waiting_on_earlier_changes_prs_waits_on_their_jobs_again_and_on_each_earlier_changes_builder_its_design_lists(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["change_waits_stacked"]
    moment = "2026-11-15T04:00:00-08:00"
    first, second, merged, outside = (f"yasyf/cc-skills#{number}" for number in (1, 2, 3, 4))
    changes = ["the interface", "the reads", "the cache", "the writes"]
    data["orders"] = {
        "o1": state.new_order("copy the store", "terminal", None, {part: state.new_part() for part in ["copy the store", *changes]}, moment) | {"jobs": ["j1", "j2", "j3", "j4", "j5", "j6"]},
        "o2": state.new_order("the other store", "terminal", None, {"the other store": state.new_part()}, moment) | {"jobs": ["j7"]},
    }
    waits = [first, second, outside]
    data["jobs"] = {
        "j1": state.new_job("o1", "design", "cards: the copy", ["copy the store"], moment) | {"state": "done"},
        "j2": state.new_job("o1", "builder", "build j1's change: the interface", ["the interface"], moment) | {"state": "built", "agent": "a2", "prs": [state.pr_url(first)]},
        "j3": state.new_job("o1", "builder", "build j1's change: the reads", ["the reads"], moment) | {"state": "built", "agent": "a3", "prs": [state.pr_url(merged), state.pr_url(second)]},
        "j4": state.new_job("o1", "builder", "build j1's change: the writes", ["the writes"], moment) | {"waits": waits},
        "j5": state.new_job("o1", "builder", "build the cache", ["the cache"], moment),
        "j6": state.new_job("o1", "builder", "test the copy on staging", [], moment) | {"state": "waiting", "waits": [first, merged], "said": "waits on them"},
        "j7": state.new_job("o2", "builder", "build the other store", ["the other store"], moment) | {"state": "built", "agent": "a7", "prs": [state.pr_url(outside)]},
    }
    data["prs"] = {key: state.new_pr(state.pr_url(key), job_id, order_id) for key, job_id, order_id in ((first, "j2", "o1"), (second, "j3", "o1"), (merged, "j3", "o1"), (outside, "j7", "o2"))}
    data["prs"][merged]["state"] = "merged"
    state.create(data)
    listed = "".join(f"Change: {change}\n" for change in changes[:3]) + f"Change: {changes[3]} (waits on change 1 and change 2 and change 3)\n"
    state.keep_report("demo", "j1", "DESIGN copy the store in four changes", f"DESIGN copy the store in four changes\n{listed}")
    drive = state.load("demo")
    assert {job_id: (job["state"], job["waits"], job["said"]) for job_id, job in drive["jobs"].items() if job_id in ("j4", "j5", "j6")} == {
        "j4": ("waiting", ["j2", "j3", outside, "j5"], state.NOT_STARTED),
        "j5": ("waiting", [], state.NOT_STARTED),
        "j6": ("waiting", ["j2", merged], "waits on them"),
    }
    assert drive["change_waits_stacked"] is True


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
        ruling: kept | {"at": early, "said": late, "cards": {"demo:j1-1": dropped, "demo:j1-2": {"mark": "stands"}}, "jobs": {"j1": {"mark": "sent"}}, "approves": None},
        other: kept | {"at": late, "said": late, "cards": {}, "jobs": {}, "approves": None},
    }
    assert (drive["jobs"]["j1"]["questions"][0]["closed"]["ruling"], drive["jobs"]["j1"]["brief_flags"]["ruling"]) == (other, [ruling, "deadbee"])
    with edit("demo"):
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
    checker = {"head": "abc", "pid": 1, "at": taken, "orders": None, "answers": None, "outcome": "NOT READY", "against": ""}
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
    with edit("demo"):
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
    passed = {"head": "abc", "pid": 1, "at": asked, "orders": ["o1"], "answers": [], "outcome": "CHECKED against the words of o1 at abc", "against": ""}
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
    with edit("demo") as data:
        data["prs"]["yasyf/cc-skills#1"]["answers"] = {"cited": [cited], "near": [near]}
    assert (state.load("demo")["prs"]["yasyf/cc-skills#1"]["answers"], state.load("demo")["near_answers_apart"]) == ({"cited": [cited], "near": [near]}, True)


def test_a_drive_saved_with_a_stack_refused_for_a_ruling_write_its_own_pr_makes_drops_each_open_prs_refusal_once_so_the_stack_is_checked_again_with_no_push(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["stack_writes_read"]
    at = "2026-11-15T00:40:00-08:00"
    owes = f'c0de123 "Key the cache on the run id": Forge-AI/monorepo#1 changes go/sand/cache/key.go, under go/sand/cache, and no AGENTS.md at or above go/sand/cache holds these words of his at the tip of its base branch, at the head of another open PR or in its diff; add them verbatim with their date to the AGENTS.md nearest go/sand/cache, by his ruling {served.WRITES_RULING}, "{served.WRITES_WORDS}"'
    refused = {"head": "abc", "pid": 1, "at": at, "orders": ["o1"], "answers": [], "outcome": f"NOT READY: Forge-AI/monorepo#1, Forge-AI/monorepo#2 go against his words: {owes}", "against": owes}
    judged = refused | {"outcome": 'NOT READY: Forge-AI/monorepo#3 go against his words: o1 "one store": adds a second store', "against": 'o1 "one store": adds a second store'}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "build it", [], at)}
    data["prs"] = {
        f"Forge-AI/monorepo#{number}": state.new_pr(f"https://github.com/Forge-AI/monorepo/pull/{number}", "j1", "o1") | {"state": status, "checker": checker}
        for number, status, checker in ((1, "open", refused), (2, "open", refused), (3, "open", judged), (4, "merged", refused))
    }
    state.create(data)
    drive = state.load("demo")
    assert ([pr["checker"] for pr in drive["prs"].values()], drive["stack_writes_read"]) == ([None, None, judged | {"faulted": []}, refused | {"faulted": []}], True)
    with edit("demo") as data:
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


def test_a_drive_saved_before_proof_results_marked_marks_each_proof_that_moved_its_job_off_its_line_and_names_when_own_done_proofs_began(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["proof_results_marked"], data["own_done_proofs_since"]
    ran, blocked = "DONE checked it by hand", "BLOCKED j1: repro/j1.sh exits 1"
    proof = {"pid": 1, "at": state.stamp(), "said": ran, "version": "1.0.1170"}
    data["orders"] = {"o1": state.new_order("fix it", "slack", None, {"fix": state.new_part()}, state.stamp()) | {"kind": "build", "jobs": ["j1", "j2", "j3"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "fail it", ["fix"], state.stamp()) | {"state": "failed", "said": blocked, "proof": proof},
        "j2": state.new_job("o1", "builder", "end it", ["fix"], state.stamp()) | {"state": "done", "said": ran, "proof": proof},
        "j3": state.new_job("o1", "builder", "wait on him", ["fix"], state.stamp()) | {"state": "waiting", "said": "WAITING his next word", "proof": proof | {"waits": "WAIT he answers"}},
    }
    state.create(data)
    loaded = state.load("demo")
    assert [loaded["jobs"][job_id]["proof"].get("result") for job_id in ("j1", "j2", "j3")] == [blocked, None, None]
    assert (loaded["own_done_proofs_since"], loaded["proof_results_marked"], state.new_drive("fresh", "coordinator", None, "/work")["own_done_proofs_since"]) == ("2026-11-15T06:49:27-08:00", True, None)


def test_a_drive_saved_before_work_proofs_owned_keeps_work_delivered_only_where_drive_job_prove_proved_it_on_its_newest_job_and_puts_back_a_superseded_job_its_proof_failed(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["work_proofs_owned"]
    data |= {"proofs_posted": True, "proofs_proven": True}
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
    data["prs"] = {"yasyf/cc-skills#7": state.new_pr(link, "j1", "o1") | {"state": "merged", "merged_sha": "abc123", "head": "abc123"}}
    state.create(data)
    loaded = state.load("demo")
    assert {part: loaded["orders"]["o1"]["parts"][part]["posted"] for part in parts} == {proven: proof, merged: None, ended: None, staging: build, moved: None}
    assert [(loaded["jobs"][job_id]["state"], loaded["jobs"][job_id]["said"]) for job_id in ("j4", "j5")] == [("done", pinned), ("failed", "BLOCKED j5: repro/j5.sh exits 1")]
    assert [loaded["orders"]["o1"]["parts"][part]["posted_by"] for part in (proven, merged, ended, moved)] == ["j1", None, None, None]
    assert (loaded["orders"]["o2"]["parts"][answered]["posted"], loaded["work_proofs_owned"], "proofs_posted" in loaded, "proofs_proven" in loaded) == (post, True, False, False)


def test_a_drive_saved_before_carried_proofs_delivered_delivers_each_part_a_passing_proofs_done_owes_on_an_order_its_job_carries_and_none_a_handoff_holds(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["carried_proofs_delivered"]
    link = "https://github.com/yasyf/cc-skills/pull/7"
    proof = f"{link} live: repro/j1.sh exits 0 on long-running@skills 1.0.3"
    data["orders"] = {
        "o1": state.new_order("fix it", "slack", None, {"fix": state.new_part() | {"posted": proof, "posted_by": "j1"}}, state.stamp()) | {"kind": "build", "jobs": ["j1"]},
        "o2": state.new_order("carry it", "slack", None, {"carried": state.new_part(), "handed": state.new_part()}, state.stamp()) | {"kind": "build", "jobs": ["j1", "j2"]},
    }
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "fix it", ["fix"], state.stamp()) | {"state": "done", "said": f"DONE {proof}", "prs": [link], "carries": {"o2": ["carried"]}},
        "j2": state.new_job("o2", "builder", "hand it", ["handed"], state.stamp()) | {"state": "done", "said": "HANDOFF j2: the rest waits on a prover"},
    }
    data["prs"] = {"yasyf/cc-skills#7": state.new_pr(link, "j1", "o1") | {"state": "merged", "merged_sha": "abc123", "head": "abc123"}}
    state.create(data)
    loaded = state.load("demo")
    parts = loaded["orders"]["o2"]["parts"]
    assert ([(parts[part]["posted"], parts[part]["posted_by"]) for part in ("carried", "handed")], loaded["carried_proofs_delivered"]) == ([(proof, "j1"), (None, None)], True)


def test_j5802_a_part_a_proof_delivered_reads_undelivered_while_a_newer_builder_holds_it_until_that_builders_own_proof_passes(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    part = "the fix"
    data["orders"] = {"o1": state.new_order("fix it", "slack", None, {part: state.new_part()}, state.stamp()) | {"kind": "build", "jobs": ["j1"]}}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "fix it", [part], state.stamp()) | {"state": "done", "said": "DONE fix it"}}
    state.deliver_proven(data, "j1", "j1 live: repro/j1.sh exits 0 on long-running@skills 1.0.3")
    assert (state.delivered(data, "o1", part), data["orders"]["o1"]["parts"][part]["posted_by"]) == (True, "j1")
    data["orders"]["o1"]["jobs"].append("j2")
    data["jobs"]["j2"] = state.new_job("o1", "builder", "fix it again", [part], state.stamp())
    state.deliver_proven(data, "j1", "j1 live: repro/j1.sh exits 0 on long-running@skills 1.0.4")
    assert (state.delivered(data, "o1", part), state.owed_work(data, "j1"), state.owed_work(data, "j2")) == (False, [], [("o1", part)])
    assert data["orders"]["o1"]["parts"][part]["posted"] == "j1 live: repro/j1.sh exits 0 on long-running@skills 1.0.3"
    data["jobs"]["j2"].update(state="failed", said="BLOCKED repro/j2.sh exits 1")
    assert not state.delivered(data, "o1", part)
    data["orders"]["o1"]["jobs"].append("j3")
    data["jobs"]["j3"] = state.new_job("o1", "builder", "prove it", [part], state.stamp()) | {"state": "done", "said": "DONE proved"}
    state.deliver_proven(data, "j3", "j3 live: repro/j3.sh exits 0 on long-running@skills 1.0.5")
    assert (state.delivered(data, "o1", part), data["orders"]["o1"]["parts"][part]["posted_by"], state.owed_work(data, "j3")) == (True, "j3", [])
    data["jobs"]["j3"]["state"] = "stopped"
    assert not state.delivered(data, "o1", part)


def test_the_post_that_answered_a_part_stops_delivering_it_while_a_builder_takes_it_on_as_work_which_only_its_passing_proof_delivers(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    created = "2026-11-14T10:00:00-08:00"
    part, post, proof = "why it broke", "https://acme.slack.com/archives/D123/p1794200040000500", "j1 live: repro/j1.sh exits 0 on long-running 1.0.3"
    data["orders"] = {"o1": state.new_order("fix it", "slack", None, {part: state.new_part() | {"answer": True, "posted": post}}, created) | {"kind": "build", "jobs": ["j1"]}}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "fix what broke", [part], created) | {"state": "stopped"}}
    answered = state.delivered(data, "o1", part)
    data["jobs"]["j1"]["state"] = "running"
    taken = (state.delivered(data, "o1", part), state.owed_work(data, "j1"))
    data["jobs"]["j1"]["state"] = "done"
    done = state.delivered(data, "o1", part)
    state.deliver_proven(data, "j1", proof)
    assert [answered, taken, done, (state.delivered(data, "o1", part), data["orders"]["o1"]["parts"][part]["posted"])] == [True, (False, [("o1", part)]), False, (True, proof)]


def test_a_drive_saved_before_old_dones_kept_puts_back_each_done_of_its_own_from_before_post_proofs_whose_first_proof_failed_and_leaves_the_others_to_their_proof(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["old_dones_kept"]
    data["own_done_proofs_since"] = state.stamp(state.POST_PROOFS_MERGED)
    flipped, unproven, since = "the flipped fix", "the unproven fix", "the fix since the rule"
    blocked = "BLOCKED j1: repro/j1.sh exits 1 on long-running 1.0.1170, its output reports/j1.proof.txt: it printed no FAIL line"
    data["orders"] = {"o1": state.new_order("fix it", "slack", None, {part: state.new_part() for part in (flipped, unproven, since)}, state.stamp()) | {"kind": "build", "jobs": ["j1", "j2", "j3"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "fix it", [flipped], state.stamp()) | {"state": "failed", "said": blocked, "proof": {"pid": 1, "at": state.stamp(), "said": "DONE checked by hand", "version": "1.0.1170"}},
        "j2": state.new_job("o1", "codex", "end it", [unproven], state.stamp()) | {"state": "done", "said": "DONE ended by hand"},
        "j3": state.new_job("o1", "builder", "fix it since", [since], state.stamp()) | {"state": "failed", "said": blocked, "proof": {"pid": 1, "at": state.stamp(), "said": "DONE checked since", "version": "1.0.1170"}},
    }
    state.create(data)
    for job_id, line, at in (
        ("j1", "DONE checked by hand", "2026-11-12T17:26:26-08:00"),
        ("j1", blocked, "2026-11-15T07:07:57-08:00"),
        ("j2", "DONE ended by hand", "2026-11-13T09:00:00-08:00"),
        ("j3", "DONE checked since", "2026-11-15T07:30:00-08:00"),
        ("j3", blocked, "2026-11-15T07:31:00-08:00"),
    ):
        state.keep_report("demo", job_id, line, f"{line}\n", state.parse_time(at))
    loaded = state.load("demo")
    assert [(loaded["jobs"][job_id]["state"], loaded["jobs"][job_id]["said"]) for job_id in ("j1", "j2", "j3")] == [("done", "DONE checked by hand"), ("done", "DONE ended by hand"), ("failed", blocked)]
    assert (install.done_before_proofs(loaded, "j1"), install.done_unproven(loaded, "j1")) == (True, False)
    assert ({part: fields["posted"] for part, fields in loaded["orders"]["o1"]["parts"].items()}, loaded["old_dones_kept"]) == ({flipped: None, unproven: None, since: None}, True)


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
    base = {"order": "o1", "state": "running", "said": None, "updated": "2026-11-11T08:00:00-08:00", "where": "remote"}
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
    data = {"slack": {"channels": {}}}
    order = state.new_order("ship the release", "slack:C0B75AL4XEW/1794436805.002349", tuple(release), {}, at)
    kept = [["C0B75AL4XEW", "1794436900.000100", at, "posted"], ["D123", "1794437000.000200", at, "question"]]
    assert [
        state.promise_thread(data, order),
        state.promise_thread(data, order | {"threads": kept}),
        state.promise_thread(data, order | {"threads": kept, "incident": True}),
        state.promise_thread(data, order | {"thread": ["D123", "1794437100.000300"], "threads": kept}),
        state.promise_thread(data, order | {"thread": None, "threads": kept}),
    ] == [None, ("D123", "1794437000.000200"), tuple(release), ("D123", "1794437100.000300"), None]


def test_while_the_drive_keeps_channels_an_orders_lines_go_in_a_thread_of_one_of_them_else_at_its_seats_channel_root_never_his_dm() -> None:
    at = "2026-11-13T10:00:00-08:00"
    data = {"slack": {"channels": {"": {"id": "C100", "name": "him-drive-demo"}, "Iris": {"id": "C200", "name": "him-drive-demo-iris"}}}}
    his = ["D123", "1794437100.000300"]
    order = state.new_order("index the tables", "slack:D123/1794437100.000300", tuple(his), {}, at)
    started = [["C200", "1794437200.000400", at, "posted"]]
    alert = ["C0B75AL4XEW", "1794436805.002349"]
    assert [
        state.promise_thread(data, order),
        state.promise_thread(data, order | {"threads": started}),
        state.promise_thread(data, order | {"thread": ["C200", "1794437300.000500"]}),
        state.promise_thread(data, order | {"thread": alert, "incident": True}),
        state.promise_thread(data, order | {"incident": True}),
        state.promise_thread(data, order | {"incident": True, "threads": started}),
    ] == [None, ("C200", "1794437200.000400"), ("C200", "1794437300.000500"), tuple(alert), None, ("C200", "1794437200.000400")]
    assert state.thread_attr(data, order | {"threads": started}) == {"thread": "1794437200.000400", "channel": "C200"}
    assert state.thread_attr(data, order) == {}


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
    with pytest.raises(RuntimeError), edit("demo") as data:
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
    with edit("other") as data:
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
    state.save("demo", saved)
    with edit("demo") as data:
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
    with edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "iris", "terminal": None, "cwd": "/work", "started": "2026-11-11T02:00:00-08:00"}
        for order_id, text, project in (("o1", "ship the run page", "Iris"), ("o2", "fix the watch", "The drive"), ("o3", "Drive tooling: fix the wake", None)):
            data["orders"][order_id] = state.new_order(text, "terminal", None, {}, "2026-11-11T02:00:00-08:00") | {"project": project}
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
    with edit("demo") as data:
        for order_id, project in (("o1", "Iris"), ("o2", "Iris"), ("o3", None), ("o4", "Iris")):
            data["orders"][order_id] = state.new_order(f"order {order_id}", "terminal", None, {f"order {order_id}": state.new_part()}, created) | {"project": project}
        data["orders"]["o4"]["state"] = "done"
    start = state.events_end("demo")
    with edit("demo") as data:
        data["orders"]["o3"]["text"] = "a changed order"
    assert list(state.read_events("demo", start)) == []
    with edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "iris", "terminal": None, "cwd": "/work", "started": created}
        data["orders"]["o5"] = state.new_order("order o5", "terminal", None, {"order o5": state.new_part()}, created) | {"project": "Iris"}
    with state.batch("demo"):
        with edit("demo") as data:
            data["orders"]["o3"]["owner"] = "meta"
        with edit("demo") as data:
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
    assert (data["jobs"]["j9"]["waits"], data["jobs"]["j10"]["waits"], state.card_wait(data, build)) == ([build], [started], False)


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
    pr["tap"] = {"label": "Approve", "at": state.stamp(REFERENCE), "head": "abc", "source": "slack", "picks": {}, "card": None}
    state.rework(pr, "o227", "Topology should be local metadata", "https://acme.slack.com/archives/D123/p1794300000000600")
    assert (bool(pr["rework"]), state.asks_tap(data, pr), pr["tap"]["label"]) == (True, False, "Approve")
    state.take_result(data, "j9", "READY #4289", "READY #4289\nPR: #4289\nCHANGE: topology moves to the session's meta table")
    assert (stage.carded(pr), pr["rework"], pr["tap"], state.asks_tap(data, pr)) == (True, None, None, True)
    assert pr["fundamental"] == 'Reworked to your words "Topology should be local metadata": topology moves to the session\'s meta table'


def test_load_drops_a_tap_standing_beside_a_fundamental_change_so_his_fresh_card_asks_it_and_keeps_every_other_tap(state_root: Path) -> None:
    data = kept_prs()
    for number in (4289, 4279):
        data["prs"][f"{MONOREPO}#{number}"]["tap"] = state.new_tap("Approve", "abc", "slack", at=state.stamp(REFERENCE))
    data["prs"][f"{MONOREPO}#4289"]["fundamental"] = 'Reworked to your words "Own settings key per value": one indexed row per read'
    data["jobs"]["j622"] = state.new_job("o226", "builder", "fix it", [], state.stamp(REFERENCE))
    state.create(data)
    loaded = state.load("demo")
    kept = loaded["prs"]
    assert (kept[f"{MONOREPO}#4289"]["tap"], kept[f"{MONOREPO}#4279"]["tap"]["label"], state.asks_tap(loaded, kept[f"{MONOREPO}#4289"])) == (None, "Approve", True)


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
    state.save("demo", saved)
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
    with edit("demo") as drive:
        assert drive["members_kept"] is True
    said(staging, f"https://acme.slack.com/archives/{staging}/p1794695000000100")
    assert [[*where[:2], where[3]] for where in state.load("demo")["orders"]["o1"]["threads"]] == kept


def test_a_drive_saved_with_a_builders_done_on_its_own_open_pr_puts_it_back_waiting_on_that_pr_once_its_staging_test_delivered_by_its_first_check_proof_as_j1308s_was_not(
    named_repo: None,
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
    with edit("demo") as drive:
        assert drive["open_dones_held"] is True


def test_a_drive_saved_with_live_readers_holding_prs_hands_each_to_the_builder_on_its_part_and_sets_each_reader_done_once(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["reader_prs_handed"]
    created = "2026-11-15T10:00:00-08:00"
    handed, done, alone = (f"https://github.com/yasyf/cc-skills/pull/{number}" for number in (5470, 5471, 5472))
    data["orders"] = {
        "o1": state.new_order("stop the relaunch", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {"stop it": state.new_part()}, created) | {"kind": "build", "jobs": ["j1", "j2", "j3"]},
        "o2": state.new_order("answer it", "slack:D123/1794600000.000200", ("D123", "1794600000.000200"), {"answer it": state.new_part()}, created) | {"kind": "build", "jobs": ["j4"]},
    }
    data["jobs"] = {
        "j1": state.new_job("o1", "reader", "research: the relaunch", ["stop it"], created) | {"state": "waiting", "said": f"PUSHED {handed}", "waits": [state.pr_key(handed)], "prs": [handed]},
        "j2": state.new_job("o1", "builder", "stop it", ["stop it"], created) | {"state": "running"},
        "j3": state.new_job("o1", "reader", "research: the relaunch", ["stop it"], created) | {"state": "done", "said": f"PUSHED {done}", "prs": [done]},
        "j4": state.new_job("o2", "reader", "answer it", ["answer it"], created) | {"state": "running", "said": f"PUSHED {alone}", "prs": [alone]},
    }
    data["prs"] = {state.pr_key(url): state.new_pr(url, job_id, order_id) | {"state": "merged"} for url, job_id, order_id in ((handed, "j1", "o1"), (done, "j3", "o1"), (alone, "j4", "o2"))}
    state.create(data)
    data = state.load("demo")
    jobs = data["jobs"]
    assert (
        [data["prs"][state.pr_key(url)]["job"] for url in (handed, done, alone)],
        jobs["j2"]["prs"],
        [(jobs[job_id]["state"], jobs[job_id]["waits"], jobs[job_id]["said"]) for job_id in ("j1", "j4")],
        data["reader_prs_handed"],
    ) == (["j2", "j3", "j4"], [handed], [("done", [], f"PUSHED {handed}"), ("done", [], f"PUSHED {alone}")], True)


def test_a_drive_saved_with_codex_proofs_copied_away_from_their_evidence_keeps_each_where_its_done_names_it_and_puts_back_on_that_done_a_job_its_copy_failed_once(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["proofs_rehomed"]
    created, done = "2026-11-15T10:00:00-08:00", "DONE 1,216 PRs checked at 27c34883bf69: 0 gaps; no readers added"
    proofs = {job_id: state.drive_dir("demo") / "scratch" / job_id / "proof.py" for job_id in ("j1", "j2")}
    for job_id, proof in proofs.items():
        proof.parent.mkdir(parents=True)
        proof.write_text("#!/bin/sh\n")
        state.repro_path("demo", job_id, ".py").parent.mkdir(exist_ok=True)
        shutil.copy(proof, state.repro_path("demo", job_id, ".py"))
        state.keep_report("demo", job_id, done, f"{done}\nREPRO: {proof}\n")
    failed = {"pid": 1, "at": created, "said": done, "version": "1.0.1", "result": "BLOCKED j1: repro/j1.py exits 1"}
    data["orders"] = {"o1": state.new_order("keep the ports moving", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {"old-tree ports": state.new_part()}, created) | {"kind": "build", "jobs": ["j1", "j2", "j3"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "codex", "the old-tree ports keep moving", ["old-tree ports"], created) | {"state": "stopped", "said": "replaced by j3", "repro": str(state.repro_path("demo", "j1", ".py")), "proof": failed},
        "j2": state.new_job("o1", "codex", "the old-tree ports keep moving", ["old-tree ports"], created) | {"state": "done", "said": done, "repro": str(state.repro_path("demo", "j2", ".py"))},
        "j3": state.new_job("o1", "codex", "the old-tree ports keep moving", ["old-tree ports"], created) | {"state": "running"},
    }
    state.create(data)
    data = state.load("demo")
    assert ([(data["jobs"][job_id]["state"], data["jobs"][job_id]["said"], data["jobs"][job_id]["repro"], data["jobs"][job_id].get("proof")) for job_id in proofs], data["proofs_rehomed"]) == (
        [("done", done, str(proof), None) for proof in proofs.values()],
        True,
    )


def test_a_drive_saved_with_a_release_follower_failed_by_proving_its_done_on_a_merged_prs_part_a_newer_prover_holds_puts_it_back_on_that_done_once_and_never_proves_it_again(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["unowed_fails_undone"]
    created, work, owned, staging = "2026-11-15T10:00:00-08:00", "Duck route writes the slow-statement log", "ship the dashboard", "staging test of #56775 before it lands"
    merged, done = "https://github.com/Forge-AI/monorepo/pull/56775", "DONE https://buildkite.com/forge/release/builds/16698 applied sandsql/plat-usw2-staging at `2026-11-15T16:40:09Z`"
    failed = "BLOCKED j2: repro/j2.sh exits 1 on long-running 1.0.1244: FAIL sandsql/plat-usw2-staging: serves 3ded868a2f0d, not c5dca80ed9be"
    proof = {"pid": 1, "at": created, "said": done, "version": "1.0.1244", "result": failed}
    released = {"release": "tools/ci release start sandsql --emergency --env plat-usw2-staging --from c5dca80ed9be --order 23eefb9"}
    data["orders"] = {
        "o1": state.new_order("duck route logs", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {work: state.new_part(), staging: state.new_part()}, created) | {"kind": "build", "jobs": ["j1", "j2", "j3"]},
        "o2": state.new_order("ship the dashboard", "slack:D123/1794600000.000200", ("D123", "1794600000.000200"), {owned: state.new_part()}, created) | {"kind": "build", "jobs": ["j4"]},
    }
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", f"build {work}", [work], created) | {"state": "done", "said": f"DONE {merged} merged: Forge-AI/monorepo#56775 at 4a8aa7725584", "prs": [merged]},
        "j2": state.new_job("o1", "builder", "release c5dca80ed9be, the head of Forge-AI/monorepo#56775, to staging's SandSQL alone", [staging, work], created)
        | {"state": "failed", "said": failed, "proof": proof, "brief_flags": released, "builds_nothing": True},
        "j3": state.new_job("o1", "builder", "prove Forge-AI/monorepo#56775 live", [work], created) | {"state": "running", "builds_nothing": True},
        "j4": state.new_job("o2", "builder", owned, [owned], created) | {"state": "failed", "said": failed.replace("j2", "j4"), "proof": proof | {"result": failed.replace("j2", "j4")}},
    }
    data["prs"] = {state.pr_key(merged): state.new_pr(merged, "j1", "o1") | {"state": "merged", "merged_sha": "4a8aa7725584"}}
    state.create(data)
    data = state.load("demo")
    jobs = data["jobs"]
    assert ([(jobs[job_id]["state"], jobs[job_id]["said"], jobs[job_id]["proof"]["result"]) for job_id in ("j2", "j4")], data["unowed_fails_undone"]) == (
        [("done", done, failed), ("failed", failed.replace("j2", "j4"), failed.replace("j2", "j4"))],
        True,
    )
    assert (state.owed_work(data, "j2"), install.watch_proves(data, "j2"), install.prover_of(data, state.pr_key(merged))) == ([], False, "j3")


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
    ("cases", "read"),
    [
        ("CASE a rollback refused: pass\nCASE a write lands: PASS on the cut\n", (True, PROOF, "j1")),
        ("CASE a rollback refused: pass\nCASE a write lands: fail: the writer kept the old row\n", (False, None, "j1")),
        ("CASE a rollback refused: pass\nCASE a write lands: not exercised: staging signs in as one account\n", (True, PROOF, "j1")),
        ("CASE a write lands: not exercised\nCASE a rollback refused: not exercised: this job only followed the release\n", (False, None, None)),
        ("CASE a write lands: skipped\n", (False, None, "j1")),
        ("Case 5 a write lands: fail: the writer kept the old row\n", (False, None, None)),
        ("CASE line sent to root on topic staging-slots {at:718168}\nCASE a write on staging:plat-usw2-staging lands: pass\n", (True, PROOF, "j1")),
        ('CASE rollback semantics: the release reads mode dry-run, rollback true: pass\nCASE the key published ("ci-go: linux-amd64 is already published"): pass\n', (True, PROOF, "j1")),
        ("CASE B the first blob mint on lapsed keys: pass: run run_01m7 on the soak\n", (True, PROOF, "j1")),
        ("CASE rollback semantics: the release reads mode dry-run: fail: rollback false\n", (False, None, "j1")),
        ("CASE 1 pass (W2m c2475b75c178): all 10 backfill-pass CronJobs run the cut's image\nCASE 3 pass (D1b 9c560ac70113, #42 alone on dev): the dry run succeeded\n", (True, PROOF, "j1")),
        ("CASE 1 pass (W2m c2475b75c178): all 10 backfill-pass CronJobs run the cut's image\nCASE 2 fail (W2m): the dry run logged no run\n", (False, None, "j1")),
    ],
    ids=["every-case-passes", "a-case-fails", "a-case-not-exercised", "every-case-not-exercised", "a-case-skipped", "no-case-line", "a-prose-line-opening-case", "a-verdict-after-what-ran", "a-verdict-before-what-ran", "a-failing-verdict-after-what-ran", "verdicts-in-the-case-name-over-two-cuts", "a-failing-verdict-in-the-case-name"],
)
def test_a_staging_test_part_is_delivered_by_its_testers_done_only_when_a_case_passes_and_each_other_passes_or_was_not_exercised_as_j164s_was_not(
    named_repo: None,
    state_root: Path, cases: str, read: tuple[bool, str | None, str | None]
) -> None:
    data = staging_drive("j1")
    staging_return(data, "j1", "DONE ran the staging test of #42 on cut 1", cases)
    assert (data["jobs"]["j1"]["state"], staging_reads(data)) == ("done", read)


def test_a_staging_test_parts_full_pass_holds_only_while_its_tester_still_holds_that_done_and_no_newer_tester_is_at_work_or_failed(named_repo: None, state_root: Path) -> None:
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
        (False, None, "j1"),
        (True, newer, "j2"),
        (False, None, "j2"),
    ]



@pytest.mark.parametrize(
    ("cases", "tap"),
    [
        ("CASE a rollback refused: pass\nCASE a write lands: not exercised: staging signs in as one account\n", None),
        ("CASE a rollback refused: pass\nCASE a write lands: pass\n", "Approve"),
    ],
)
def test_a_staging_pass_leaving_a_case_not_exercised_takes_back_his_older_approve_so_his_card_asks_again_as_56963_needed_while_a_full_pass_keeps_it(
    named_repo: None,
    state_root: Path, cases: str, tap: str | None
) -> None:
    data = staging_drive("j1")
    key = "Forge-AI/monorepo#42"
    data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"waits": [f"o1:{STAGING}"], "tap": state.new_tap("Approve", "abc", "card", at="2026-11-14T06:00:00-08:00")}
    staging_return(data, "j1", "DONE ran the staging test of #42 on cut 1", cases)
    pr = data["prs"][key]
    assert ((pr["tap"] or {}).get("label"), state.cleared(pr)) == (tap, tap == "Approve")
    if not tap:
        assert pr["fundamental"] == "staging did not exercise a write lands after your Approve, so only an Approve you give now lands it without them"


def git_head(clone: Path, *args: str) -> str:
    subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_a_staging_tests_done_frees_the_prs_held_on_it_only_by_a_check_build_that_put_a_commit_carrying_their_heads_on_staging_or_a_mac_box_signed_in_to_it_as_j4507s_soak_build_did_not(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "monorepo"
    (clone / "infra" / "envs").mkdir(parents=True)
    (clone / "infra" / "envs" / "plat-usw2-staging.yaml").write_text("name: plat-usw2-staging\nstage: staging\n")
    (clone / "infra" / "envs" / "plat-usw2-prod.yaml").write_text("name: plat-usw2-prod\nstage: prod\n")
    subprocess.run(["git", "init", "-q", "-b", "dev", str(clone)], check=True)
    git_head(clone, "commit", "-q", "--allow-empty", "-m", "base")
    git_head(clone, "remote", "add", "origin", str(clone))
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
    provide(monkeypatch, clone, repo=NamedRepo, release=EnvsRelease)
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {})
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
    data["prs"] = {key: state.new_pr(f"https://github.com/{MONOREPO}/pull/{key.rsplit('#', 1)[1]}", "j1", "o1") | {"head": head, "base": "dev", "waits": ["j2"]} for key, head in zip(keys, (bottom, tip))}
    apply = {"state": "passed", "step_key": "apply-sandsql--plat-usw2-staging"}
    boxed = [{"state": "passed", "step_key": step} for step in ("mac-box-iris", "mac-box-sand", "mac-box-approve")]
    builds = {
        "16203": {"commit": soak, "jobs": [{"state": "passed", "step_key": "soak-staging"}]},
        "16085": {"commit": half, "jobs": [apply]},
        "16090": {"commit": cut, "jobs": [{"state": "passed", "step_key": "apply-sandsql--plat-usw2-prod"}, {"state": "failed", "step_key": "apply-sandsql--plat-usw2-staging"}]},
        "16086": {"commit": cut, "jobs": [apply]},
        "16300": {"commit": half, "jobs": boxed},
        "16301": {"commit": cut, "jobs": [*boxed[:2], {"state": "failed", "step_key": "mac-box-approve"}]},
        "16302": {"commit": cut, "jobs": boxed},
    }
    monkeypatch.setattr(github, "build", lambda pipeline, number: builds[number])

    def done(job_id: str, *numbers: str) -> str | None:
        report = "DONE the stack ran its tests on staging\n" + "".join(f"CHECK: https://buildkite.com/forge/release/builds/{number}\n" for number in numbers)
        return state.result_refusal(data, job_id, report.splitlines()[0], report)

    soaked, halved, prod, none = done("j2", "16203"), done("j2", "16085"), done("j2", "16090"), done("j2")
    box_halved, unsigned = done("j2", "16300"), done("j2", "16301")
    assert "j2's DONE frees Forge-AI/monorepo#4201, Forge-AI/monorepo#4202 from waiting on its staging test" in soaked
    assert "https://buildkite.com/forge/release/builds/16203 deployed nothing to staging" in soaked
    assert halved.startswith("j2's DONE frees Forge-AI/monorepo#4202 from waiting")
    assert f"Forge-AI/monorepo#4202's changes to reach.rs are missing from {half[:12]}, which https://buildkite.com/forge/release/builds/16085 deployed to plat-usw2-staging" in halved
    assert "16090 deployed nothing to staging" in prod
    assert f"Forge-AI/monorepo#4202's changes to reach.rs are missing from {half[:12]}, which https://buildkite.com/forge/release/builds/16300 deployed to a Mac box signed in to staging" in box_halved
    assert "16301 deployed nothing to staging, passing no apply step on an env the release provider places on staging and no mac-box-approve step of a Mac box proof" in unsigned
    assert none.endswith(
        "they name no Buildkite build. Name on a CHECK: line the release build that deployed a commit carrying each one's head to staging,"
        " or the Mac box prove build that put it on a Mac box signed in to staging, or, for a PR reaching only code its caller runs from a checkout, the GitHub commit link of the head its checkout ran,"
        " heading the CASE lines of that PR's run and naming the PR when the DONE tests several in turn, or return WAITING on what still holds them,"
        " such as staging:<target>/<env>@<commit> while staging does not run it yet"
    )
    assert [done("j2", "16086"), done("j2", "16203", "16086"), done("j2", "16302"), done("j1", "16203")] == [None, None, None, None]
    tested = {key: state.STAGING_TEST.format(number=key.rsplit("#", 1)[1]) for key in keys}
    data["orders"]["o1"]["parts"] |= {each: state.new_part() for each in tested.values()}
    data["jobs"]["j2"]["parts"] = [part, *tested.values()]

    def in_turn(*numbers: str) -> str | None:
        report = (
            f"DONE {' and '.join(f'#{number}' for number in numbers)} each passed on staging\n"
            "CHECK: https://buildkite.com/forge/release/builds/16085\nCASE a layout reads its key ranges: pass\n"
            "CHECK: https://buildkite.com/forge/release/builds/16086\nCASE a layout reads its key ranges: pass\n"
        )
        return state.result_refusal(data, "j2", report.splitlines()[0], report)

    swapped = in_turn("4202", "4201")
    assert in_turn("4201", "4202") is None
    assert swapped.startswith("j2's DONE frees Forge-AI/monorepo#4202 from waiting on its staging test, and no build the CHECK: lines heading its cases name deployed")
    assert f"Forge-AI/monorepo#4202's changes to reach.rs are missing from {half[:12]}, which https://buildkite.com/forge/release/builds/16085 deployed to plat-usw2-staging" in swapped
    data["jobs"]["j2"]["parts"] = [part]
    data["jobs"]["j2"]["goal"] = "read the layout's key ranges"
    assert done("j2", "16203") is None


def test_a_staging_tests_done_frees_a_pr_changing_only_tools_ci_and_go_ci_by_a_check_linking_a_commit_carrying_its_head_as_j212s_pass_of_56826_was_not(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "monorepo"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(clone)], check=True)
    git_head(clone, "commit", "-q", "--allow-empty", "-m", "base")
    git_head(clone, "remote", "add", "origin", str(clone))
    git_head(clone, "switch", "-q", "-c", "pin")
    heads = []
    for path, subject in (("inside.go", "the dialer pod pins amd64"), ("ci", "tools/ci fetches the linux-amd64 ci")):
        (clone / path).write_text(f"{subject}\n")
        git_head(clone, "add", path)
        heads.append(git_head(clone, "commit", "-q", "-m", subject))
    older, head = heads
    proof = tmp_path / "proof.sh"
    proof.write_text("#!/bin/sh\nexit 0\n")
    proof.chmod(0o755)
    data = state.new_drive("demo", "coordinator", None, str(clone))
    state.create(data)
    data = state.load("demo")
    created, part = "2026-11-15T12:00:00-08:00", "the staging smoke runs from an Apple Silicon Mac"
    data["orders"] = {"o1": state.new_order("smoke staging from a Mac", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {part: state.new_part()}, created) | {"kind": "build", "jobs": ["j1", "j2"]}}
    data["jobs"] = {
        "j1": state.new_job("o1", "builder", "pin the dialer pod to amd64", [part], created) | {"state": "waiting", "repro": str(proof)},
        "j2": state.new_job("o1", "builder", "run the staging smoke from this Mac at the PR's head", [part], created) | {"state": "running", "builds_nothing": True, "repro": str(proof)},
    }
    key = f"{MONOREPO}#56826"
    data["prs"] = {key: state.new_pr(f"https://github.com/{MONOREPO}/pull/56826", "j1", "o1") | {"head": head, "base": "dev", "waits": ["j2"]}}
    reach = {key: {"components": ["api", "platy"], "touched": ["platy"], "dev": ["go/ci/internal/tenantsmoke/inside.go", "tools/ci"], "releases": []}}
    monkeypatch.setattr(cli, "release_reach", lambda name, data, keys: {each: reach[each] for each in keys if each in reach})

    def done(*proofs: str) -> str | None:
        report = "DONE #56826's staging smoke passed from this Mac\n" + "".join(f"CHECK: {proof}\n" for proof in proofs) + "CASE the dialer pod runs on an amd64 node: pass\n"
        return state.result_refusal(data, "j2", report.splitlines()[0], report)

    def commit(sha: str, repo: str = MONOREPO) -> str:
        return f"https://github.com/{repo}/commit/{sha}"

    assert [done(commit(head)), done(str(tmp_path / "staging-test-56826.txt"), commit(head[:12]))] == [None, None]
    lacking = f"{key} reaches no component a release deploys, only code its caller runs from a checkout, and no CHECK: line heading its cases links a commit carrying its head {head[:12]}"
    for refused in (done(commit(older)), done(commit(head, "Forge-AI/other")), done(str(tmp_path / "staging-test-56826.txt"))):
        assert refused.startswith(f"j2's DONE frees {key} from waiting on its staging test, and no build the CHECK: lines heading its cases name deployed")
        assert lacking in refused
    for released in ({key: reach[key] | {"releases": ["api"]}}, {}):
        reach = released
        refused = done(commit(head))
        assert refused.startswith(f"j2's DONE frees {key} from waiting on its staging test, and no build the CHECK: lines heading its cases name deployed a commit carrying it to staging: they name no Buildkite build.")
    reach = {key: {"components": ["api", "platy"], "touched": ["platy"], "dev": ["go/ci/internal/tenantsmoke/inside.go", "tools/ci"], "releases": []}}
    monkeypatch.setattr(github, "build", lambda pipeline, number: {"jobs": [], "commit": older})
    assert done("https://buildkite.com/forge/release/builds/17535", commit(head)) is None


def test_a_full_pass_by_the_builder_of_the_pr_it_tests_delivers_its_staging_part_while_its_done_waits_on_that_pr_and_after_the_watch_takes_its_merge(named_repo: None, state_root: Path) -> None:
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


def test_a_testers_passing_done_delivers_its_staging_part_for_the_head_it_tested_whatever_the_tester_says_after_and_a_new_head_reopens_it_as_j4481s_did_not(
    named_repo: None, state_root: Path
) -> None:
    data = staging_drive("j1")
    url = "https://github.com/Forge-AI/monorepo/pull/42"
    data["jobs"]["j1"]["prs"] = [url]
    data["prs"] = {"Forge-AI/monorepo#42": state.new_pr(url, "j1", "o1") | {"head": "75ddb639b7a1336f43f77a6e3be38bfac80f1828"}}
    staging_return(data, "j1", "DONE every case of #42 passed", f"CASE a rollback refused: pass\nCHECK: {PROOF}, carrying {url} at 75ddb639b7a1\n")
    passed = (data["jobs"]["j1"]["state"], staging_reads(data), data["orders"]["o1"]["parts"][STAGING]["passed_head"])
    state.take_result(data, "j1", f"WAITING {url} to land: it passed its staging test, is approved and green")
    waiting = (data["jobs"]["j1"]["state"], staging_reads(data))
    data["prs"]["Forge-AI/monorepo#42"]["head"] = "0123456789abcdef0123456789abcdef01234567"
    assert [passed, waiting, staging_reads(data)] == [
        ("waiting", (True, PROOF, "j1"), "75ddb639b7a1"),
        ("waiting", (True, PROOF, "j1")),
        (False, PROOF, "j1"),
    ]


def test_a_job_following_a_release_it_started_on_a_staging_test_part_is_no_staging_run_and_its_done_naming_no_case_leaves_the_testers_pass_as_j5978s_did_not(named_repo: None, state_root: Path) -> None:
    data = staging_drive("j1", "j2")
    data["jobs"]["j2"] |= {"brief_flags": {"release": "kaleidoscopic-gamsakhurdia"}, "builds_nothing": True, "waits": ["staging:monorepo/plat-usw2-staging@70f5e5b"]}
    staging_return(data, "j1", "DONE every case of #42 passed", "CASE a rollback refused: pass\n")
    state.take_result(data, "j2", "DONE APPLIED https://buildkite.com/forge/release/builds/16491: cloudrunner-next on plat-usw2-staging at 70f5e5bfc396, applied 6m ago")
    assert (state.staging_parts(data, data["jobs"]["j2"]), staging_reads(data), state.staging_tester(data, "j2"), state.staging_tester(data, f"o1:{STAGING}")) == ([], (True, PROOF, "j1"), None, "j1")


APPROVE = {"label": "Approve", "at": "2026-11-14T10:30:00-08:00", "head": "aaaaaaa111", "source": "slack", "picks": {}, "card": None}


def test_a_staging_tests_full_pass_keeps_his_approve_given_before_it_on_the_pr_its_part_holds_as_56802s_rehearsal_did_not(named_repo: None, state_root: Path) -> None:
    data = staging_drive("j1")
    data["jobs"]["j2"] = state.new_job("o1", "builder", "ship the writer", [], "2026-11-14T10:00:00-08:00")
    data["orders"]["o1"]["jobs"].append("j2")
    tested = f"{MONOREPO}#42"
    data["prs"] = {tested: state.new_pr(state.pr_url(tested), "j2", "o1") | {"tap": dict(APPROVE), "waits": [f"o1:{STAGING}"]}}
    staging_return(data, "j1", "DONE every case of #42 passed", "CASE a rollback refused: pass\n")
    assert (data["prs"][tested]["tap"], data["prs"][tested]["fundamental"], staging_reads(data)) == (APPROVE, None, (True, PROOF, "j1"))


def test_a_drive_saved_with_approves_a_staging_pass_took_back_gives_each_open_pr_its_newest_approve_once(state_root: Path) -> None:
    make("demo")
    data = state.load("demo")
    del data["staging_approves_restored"]
    created = "2026-11-15T09:00:00-08:00"
    data["orders"] = {"o1": state.new_order("ship the writer", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {"ship it": state.new_part()}, created) | {"kind": "build", "jobs": ["j1"]}}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "ship it", ["ship it"], created)}
    taken = f"{state.TAKEN_BY_PASS}, so only an Approve you give now lands it: {PROOF}"
    restored, held, worded, merged = (f"{MONOREPO}#{number}" for number in (42, 43, 44, 45))
    data["prs"] = {key: state.new_pr(state.pr_url(key), "j1", "o1") | {"fundamental": taken} for key in (restored, held, worded, merged)}
    data["prs"][merged] |= {"state": "merged", "merged_sha": "fedcba9876543210"}
    state.create(data)
    for key, label in ((restored, "Hold"), (restored, "Approve"), (held, "Approve"), (held, "Hold"), (worded, "Approve"), (worded, "Words"), (merged, "Approve")):
        repo, number = key.split("#")
        state.append_event("demo", "tap", repo=repo, number=int(number), label=label, head="aaaaaaa111", source="pressed the button", action_ts="1794762137.637881", lane="root")
    data = state.load("demo")
    assert [(data["prs"][key]["tap"] or {}).get("label") for key in (restored, held, worded, merged)] == ["Approve", None, None, None]
    assert ({field: data["prs"][restored]["tap"][field] for field in ("head", "source", "action_ts")}, data["prs"][restored]["fundamental"], data["staging_approves_restored"]) == (
        {"head": "aaaaaaa111", "source": "pressed the button", "action_ts": "1794762137.637881"},
        None,
        True,
    )


def test_each_open_tap_saved_with_no_card_gives_a_card_press_the_prs_of_the_newest_stored_card_on_its_pr_asked_by_it_and_his_words_their_pr_alone_at_every_load(state_root: Path) -> None:
    make("demo")
    data = state.load("demo") | {"tap_cards_kept": True}
    created = "2026-11-15T04:00:00-08:00"
    data["orders"] = {"o1": state.new_order("ship the writer", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {"ship it": state.new_part()}, created) | {"kind": "build", "jobs": ["j1"]}}
    data["jobs"] = {"j1": state.new_job("o1", "builder", "ship it", ["ship it"], created)}
    approved, joined, worded, uncarded = (f"{MONOREPO}#{number}" for number in (56802, 56935, 56773, 42))
    data["prs"] = {key: state.new_pr(state.pr_url(key), "j1", "o1") | {"stack": [approved, joined, worded]} for key in (approved, joined, worded)} | {uncarded: state.new_pr(state.pr_url(uncarded), "j1", "o1")}
    for key, source, action, tapped in (
        (approved, "slack", "1794765737.000100", "2026-11-15T09:02:17-08:00"),
        (joined, "slack", "1794778800.000100", "2026-11-15T12:40:00-08:00"),
        (worded, "slack:https://acme.slack.com/archives/D123/p1794783600000100", None, "2026-11-15T15:09:00-08:00"),
        (uncarded, "slack", "1794765600.000100", "2026-11-15T09:00:00-08:00"),
    ):
        data["prs"][key]["tap"] = state.new_tap("Approve", "aaaaaaa111", source, action, at=tapped)
    state.create(data)

    def stored(url: str, asked: str, *keys: str) -> dict:
        return {"url": url, "prs": [state.pr_url(key) for key in keys], "asked": asked, "card": {"title": "Fix: land the writer"}}

    shared = stored("https://acme.slack.com/archives/D123/p1794781000000100", "2026-11-15T12:35:47-08:00", approved, joined, worded)
    alone = stored("https://acme.slack.com/archives/D123/p1794770000000100", "2026-11-15T04:23:52-08:00", approved) | {"answer": {"label": "Approve", "at": "2026-11-15T09:02:17-08:00"}}
    state.cards_path("demo").write_text(json.dumps({"source": "live", "team_url": "", "questions": [], "pr_cards": [shared], "closed": [alone]}))
    data = state.load("demo")
    assert ([data["prs"][key]["tap"]["card"] for key in (approved, joined, worded, uncarded)], "tap_cards_kept" in data) == ([[approved], [approved, joined, worded], [worded], [uncarded]], False)


def test_a_drive_saved_reading_staging_passes_by_prose_case_lines_and_release_followers_reads_each_again_from_its_testers_returns_dated_by_the_first_carrying_it_as_56594s_and_56677s_were_not(
    named_repo: None,
    state_root: Path,
) -> None:
    data = staging_drive()
    del data["staging_cases_reread"]
    data |= {"staging_passes_named": True, "staging_passes_reread": True}
    created, passed = "2026-11-14T10:00:00-08:00", "2026-11-14T11:00:00-08:00"
    prose, followed, rerun, uncased, unnamed = (state.STAGING_TEST.format(number=number) for number in (42, 43, 44, 45, 46))
    followed_soak = "staging:monorepo/plat-usw2-staging@70f5e5b"
    data["orders"]["o1"]["parts"] = {part: state.new_part() | {"soak": followed_soak if part == followed else None} for part in (prose, followed, rerun, uncased, unnamed)}
    testers = {"j1": prose, "j2": followed, "j3": followed, "j4": rerun, "j5": rerun, "j6": uncased, "j7": unnamed}
    data["jobs"] = {job_id: state.new_job("o1", "builder", "run the staging test", [part], created) | {"state": "done"} for job_id, part in testers.items()}
    data["jobs"]["j2"]["waits"] = ["staging:monorepo/plat-usw2-staging@346fcf4"]
    data["jobs"]["j3"] |= {"state": "running", "brief_flags": {"release": "kaleidoscopic-gamsakhurdia"}, "builds_nothing": True, "waits": [followed_soak]}
    data["jobs"]["j5"]["state"] = "waiting"
    data["jobs"] |= {"j8": state.new_job("o1", "builder", "ship #42", [], created), "j9": state.new_job("o1", "builder", "ship #43", [], created)}
    data["orders"]["o1"]["jobs"] = [*testers, "j8", "j9"]
    approves = {f"{MONOREPO}#42": APPROVE, f"{MONOREPO}#43": APPROVE | {"at": "2026-11-14T11:30:00-08:00"}}
    data["prs"] = {key: state.new_pr(state.pr_url(key), job_id, "o1") | {"tap": dict(tap)} for (key, tap), job_id in zip(approves.items(), ("j8", "j9"))}
    state.create(data)

    def kept(job_id: str, line: str, body: str, at: str = passed) -> None:
        state.keep_report("demo", job_id, line, f"{line}\n{body}", state.parse_time(at))

    kept("j1", "DONE ran the staging test of #42", f"CHECK: {PROOF}\nCASE line sent to root on topic staging-slots {{at:718168}}\nCASE a rollback refused: pass\n")
    kept("j1", f"DONE {state.pr_url(f'{MONOREPO}#42')} merged: {MONOREPO}#42 at fedcba987654", "", "2026-11-14T11:10:00-08:00")
    kept("j2", "DONE ran the staging test of #43", f"CHECK: {PROOF}\nCASE a rollback refused: pass\n")
    kept("j2", "WAITING staging:monorepo/plat-usw2-staging@346fcf4, the restore after its cases", "", "2026-11-14T11:05:00-08:00")
    kept("j2", "DONE ran the staging test of #43, and staging is restored", f"CHECK: {PROOF}\nCASE a rollback refused: pass\n", "2026-11-14T12:30:00-08:00")
    kept("j3", "DONE APPLIED https://buildkite.com/forge/release/builds/16491: cloudrunner-next on plat-usw2-staging at 70f5e5bfc396", "", "2026-11-14T12:00:00-08:00")
    kept("j4", "DONE ran the staging test of #44", f"CHECK: {PROOF}\nCASE a rollback refused: pass\n")
    kept("j6", "DONE ran the staging test of #45", f"CHECK: {PROOF}\n")
    kept("j7", "DONE ran the staging test of #42", f"CHECK: {PROOF}\nCASE a rollback refused: pass\n")
    data = state.load("demo")
    parts = data["orders"]["o1"]["parts"]
    assert [(state.delivered(data, "o1", part), parts[part]["posted"], parts[part]["posted_by"], parts[part]["passed_at"]) for part in (prose, followed, rerun, uncased, unnamed)] == [
        (True, PROOF, "j1", state.stamp(state.parse_time(passed))),
        (True, PROOF, "j2", state.stamp(state.parse_time(passed))),
        (False, PROOF, "j4", state.stamp(state.parse_time(passed))),
        (False, None, None, None),
        (False, None, "j7", None),
    ]
    assert ([(data["prs"][key]["tap"] or {}).get("at") for key in approves], parts[followed]["soak"]) == (["2026-11-14T10:30:00-08:00", "2026-11-14T11:30:00-08:00"], "staging:monorepo/plat-usw2-staging@346fcf4")
    with edit("demo") as drive:
        assert (drive["staging_cases_reread"], "staging_passes_named" in drive, "staging_passes_reread" in drive) == (True, False, False)


J164 = """DONE #56799 and #56775 each answered all 22 staging cases exactly as dev's code did
CHECK: https://buildkite.com/forge/release/builds/16698
CASE good token: pass
CASE token issued to another user: not exercised: a staging soak signs in only as the smoke-test account
CASE DuckDB route with attachments: not exercised: no staging sandsql server sets analytics = true
CHECK: https://buildkite.com/forge/release/builds/16721
CASE good token: pass
CASE token issued to another user: not exercised: a staging soak signs in only as the smoke-test account
CASE DuckDB slow-statement log line: fail: no line logged
PR: https://github.com/Forge-AI/monorepo/pull/56799 (head c5dca80ed9be032855993e8dd9b000c2e4ef8665)
PR: https://github.com/Forge-AI/monorepo/pull/56775 (head 2c18705b7744b2df71a0f2103b86e3b8a2f17d83)
"""


def test_a_done_testing_two_prs_in_turn_keeps_each_prs_own_cases_build_and_soak_commit_on_its_part_as_j164s_did_not(home_repo: None, state_root: Path) -> None:
    data = staging_drive("j1")
    parts = {number: state.STAGING_TEST.format(number=number) for number in ("56799", "56775")}
    heads = {"56799": "c5dca80ed9be032855993e8dd9b000c2e4ef8665", "56775": "2c18705b7744b2df71a0f2103b86e3b8a2f17d83"}
    data["orders"]["o1"]["parts"] = {part: state.new_part() for part in parts.values()}
    data["jobs"]["j1"]["parts"] = list(parts.values())
    data["prs"] = {f"{MONOREPO}#{number}": state.new_pr(state.pr_url(f"{MONOREPO}#{number}"), "j1", "o1") | {"head": head} for number, head in heads.items()}
    soaks = [f"staging:sandsql/plat-usw2-staging@{commit}" for commit in ("833e4cd4388f", heads["56799"], heads["56775"])]
    data["jobs"]["j1"]["waits"] = soaks
    state.take_result(data, "j1", J164.splitlines()[0], J164)
    held = data["orders"]["o1"]["parts"]
    assert [(state.delivered(data, "o1", part), held[part]["posted"], held[part]["posted_by"], held[part]["soak"]) for part in parts.values()] == [
        (True, "https://buildkite.com/forge/release/builds/16698", "j1", soaks[1]),
        (False, None, "j1", soaks[2]),
    ]
    assert (state.unexercised(held[parts["56799"]]), state.unpassed(held[parts["56775"]])) == (
        [("token issued to another user", "a staging soak signs in only as the smoke-test account"), ("DuckDB route with attachments", "no staging sandsql server sets analytics = true")],
        "CASE DuckDB slow-statement log line: fail: no line logged",
    )
    named = J164.replace("builds/16698\n", "builds/16698 for #56775\n").replace("builds/16721\n", "builds/16721 for #56799\n")
    assert [state.part_run(data, "j1", report, parts["56799"])[1] for report in (J164, named)] == [["https://buildkite.com/forge/release/builds/16698"], ["https://buildkite.com/forge/release/builds/16721"]]


J6143 = """DONE all cases read on Mac box j6143-proof: #55974 A1-A2 pass, #56754 C1-C2 pass, #56058 B1 pass and B2 2a fails
PR: https://github.com/Forge-AI/monorepo/pull/55974 at 9006679
PR: https://github.com/Forge-AI/monorepo/pull/56058 at 326678a
PR: https://github.com/Forge-AI/monorepo/pull/56754 at df046be
CASE #55974 A1 leave the root while its subagent runs: pass: left root with its child running
CASE #55974 A2 archive a root: pass: the root left GET /api/sessions
CASE #56058 B1 three typed sends while busy: pass: snapshot 119 lists the three queued in typed order
CASE #56058 B2 2a no row for the courier's report while the card is open: fail: snapshot 83 lists the report queued
CASE #56754 C1 Agents pill while bash runs sand iris: pass: iris.subagents reached the parent stream
CASE #56754 C2 inbox line names the child by title: not exercised: no child titled itself
CHECK: /Users/yasyf/.local/state/long-running/durable-agents/scratch/j6143/results.md
CHECK: /Users/yasyf/.local/state/long-running/durable-agents/scratch/j6143/out/B-check.txt
"""


def test_a_done_testing_prs_of_two_orders_keeps_on_each_part_the_cases_whose_case_lines_name_its_pr_so_only_the_failing_prs_part_reads_no_pass_as_j6143s_did_not(named_repo: None, state_root: Path) -> None:
    data = staging_drive("j1")
    parts = {number: state.STAGING_TEST.format(number=number) for number in ("55974", "56754", "56058")}
    data["orders"]["o1"]["parts"] = {parts["55974"]: state.new_part(), parts["56754"]: state.new_part()}
    data["orders"]["o2"] = state.new_order("ship #56058", "terminal", None, {parts["56058"]: state.new_part()}, state.stamp()) | {"jobs": ["j1"]}
    data["jobs"]["j1"] |= {"parts": [parts["55974"], parts["56754"]], "carries": {"o2": [parts["56058"]]}}
    state.take_result(data, "j1", J6143.splitlines()[0], J6143)
    held = [data["orders"][order_id]["parts"][parts[number]] for order_id, number in (("o1", "55974"), ("o1", "56754"), ("o2", "56058"))]
    results = "/Users/yasyf/.local/state/long-running/durable-agents/scratch/j6143/results.md"
    assert [(len(part["cases"]), part["posted"]) for part in held] == [(2, results), (2, results), (2, None)]
    assert (state.unexercised(held[1]), state.unpassed(held[2])) == (
        [("#56754 C2 inbox line names the child by title", "no child titled itself")],
        "CASE #56058 B2 2a no row for the courier's report while the card is open: fail: snapshot 83 lists the report queued",
    )


def test_a_drive_saved_before_after_merge_passes_were_kept_reads_each_merged_prs_from_its_provers_returns_dated_by_the_return_as_56539s_was_not(home_repo: None, state_root: Path) -> None:
    data = staging_drive()
    del data["staging_cases_reread"]
    created = "2026-11-14T10:00:00-08:00"
    provers = {"j1": 56539, "j2": 56516, "j3": 56478}
    data["jobs"] = {job_id: state.new_job("o1", "builder", f"prove {MONOREPO}#{number} live", ["prove it"], created) | {"builds_nothing": True, "state": "done"} for job_id, number in provers.items()}
    data["jobs"]["j9"] = state.new_job("o1", "builder", "ship them", ["prove it"], created) | {"state": "done"}
    data["orders"]["o1"] |= {"parts": {"prove it": state.new_part()}, "jobs": [*provers, "j9"]}
    data["prs"] = {f"{MONOREPO}#{number}": state.new_pr(state.pr_url(f"{MONOREPO}#{number}"), "j9", "o1") | {"state": "merged", "merged_sha": "6545af289898"} for number in provers.values()}
    state.create(data)
    passed, failed = "2026-11-14T11:00:00-08:00", "2026-11-14T11:20:00-08:00"
    release = "https://buildkite.com/forge/release/builds/16484"
    state.keep_report("demo", "j1", f"WAITING release:api:{MONOREPO}#56539@plat-usw2-prod", f"WAITING release:api:{MONOREPO}#56539@plat-usw2-prod: staging passed for #56539\nCHECK: {release}\nCASE old clients still read: pass\nCASE sand-exec: not exercised (staging has no Riza credentials)\n", state.parse_time(passed))
    state.keep_report("demo", "j1", f"DONE {MONOREPO}#56539 live on plat-usw2-prod", f"DONE {MONOREPO}#56539 live on plat-usw2-prod\n", state.parse_time("2026-11-14T12:00:00-08:00"))
    state.keep_report("demo", "j2", f"WAITING release:api:{MONOREPO}#56516@plat-usw2-prod", f"WAITING release:api:{MONOREPO}#56516@plat-usw2-prod: staging ran #56516\nCHECK: {release}\nCASE old clients still read: fail: 500 on GET /api/sessions\n", state.parse_time(failed))
    state.keep_report("demo", "j3", f"WAITING release:api:{MONOREPO}#56478@plat-usw2-prod", f"WAITING release:api:{MONOREPO}#56478@plat-usw2-prod: staging ran #56478\nCHECK: {release}\nCASE sand-exec: not exercised\n", state.parse_time(failed))
    prs = state.load("demo")["prs"]
    assert [prs[f"{MONOREPO}#{number}"]["after_merge"] for number in provers.values()] == [
        {"cases": [["old clients still read", "pass"], ["sand-exec", "not exercised (staging has no Riza credentials)"]], "posted": release, "posted_by": "j1", "at": state.stamp(state.parse_time(passed))},
        {"cases": [["old clients still read", "fail: 500 on GET /api/sessions"]], "posted": None, "posted_by": "j2", "at": state.stamp(state.parse_time(failed))},
        None,
    ]


def test_a_builder_only_following_a_release_another_job_started_neither_stands_in_for_the_tester_nor_holds_its_pass_its_cases_not_exercised_as_j6240s_did(named_repo: None, state_root: Path) -> None:
    data = staging_drive("j1", "j2")
    data["jobs"]["j1"]["state"] = "running"
    data["jobs"]["j2"] |= {"goal": "follow the staging release radiant-tudor to applied", "builds_nothing": True, "state": "running"}
    named = (state.part_tester(data, "o1", STAGING), state.staging_tester(data, f"o1:{STAGING}"))
    followed = "DONE APPLIED: radiant-tudor serves cloudrunner-next on plat-usw2-staging at 346fcf4a024d"
    staging_return(data, "j2", followed, "CASE a rollback refused for #42: not exercised: this job only followed the release; j1 runs the case\n")
    before = staging_reads(data)
    staging_return(data, "j1", "DONE every case of #42 passed", "CASE a rollback refused: pass\n")
    passed = staging_reads(data)
    staging_return(data, "j2", followed, "CASE a rollback refused for #42: not exercised: this job only followed the release; j1 runs the case\n")
    assert [named, before, passed, staging_reads(data), state.part_tester(data, "o1", STAGING)] == [("j1", "j1"), (False, None, None), (True, PROOF, "j1"), (True, PROOF, "j1"), "j1"]


def test_a_part_an_older_install_saved_with_spaces_around_its_name_loads_trimmed_wherever_a_job_or_pr_names_it(state_root: Path) -> None:
    make("demo")
    saved = state.stored("demo")
    cut = "both PRs he approved this morning and the stack he tapped an hour later sat for "
    parts = {cut: {"posted": None, "design": None}, " fix": {"posted": None, "design": None}}
    saved["orders"] = {"o1": {"text": "the stack sat", "jobs": ["j1", "j2"], "parts": parts, "promised": None}, "o2": {"text": "why", "jobs": [], "parts": {"cause ": {"posted": None}}, "promised": None}}
    saved["jobs"] = {"j1": state.new_job("o1", "reader", "find why", [cut], state.stamp()), "j2": state.new_job("o2", "builder", "fix it", ["cause "], state.stamp()) | {"carries": {"o1": [" fix"]}}}
    saved["prs"] = {"yasyf/cc-skills#7": state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j2", "o1") | {"parts": [" fix"]}}
    state.save("demo", saved)
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
    state.create(data)
    loaded = state.load("demo")
    order, job = loaded["orders"]["o1"], loaded["jobs"]["j1"]
    assert (order["closed_on"], order["quiet"]["record"], order["watch"]["record"], job["questions"][0]["closed"]["seq"], job["undelivered"][0]["seq"]) == (
        "697590",
        "697593",
        "697594",
        "697591",
        "697592",
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
    state.save("demo", saved)
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
    assert (argv[:2], Path(argv[2]).name, argv[3:]) == ([sys.executable, "-I"], "drive", ["pr", "land", "https://github.com/yasyf/cc-skills/pull/7", "--drive", "demo"])
    assert options["env"][state.DRIVE_ENV] == "demo" and options["env"][state.DETACHED_ENV] == "landings.log" and "CLAUDE_CODE_SESSION_ID" not in options["env"]
    assert options["env"]["PATH"] == "/cache/cc-context/0.96.10/bin:/opt/homebrew/bin"


def test_projection_indexes_pr_jobs_once_preserving_order_and_scope() -> None:
    class Jobs(dict):
        scans = 0

        def items(self):
            self.scans += 1
            return super().items()

    jobs = Jobs({f"j{n}": {"order": "o1", "prs": [f"pr{n}", "shared", "shared"], "questions": []} for n in range(64)})
    data = {"jobs": jobs, "prs": {}, "orders": {}}
    prs = [{"job": f"j{n}", "url": f"pr{n}"} for n in range(64)] + [{"job": "outside", "url": "shared"}, {"job": "outside", "url": "unknown"}]
    expected = [state.pr_jobs(data, pr) for pr in prs]
    assert jobs.scans == len(prs)
    jobs.scans = 0
    other = {"jobs": {"other": {"order": "o1", "prs": ["shared"], "questions": []}}, "prs": {}, "orders": {}}
    with state.indexed(data):
        assert [state.pr_jobs(data, pr) for pr in prs] == expected
        assert jobs.scans == 1
        assert state.pr_jobs(other, prs[-2]) == ["outside", "other"]
        with state.indexed(other):
            assert state.pr_jobs(other, prs[-2]) == ["outside", "other"]
        assert state.pr_jobs(data, prs[-2]) == expected[-2]
        assert jobs.scans == 1
    jobs["new"] = {"order": "o1", "prs": ["shared"], "questions": []}
    assert state.pr_jobs(data, prs[-2]) == [*expected[-2], "new"]
    assert jobs.scans == 2
    with pytest.raises(RuntimeError), state.indexed(data):
        raise RuntimeError("projection failed")
    assert state.INDEX.get() is None


def test_the_demo_drive_a_release_saves_for_the_next_release_to_try_reads_with_its_own_drive_status(
    tmp_path: Path, run, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = tmp_path / "saved"
    subprocess.run([sys.executable, "-I", str(Path(__file__).resolve().parents[1] / "tools" / "demo-drive"), str(saved)], check=True)
    monkeypatch.setenv("LONG_RUNNING_STATE", str(saved))

    code, out = run("status", "--drive", "demo")

    assert code == 0
    assert len([job for job in state.load("demo")["jobs"].values() if job["order"] == "o1"]) == len(state.JOB_KINDS) * len(state.JOB_STATES)


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
    with edit("demo") as data:
        for job in data["jobs"].values():
            state.hold_text(job, wake, None)
        state.hold_text(data["jobs"]["j4"], "rebase on main first", "coordinator")
    with edit("demo") as data:
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
    with edit("demo") as data:
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
    with edit("demo") as data:
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
    with edit("demo") as data:
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


@pytest.mark.parametrize(
    ("worked", "owed"),
    [
        ({"worker_started": "2026-11-15T13:14:40-08:00"}, False),
        ({"resumed": "2026-11-15T12:20:00-08:00"}, False),
        ({"returned": "2026-11-15T12:36:00-08:00"}, False),
        ({"given": [{"words": "pick up the o146 fix", "at": "2026-11-15T11:58:00-08:00"}]}, False),
        ({"worker_started": "2026-11-15T11:50:00-08:00", "resumed": "2026-11-15T11:51:00-08:00"}, True),
    ],
)
def test_a_handoff_is_owed_until_a_job_it_or_a_record_re_it_names_or_the_job_that_one_was_handed_to_works_after_it_and_a_withdraw_re_it_ends_it(
    worked: dict, owed: bool
) -> None:
    """Handoff 719716 on o146 named j355, which hand_over had handed to j5665 at 11:45 AM; j5665's worker started at 1:14 PM and opened #56937, yet the watch listed the handoff as unstarted."""
    handoff = {"seq": "719716", "text": "resume j355 on the o146 fix", "at": "2026-11-15T11:57:08-08:00", "jobs": []}
    data = {
        "orders": {"o146": state.new_order("the run page card drops its PRs", "terminal", None, {}, "2026-11-14T09:00:00-08:00")},
        "jobs": {
            "j355": state.new_job("o146", "builder", "fix the run page card", [], "2026-11-14T09:30:00-08:00") | {"handed_to": "j5665"},
            "j5665": state.new_job("o2", "builder", "fix the run page card", [], "2026-11-15T11:45:24-08:00") | worked,
        },
    }
    data["orders"]["o146"].update(jobs=["j355"], handoffs=[handoff])
    assert state.owed_handoffs(data, ["o146"]) == [handoff]
    state.answer_handoffs(data, [("719716", "answer", {"job": "j355"}), ("719999", "answer", {"job": "j5665"})])
    assert handoff["jobs"] == ["j355"]
    assert state.owed_handoffs(data, ["o146"]) == ([handoff] if owed else [])
    state.answer_handoffs(data, [("719716", "withdraw", {})])
    assert state.owed_handoffs(data, ["o146"]) == []


def test_his_approval_covers_work_on_its_order_until_a_newer_answer_supersedes_it_or_he_holds_a_pr_it_covers(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = staging_drive("j1")
    key = f"{MONOREPO}#42"
    data["prs"] = {key: state.new_pr(state.pr_url(key), "j1", "o1")}
    approves = {"orders": ["o1"], "keys": [], "on": ["o1"], "asked": "Drive tooling", "said": "stuff like this has my approval", "held": None}
    data["answers"] = {"2164dcb0": {"words": approves["said"], "link": "terminal", "at": "", "said": "", "cards": {}, "jobs": {}, "approves": approves}}
    gone: set[str] = set()
    monkeypatch.setattr(settled, "replaced", lambda cwd: gone)
    assert settled.approved_by(data, key, "o1", {}) == "2164dcb0"
    gone.add("2164dcb0")
    assert settled.approved_by(data, key, "o1", {}) is None
    gone.clear()
    state.end_approvals(data, key, None)
    assert (settled.approved_by(data, key, "o1", {}), approves["held"]["pr"]) == (None, key)


def test_projection_partitions_design_builders_without_repeated_job_scans() -> None:
    class Jobs(dict):
        scans = 0

        def items(self):
            self.scans += 1
            return super().items()

    moment = state.stamp()
    orders = {
        "o1": state.new_order("build it", "terminal", None, {"design": state.new_part(), "listed": state.new_part() | {"design": "j1"}}, moment),
        "o2": state.new_order("other work", "terminal", None, {}, moment),
    }
    jobs = Jobs({
        "j1": state.new_job("o1", "design", "design it", ["design"], moment),
        "j2": state.new_job("o1", "builder", "build the design", ["design", "extra"], moment) | {"state": "running"},
        "j3": state.new_job("o1", "builder", "build its listed change", ["listed"], moment) | {"state": "done"},
        "j4": state.new_job("o1", "builder", "failed", ["design"], moment) | {"state": "failed"},
        "j5": state.new_job("o1", "builder", "unrelated", ["elsewhere"], moment),
        "j6": state.new_job("o1", "reader", "read", ["design"], moment),
        "j7": state.new_job("o1", "design", "design nothing", [], moment),
        **{f"j{n}": state.new_job("o2", "builder", "other order", ["design", "listed"], moment) for n in range(8, 72)},
    })
    data = {"jobs": jobs, "orders": orders, "prs": {}}
    assert state.design_builders(data, "j1") == ["j3", "j2"]
    assert state.design_builders(data, "j7") == ["j3", "j2", "j5"]
    expected = {key: state.design_builders(data, key) for key in jobs}
    jobs.scans = 0
    other = data | {"jobs": {"j1": jobs["j1"]}}
    with state.indexed(data):
        assert jobs.scans == 1
        for _ in range(3):
            assert {key: state.design_builders(data, key) for key in jobs} == expected
        assert state.order_jobs(data, "missing") == []
        assert jobs.scans == 1
        with state.indexed(other):
            assert state.design_builders(other, "j1") == []
        assert state.design_builders(data, "j1") == ["j3", "j2"]
        assert jobs.scans == 1
    jobs["j2"]["order"] = "o2"
    with state.indexed(data):
        assert state.design_builders(data, "j1") == ["j3"]
    assert state.INDEX.get() is None


def test_a_queued_line_addresses_a_cut_ask_to_the_packer_lane_the_slot_names_and_names_none_while_no_lane_is_named() -> None:
    moment = datetime(2026, 11, 15, 22, 5, tzinfo=UTC)
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "waits": []} for job_id in ("j1", "j2", "j3")}}
    env = "plat-usw2-staging"
    state.slot_claim(data, env, "j1", 30, [], None, moment)
    assert state.slot_claim(data, env, "j2", 30, [], None, moment) == f"QUEUED j2 for the {env} slot, place 1, behind j1, whose lease ends {data['slots'][env]['holder']['until']}"
    assert state.slot_packer(data, env, "platform") == f"{env}: testers ask lane platform for a cut they cannot start"
    assert state.slot_claim(data, env, "j3", 30, [], None, moment).endswith(f"; {state.STAGE_ASK.format(packer='platform', env=env)}")
    assert "addressed to lane platform, which packs the plat-usw2-staging cuts, never to root" in state.stage_ask(data, env)
    state.slot_packer(data, env, "")
    assert state.stage_ask(data, env) == ""


def test_a_staging_slot_goes_to_its_first_claimer_and_then_to_each_queued_job_in_order_as_each_lease_ends() -> None:
    moment = datetime(2026, 11, 15, 22, 5, tzinfo=UTC)
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "waits": []} for job_id in ("j1", "j2", "j3")}}
    assert state.slot_claim(data, "plat-usw2-staging", "j1", 30, [], None, moment).startswith("GO j1 holds the plat-usw2-staging slot until")
    assert state.slot_claim(data, "plat-usw2-staging", "j2", 25, [], None, moment).startswith("QUEUED j2 for the plat-usw2-staging slot, place 1, behind j1")
    state.slot_claim(data, "plat-usw2-staging", "j3", 10, [], None, moment)
    with pytest.raises(state.DriveError, match="already holds or waits"):
        state.slot_claim(data, "plat-usw2-staging", "j2", 25, [], None, moment)
    assert state.slot_turns(data, moment + timedelta(minutes=10)) == []
    data["jobs"]["j1"]["state"] = "done"
    data["jobs"]["j2"]["state"] = "stopped"
    [(env, freed, kind, _), (_, given, go, line)] = state.slot_turns(data, moment + timedelta(minutes=20))
    assert (env, freed, kind, given, go, line.startswith("GO staging plat-usw2-staging until")) == ("plat-usw2-staging", "j1", "freed", "j3", "go", True)
    assert state.slot_release(data, "plat-usw2-staging", "j3") == "j3 gave back the plat-usw2-staging slot"


def test_a_staging_lease_that_runs_past_its_end_extends_once_and_an_incident_tester_jumps_the_queue() -> None:
    moment = datetime(2026, 11, 15, 22, 5, tzinfo=UTC)
    orders = {"o1": {"incident": False, "priority": None, "state": "open"}, "o2": {"incident": True, "priority": None, "state": "open"}}
    data = {"slots": {}, "orders": orders, "jobs": {"j1": {"state": "running", "order": "o1", "carries": [], "parts": [], "goal": "", "waits": ["staging:api/plat-usw2-staging@abc1234"], "said": None, "release_build": None}, "j2": {"state": "waiting", "order": "o1", "carries": [], "parts": [], "goal": "", "waits": [], "said": None}, "j3": {"state": "waiting", "order": "o2", "carries": [], "parts": [], "goal": "", "waits": [], "said": None}}}
    state.slot_claim(data, "plat-usw2-staging", "j1", 30, [], None, moment)
    state.slot_claim(data, "plat-usw2-staging", "j2", 25, [], None, moment)
    assert state.slot_claim(data, "plat-usw2-staging", "j3", 10, [], None, moment).startswith("QUEUED j3 for the plat-usw2-staging slot, place 1, behind j1")
    [(_, held, kind, _)] = state.slot_turns(data, moment + timedelta(minutes=36))
    assert (held, kind, data["slots"]["plat-usw2-staging"]["holder"]["until"]) == ("j1", "extended", state.stamp(moment + timedelta(minutes=65)))
    [(_, freed, kind, _), (_, given, go, _)] = state.slot_turns(data, moment + timedelta(minutes=66))
    assert (freed, kind, given, go, [each["job"] for each in data["slots"]["plat-usw2-staging"]["queue"]]) == ("j1", "freed", "j3", "go", ["j2"])


def test_a_staging_lease_cut_short_by_a_release_for_production_holds_while_its_test_waits_on_that_release_and_runs_its_full_length_after() -> None:
    moment = datetime(2026, 11, 15, 22, 5, tzinfo=UTC)
    build = "https://buildkite.com/forge/release/builds/16950"
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "waits": []} for job_id in ("j1", "j2")}}
    state.slot_claim(data, "plat-usw2-staging", "j1", 30, [], None, moment)
    state.slot_claim(data, "plat-usw2-staging", "j2", 25, [], None, moment)
    held = data["slots"]["plat-usw2-staging"]["holder"]
    held["gave_way_to"] = build
    data["jobs"]["j1"]["waits"] = [build]
    for minutes in (40, 80, 160):
        assert state.slot_turns(data, moment + timedelta(minutes=minutes)) == []
    assert (held["until"], held["extended"]) == (state.stamp(moment + timedelta(minutes=160) + state.CLAIM_WAIT + timedelta(minutes=30)), False)
    data["jobs"]["j1"]["waits"] = []
    assert state.slot_turns(data, moment + timedelta(minutes=190)) == []
    [(_, held_by, kind, _)] = state.slot_turns(data, moment + timedelta(minutes=196))
    assert (held_by, kind) == ("j1", "extended")
    data["jobs"]["j2"]["waits"] = [build]
    [(_, freed, kind, _), (_, given, go, _)] = state.slot_turns(data, moment + timedelta(minutes=226))
    assert (freed, kind, given, go) == ("j1", "freed", "j2", "go")


def test_a_pr_a_bot_or_github_app_opened_is_no_teammates_and_the_drive_may_take_it() -> None:
    assert state.foreign("Forge-AI/monorepo#56030", github.author_login({"__typename": "Bot", "login": "poetic-vulcan"}), []) is None
    assert state.foreign("Forge-AI/monorepo#56030", "renovate[bot]", []) is None
    assert state.foreign("Forge-AI/monorepo#56030", github.author_login({"__typename": "User", "login": "rowan"}), [])


def test_a_free_staging_slot_packs_up_to_six_passes_whose_files_never_overlap_into_one_cut_under_one_lease() -> None:
    moment = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)
    names = [f"j{number}" for number in range(1, 10)]
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "waits": []} for job_id in ["j0", *names]}}
    state.slot_claim(data, "plat-usw2-staging", "j0", 20, [], None, moment)
    files = {"j1": ["a"], "j2": ["a", "b"], "j3": ["c"], "j4": None, "j5": ["d"], "j6": ["e"], "j7": ["f"], "j8": ["g"], "j9": ["h"]}
    for job_id in names:
        state.slot_claim(data, "plat-usw2-staging", job_id, 15 if job_id != "j3" else 40, [f"Forge-AI/monorepo#{job_id[1:]}"], None, moment, files[job_id], [f"stack-{job_id}"])
    data["jobs"]["j0"]["state"] = "done"
    moved = state.slot_turns(data, moment + timedelta(minutes=30))
    held = data["slots"]["plat-usw2-staging"]["holder"]
    assert (held["job"], [rider["job"] for rider in held["riders"]], held["minutes"]) == ("j1", ["j3", "j5", "j6", "j7", "j8"], 40)
    assert [each["job"] for each in data["slots"]["plat-usw2-staging"]["queue"]] == ["j2", "j4", "j9"]
    goes = {job_id: line for _, job_id, kind, line in moved if kind == "go"}
    assert ("Your cut carries Forge-AI/monorepo#3" in goes["j1"], goes["j3"].startswith("RIDE cut j1 on plat-usw2-staging until")) == (True, True)
    for job_id in ("j1", "j3", "j5", "j6", "j7"):
        data["jobs"][job_id]["state"] = "done"
    assert state.slot_turns(data, moment + timedelta(minutes=40)) == []
    assert state.slot_release(data, "plat-usw2-staging", "j8") == "j8 left the plat-usw2-staging slot's cut"
    assert [line for _, _, kind, line in state.slot_turns(data, moment + timedelta(minutes=41)) if kind == "freed"] == ["SLOT plat-usw2-staging freed by j1's cut"]


def test_a_release_jobs_lease_outlives_its_passed_build_and_testers_mid_case_and_a_wrong_go_is_taken_back_without_passing_on_as_on_cut_a3() -> None:
    moment = datetime(2026, 11, 16, 4, 50, tzinfo=UTC)
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "waits": [], "said": None} for job_id in ("j7906", "j7599", "j4708")}}
    env = "plat-usw2-staging"
    state.slot_claim(data, env, "j7906", 64, [], None, moment, starts=True)
    state.slot_claim(data, env, "j7599", 45, [], None, moment)
    data["jobs"]["j7906"]["state"] = "done"
    data["jobs"]["j4708"].update(state="running", waits=[f"staging:api/{env}@b2c430dc047d"])
    assert state.slot_turns(data, moment + timedelta(minutes=8)) == []
    assert data["slots"][env]["holder"]["job"] == "j7906"
    until = state.parse_time(data["slots"][env]["holder"]["until"])
    assert [kind for _, _, kind, _ in state.slot_turns(data, until)] == ["extended"]
    data["jobs"]["j4708"]["state"] = "done"
    assert [(job_id, kind) for _, job_id, kind, _ in state.slot_turns(data, until + timedelta(minutes=1))] == [("j7906", "freed"), ("j7599", "go")]
    state.slot_hold(data, env, "j7906", until + timedelta(hours=1), "a3's testers still run", until + timedelta(minutes=2))
    assert state.slot_take(data, env, "j7599").startswith(f"{env} taken back from j7599, first in its queue again")
    assert (data["slots"][env]["holder"], [each["job"] for each in data["slots"][env]["queue"]]) == (None, ["j7599"])
    assert state.slot_turns(data, until + timedelta(minutes=3)) == []
    with pytest.raises(state.DriveError, match="neither holds nor rides"):
        state.slot_take(data, env, "j7599")


def test_passes_restarting_one_stack_take_turns_and_a_top_coordinators_start_holds_a_cut_of_its_own() -> None:
    moment = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "waits": []} for job_id in ("j0", "j1", "j2", "j3", "j4")}}
    state.slot_claim(data, "plat-usw2-staging", "j0", 20, [], None, moment)
    state.slot_claim(data, "plat-usw2-staging", "j1", 15, ["Forge-AI/monorepo#1"], None, moment, ["a"], ["api"])
    state.slot_claim(data, "plat-usw2-staging", "j2", 15, ["Forge-AI/monorepo#2"], None, moment, ["b"], ["api"])
    assert state.slot_claim(data, "plat-usw2-staging", "j3", 60, [], None, moment, starts=True).startswith("QUEUED j3")
    state.slot_claim(data, "plat-usw2-staging", "j4", 15, ["Forge-AI/monorepo#4"], None, moment, ["c"], ["rsw-cp"])
    data["jobs"]["j0"]["state"] = "done"
    state.slot_turns(data, moment + timedelta(minutes=30))
    held = data["slots"]["plat-usw2-staging"]["holder"]
    assert (held["job"], [rider["job"] for rider in held["riders"]]) == ("j1", ["j4"])
    for job_id in ("j1", "j4"):
        data["jobs"][job_id]["state"] = "done"
    state.slot_turns(data, moment + timedelta(minutes=40))
    data["jobs"]["j2"]["state"] = "done"
    moved = state.slot_turns(data, moment + timedelta(minutes=50))
    assert [(job_id, kind) for _, job_id, kind, _ in moved] == [("j2", "freed"), ("j3", "start")]
    assert data["slots"]["plat-usw2-staging"]["holder"]["riders"] == []


def test_a_priority_job_heads_the_slot_queue_a_failed_pass_keeps_the_lease_and_a_hand_seats_a_queued_job_now() -> None:
    moment = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)
    data = {"slots": {}, "orders": {"o1": {"incident": False, "priority": None, "state": "open"}}, "jobs": {job_id: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "priority": None, "waits": []} for job_id in ("j0", "j1", "j2", "j3")}}
    state.slot_claim(data, "plat-usw2-staging", "j0", 20, [], None, moment)
    state.slot_claim(data, "plat-usw2-staging", "j1", 20, [], None, moment)
    state.slot_claim(data, "plat-usw2-staging", "j2", 20, [], None, moment)
    data["jobs"]["j2"]["priority"] = True
    data["jobs"]["j0"]["state"] = "failed"
    assert state.slot_turns(data, moment + timedelta(minutes=5)) == []
    assert [each["job"] for each in data["slots"]["plat-usw2-staging"]["queue"]] == ["j2", "j1"]
    state.slot_claim(data, "plat-usw2-staging", "j3", 30, [], None, moment, starts=True)
    entry, line = state.slot_hand(data, "plat-usw2-staging", "j3", moment + timedelta(minutes=6))
    assert (entry["starts"], line.startswith("j3 holds the plat-usw2-staging slot until"), [each["job"] for each in data["slots"]["plat-usw2-staging"]["queue"]]) == (True, True, ["j0", "j2", "j1"])
    with pytest.raises(state.DriveError, match="waits in no plat-usw2-staging slot queue"):
        state.slot_hand(data, "plat-usw2-staging", "j9", moment)


def test_a_pass_on_an_order_joined_to_a_priority_order_or_a_cut_naming_its_tester_jumps_the_staging_queue() -> None:
    moment = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
    thread = ["D0C4HQWPLCC", "1794774184.502879"]
    orders = {
        "o2046": {"incident": False, "priority": "Priority o2046", "state": "open", "joins": None, "thread": thread},
        "o2270": {"incident": False, "priority": None, "state": "open", "joins": "o2046", "thread": None},
        "o2299": {"incident": False, "priority": None, "state": "open", "joins": None, "thread": thread},
        "o1513": {"incident": False, "priority": None, "state": "open", "joins": None, "thread": None},
    }
    job = lambda order, goal="": {"state": "waiting", "order": order, "carries": [], "goal": goal, "priority": None, "waits": []}
    data = {"slots": {}, "orders": orders, "jobs": {"j0": job("o1513"), "j1": job("o1513"), "j2": job("o2270"), "j3": job("o2299"), "j4": job("o1513", "follow staging cut 6 for testers j2"), "j5": job("o1513", "follow staging cut 7 for testers j1")}}
    data["jobs"]["j4"]["brief_flags"] = data["jobs"]["j5"]["brief_flags"] = {"release": "tools/ci release start api --env plat-usw2-staging --order 23eefb9"}
    for job_id in data["jobs"]:
        state.slot_claim(data, "plat-usw2-staging", job_id, 20, [], None, moment)
    assert [each["job"] for each in data["slots"]["plat-usw2-staging"]["queue"]] == ["j2", "j3", "j4", "j1", "j5"]


def test_a_slot_holder_has_its_start_once_its_job_kept_a_release_build_or_waits_on_staging_serving_its_cut() -> None:
    data = {"jobs": {"j1": {"waits": [], "release_build": None}, "j2": {"waits": ["staging:api/plat-usw2-staging@abc1234"], "release_build": None}, "j3": {"waits": [], "release_build": "https://buildkite.com/forge/release/builds/17522"}}}
    assert [state.cut_started(data, {"job": job_id}, "plat-usw2-staging") for job_id in ("j1", "j2", "j3")] == [False, True, True]
    assert state.cut_started(data, {"job": "j2"}, "plat-usw2-sandbox") is False


def test_a_pr_whose_staging_tester_waits_on_a_cut_is_owed_by_the_slot_queue_and_one_waiting_on_anything_else_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    part = "staging test of #42 before it lands"
    data = {
        "slots": {"plat-usw2-staging": {"holder": None, "queue": [{"job": "j3"}]}},
        "orders": {"o1": {"parts": {part: {}}, "jobs": ["j1", "j2", "j3"]}},
        "jobs": {"j1": {"waits": [], "state": "waiting"}, "j2": {"waits": ["staging:api/plat-usw2-staging@abc1234"], "state": "waiting"}, "j3": {"waits": [], "state": "waiting"}},
        "prs": {"Forge-AI/monorepo#42": {"waits": [f"o1:{part}"], "job": "j1"}, "Forge-AI/monorepo#43": {"waits": ["j1"], "job": "j1"}},
    }
    for tester, owed in (("j2", True), ("j3", True), ("j1", False)):
        monkeypatch.setattr(state, "part_tester", lambda data, order_id, named, tester=tester: tester)
        assert state.staging_owed(data, "Forge-AI/monorepo#42") is owed
    assert state.staging_owed(data, "Forge-AI/monorepo#43") is False


def test_changed_since_names_each_row_committed_after_a_revision_and_none_once_the_table_no_longer_reaches_back(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("demo")
    with edit("demo"):
        pass
    rev, _ = state.changed_since("demo", 0)
    with edit("demo") as data:
        data["terminal"] = "term-1"
    newest, rows = state.changed_since("demo", rev)
    assert (newest > rev, rows) == (True, {("", "terminal")})
    monkeypatch.setattr(state, "CHANGES_KEPT", 1)
    with edit("demo") as data:
        data["binder"] = "term-2"
    with edit("demo") as data:
        data["binder"] = "term-3"
    assert state.changed_since("demo", rev)[1] is None


def test_a_slot_held_for_a_job_goes_to_no_other_job_of_its_order_and_frees_when_the_hold_ends_and_an_order_is_refused() -> None:
    moment = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
    orders = {order_id: {"incident": False, "priority": None, "state": "open"} for order_id in ("o1", "o1339")}
    job = lambda order: {"state": "waiting", "order": order, "carries": [], "goal": "", "priority": None, "waits": []}
    data = {"slots": {}, "orders": orders, "jobs": {"j1": job("o1"), "j2": job("o1339"), "j3": job("o1339")}}
    with pytest.raises(DriveError, match="names one job, never an order"):
        state.slot_hold(data, "plat-usw2-sandbox", "o1339", moment + timedelta(hours=2), "release 3 rehearsal (730930)", moment)
    assert state.slot_hold(data, "plat-usw2-sandbox", "j2", moment + timedelta(hours=2), "release 3 rehearsal (730930)", moment) == "plat-usw2-sandbox held for j2 until 2026-11-16T04:00:00+00:00: release 3 rehearsal (730930)"
    assert state.slot_claim(data, "plat-usw2-sandbox", "j1", 20, [], None, moment).startswith("QUEUED j1 for the plat-usw2-sandbox slot, place 1, held for j2")
    assert state.slot_claim(data, "plat-usw2-sandbox", "j3", 20, [], None, moment).startswith("QUEUED j3 for the plat-usw2-sandbox slot, place 2, held for j2")
    assert state.slot_turns(data, moment + timedelta(minutes=1)) == []
    assert state.slot_claim(data, "plat-usw2-sandbox", "j2", 30, [], None, moment).startswith("GO j2 holds the plat-usw2-sandbox slot")
    data["jobs"]["j2"]["state"] = "done"
    moved = state.slot_turns(data, moment + timedelta(minutes=40))
    assert ([kind for _, _, kind, _ in moved], data["slots"]["plat-usw2-sandbox"]["holder"]) == (["freed"], None)
    moved = state.slot_turns(data, moment + timedelta(hours=2))
    assert [(job_id, kind) for _, job_id, kind, _ in moved] == [("j2", "unheld"), ("j1", "go")]


def test_a_tester_riding_another_jobs_cut_takes_no_go_and_gets_its_ride_at_that_cuts_start(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    monkeypatch.setattr(state, "staging_parts", lambda data, job: [("o1", "staging test")] if job["goal"] else [])
    monkeypatch.setattr(state, "tested_prs", lambda data, job: [f"Forge-AI/monorepo#{job['goal']}"] if job["goal"] else [])
    staged = "staging:sandsql/plat-usw2-sandbox@27218dde4c11"
    job = lambda goal="", waits=(), said="": {"state": "waiting", "order": "o1", "carries": [], "goal": goal, "priority": None, "waits": list(waits), "said": said, "release_build": None, "updated": "2026-11-15T06:00:00+00:00"}
    data = {
        "slots": {},
        "orders": {"o1": {"incident": False, "priority": None, "state": "open"}},
        "prs": {f"Forge-AI/monorepo#{number}": {"state": "open", "tap": {"label": "Approve"}, "head": head} for number, head in (("1", "27218dde4c11aa"), ("2", "ffff000011112222"), ("3", "27218dde4c11aa"), ("4", "27218dde4c11aa"))},
        "jobs": {
            "j6636": job(),
            "j7013": job("1", [staged, "j6636"]),
            "j7619": job("2", [staged]),
            "j7691": job("3", [staged], "WAITING staging on root START naming it"),
            "j7700": job("4", [staged]),
        },
    }
    assert {job_id: state.rides_cut(data, job_id) for job_id in data["jobs"]} == {"j6636": None, "j7013": "j6636", "j7619": "commit 27218dde4c11", "j7691": "root", "j7700": None}
    assert [line.split(" queued ")[1].split(",")[0] for line in state.queue_waiters(data)] == ["j7700"]
    assert state.slot_claim(data, "plat-usw2-sandbox", "j7013", 20, ["Forge-AI/monorepo#1"], None, moment).startswith("QUEUED j7013 for the plat-usw2-sandbox slot, place 2, riding the cut j6636 starts")
    state.slot_claim(data, "plat-usw2-sandbox", "j7691", 20, ["Forge-AI/monorepo#3"], None, moment)
    data["slots"]["plat-usw2-sandbox"]["queue"].insert(0, data["slots"]["plat-usw2-sandbox"]["queue"].pop(1))
    moved = state.slot_turns(data, moment)
    assert [(job_id, kind) for _, job_id, kind, _ in moved] == [("j7700", "go")]
    assert [each["job"] for each in data["slots"]["plat-usw2-sandbox"]["queue"]] == ["j7013", "j7691"]
    data["jobs"]["j7700"]["state"] = "done"
    state.slot_turns(data, moment + timedelta(minutes=1))
    state.slot_claim(data, "plat-usw2-sandbox", "j6636", 60, [], None, moment + timedelta(minutes=2), starts=True)
    assert data["slots"]["plat-usw2-sandbox"]["holder"]["job"] == "j6636"
    assert state.slot_turns(data, moment + timedelta(minutes=4)) == []
    data["jobs"]["j6636"]["release_build"] = "https://buildkite.com/forge/release/builds/17600"
    [(_, rider, kind, line)] = state.slot_turns(data, moment + timedelta(minutes=5))
    assert (rider, kind, line.startswith("RIDE cut j6636 on plat-usw2-sandbox")) == ("j7013", "go", True)
    assert [each["job"] for each in data["slots"]["plat-usw2-sandbox"]["holder"]["riders"]] == ["j7013"]
    assert [each["job"] for each in data["slots"]["plat-usw2-sandbox"]["queue"]] == ["j7691"]


def test_a_running_job_queued_for_a_builder_slot_by_its_mac_line_has_a_place_in_line() -> None:
    from drive import place

    created = "2026-11-16T02:00:00-08:00"
    data = {"orders": {"o1": {"project": "P", "workstream": "W", "updated": created, "state": "open", "priority": None, "incident": False}}, "jobs": {
        "j1": {"kind": "builder", "order": "o1", "parts": [], "carries": [], "state": "running", "waits": [state.SLOT_WAIT], "created": created, "where": "local"},
        "j2": {"kind": "builder", "order": "o1", "parts": [], "carries": [], "state": "done", "waits": [state.SLOT_WAIT], "created": created, "where": "local"},
    }}
    assert place.positions(data) == {"j1": "1 of 1 in line"}


def test_a_tester_waiting_on_a_cut_with_no_slot_place_is_queued_at_its_waiting_time_and_a_cut_that_never_started_returns_its_riders(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    monkeypatch.setattr(state, "staging_parts", lambda data, job: [("o1", "staging test of #55891 before it lands")] if job is data["jobs"]["j4862"] else [])
    monkeypatch.setattr(state, "tested_prs", lambda data, job: ["Forge-AI/monorepo#55891"])
    orders = {"o1": {"incident": False, "priority": None, "state": "open"}}
    job = lambda **extra: {"state": "waiting", "order": "o1", "carries": [], "goal": "", "priority": None, "waits": [], "release_build": None, "updated": "2026-11-15T06:00:00+00:00"} | extra
    data = {"slots": {}, "orders": orders, "prs": {"Forge-AI/monorepo#55891": {"state": "open", "tap": None}}, "jobs": {"j1": job(updated="2026-11-16T01:00:00+00:00"), "j2": job(), "j4862": job(waits=["staging:api/plat-usw2-staging@abc1234"])}}
    state.slot_claim(data, "plat-usw2-staging", "j1", 20, [], None, moment)
    assert state.queue_waiters(data) == ["SLOT plat-usw2-staging queued j4862, whose WAITING on a staging cut held no place in its queue, at its place from 2026-11-15T06:00:00+00:00"]
    assert state.queue_waiters(data) == []
    state.slot_claim(data, "plat-usw2-staging", "j2", 20, [], None, moment + timedelta(minutes=1))
    data["slots"]["plat-usw2-staging"]["holder"]["extended"] = True
    [(_, freed, kind, line), (_, given, go, _)] = state.slot_turns(data, moment + timedelta(minutes=60))
    assert (freed, kind, "which never started, so j1 went back to their places in its queue" in line, given) == ("j1", "freed", True, "j4862")
    assert [each["job"] for each in data["slots"]["plat-usw2-staging"]["queue"]] == ["j1", "j2"]


def test_while_a_slot_hold_stands_only_a_tester_whose_pr_has_his_approve_is_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "staging_parts", lambda data, job: [("o1", "staging test")])
    monkeypatch.setattr(state, "tested_prs", lambda data, job: [f"Forge-AI/monorepo#{job['goal']}"])
    job = lambda number: {"state": "waiting", "order": "o1", "carries": [], "goal": number, "waits": ["staging:api/plat-usw2-staging@abc1234"], "updated": "2026-11-15T06:00:00+00:00"}
    data = {"slots": {}, "jobs": {"j1": job("1"), "j2": job("2")}, "prs": {"Forge-AI/monorepo#1": {"state": "open", "tap": {"label": "Approve"}}, "Forge-AI/monorepo#2": {"state": "open", "tap": None}}}
    assert [line.split(" queued ")[1].split(",")[0] for line in state.queue_waiters(data, approved_only=True)] == ["j1"]
    assert [line.split(" queued ")[1].split(",")[0] for line in state.queue_waiters(data)] == ["j2"]


def test_the_mac_queue_puts_a_priority_orders_waiting_job_first(state_root: Path) -> None:
    make("demo")
    with edit("demo") as data:
        data["orders"]["o1"] = state.new_order("test on a Mac", "terminal", None, {}, state.stamp())
        data["orders"]["o2"] = data["orders"]["o1"] | {"priority": True}
        data["jobs"] |= {job_id: state.new_job(order, "builder", "test on a Mac", [], state.stamp()) | {"state": "waiting", "waits": waits} for job_id, order, waits in (("j1", "o1", ["mac-box:any"]), ("j2", "o2", ["mac-box:any"]), ("j3", "o1", ["staging:api/plat-usw2-staging@abc"]))}
    assert state.mac_queue(state.load("demo")) == ["j2", "j1"]


def test_the_reservation_member_of_a_sprite_the_watch_adopted_with_no_dispatch_or_agent_names_its_death_by_workspace_as_j299s_keyerror_did_not() -> None:
    ended = {"dispatch": "ctx_5da014b26c2c", "workspace": "pool-zrdgqr6gjkon2ryt", "why": "ended by drive job end", "at": "2026-11-15T20:26:22-08:00"}
    adopted = {"dispatch": None, "workspace": "pool-nkqalkgmmxr2w3yw", "agent": None, "why": "adopted", "at": "2026-11-15T21:33:40-08:00", "reclaim": {"pid": 23327}}
    mac = {"dispatch": None, "workspace": None, "agent": "a1", "why": "its turn ended", "at": "2026-11-15T22:00:00-08:00"}
    data = {"jobs": {"j299": {"deaths": [ended, adopted, mac]}}}
    rows = [state.death_row("j299", death, "reclaim") for death in (ended, adopted, mac)]
    assert rows == [
        ("jobs", "j299", "deaths", "dispatch=ctx_5da014b26c2c", "reclaim"),
        ("jobs", "j299", "deaths", "workspace=pool-nkqalkgmmxr2w3yw", "reclaim"),
        ("jobs", "j299", "deaths", "agent=a1", "reclaim"),
    ]
    assert [state.record_at(data, row) for row in rows] == [ended, adopted, mac]


def test_a_reporters_fix_live_note_is_owed_only_once_the_part_carrying_the_reported_bug_reads_delivered_by_its_own_proof_never_on_another_jobs_as_o2s_was(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("demo")
    data = state.load("demo")
    link = "https://poetic-technology.slack.com/archives/C0CELUMJ6MU/p1794541608044529"
    reported, ported = f"<{link}> Iris cannot see screenshots via journal in chat", "port of #54725: prefill a plain value"
    created = "2026-11-12T10:00:00-08:00"
    data["orders"] = {"o1": state.new_order("cut over to the new tree", "slack:D123/1794600000.000100", ("D123", "1794600000.000100"), {reported: state.new_part(), ported: state.new_part()}, created) | {"kind": "build", "jobs": ["j1", "j2"]}}
    data["orders"]["o1"]["reporters"] = [{"link": link, "channel": "C0CELUMJ6MU", "thread": "1794541608.044529", "told": None}]
    data["jobs"] = {"j1": state.new_job("o1", "builder", "fix the screenshots", [reported], created), "j2": state.new_job("o1", "builder", "port #54725", [ported], created)}
    monkeypatch.setattr(state, "fix_proof", lambda data, order: "https://github.com/Forge-AI/monorepo/pull/56406 live: /repro/j5422.py exits 0")
    data["jobs"]["j2"].update(state="done", said="DONE j2 live: /repro/j2.py exits 0")
    data["orders"]["o1"]["parts"][ported].update(posted="j2 live: /repro/j2.py exits 0", posted_by="j2")
    other = state.may_tell(data, "o1")
    data["jobs"]["j1"].update(state="done", said="DONE j1 live: /repro/j1.py exits 0")
    data["orders"]["o1"]["parts"][reported].update(posted="j1 live: /repro/j1.py exits 0", posted_by="j1")
    assert (other, state.reported_parts(data["orders"]["o1"], data["orders"]["o1"]["reporters"][0]), state.may_tell(data, "o1")) == (False, [reported], True)


def test_a_job_not_running_keeps_its_mac_box_only_under_a_live_slot_hold_for_it(state_root: Path) -> None:
    make("demo")
    with edit("demo") as data:
        data["orders"]["o1"] = state.new_order("test on a Mac", "terminal", None, {}, state.stamp())
        data["jobs"] |= {job_id: state.new_job("o1", "builder", f"pass naming {other} and box {other}-mac", [], state.stamp()) | {"state": "waiting"} for job_id, other in (("j1", "j2"), ("j2", "j1"), ("j3", "j1"))}
        state.slot_hold(data, "dashboard/plat-usw2-staging", "j1", state.now() + timedelta(hours=1), "cut 5 runs on j1's box", state.now())
        state.slot_hold(data, "api/plat-usw2-staging", "j3", state.now() + timedelta(hours=1), "cut 6", state.now())
        data["slots"]["api/plat-usw2-staging"]["hold"]["until"] = state.stamp(state.now() - timedelta(minutes=1))
    data = state.load("demo")
    assert [job_id for job_id in ("j1", "j2", "j3") if state.box_wanted(data, job_id)] == ["j1"]



def test_an_unproven_line_keeps_a_staging_tests_passing_cases_from_delivering_its_part_until_the_coordinator_accepts_that_line_as_root_did_j4852s(
    named_repo: None, state_root: Path
) -> None:
    data = staging_drive("j1")
    unproven = "whether case 3's call met a 503 and was resent: retry.Do logs no tries"
    staging_return(data, "j1", "DONE #42's staging test passed", f"CASE 1 pass (W2m c2475b75c178): the CronJobs run the cut's image\nUNPROVEN: {unproven}\n")
    held = staging_reads(data)
    report = f"DONE #42's staging test passed\nCHECK: {PROOF}\nCASE 1 pass (W2m c2475b75c178): the CronJobs run the cut's image\nUNPROVEN: {unproven}\n"
    left = state.open_unproven(data["jobs"]["j1"], report)
    data["jobs"]["j1"]["accepted_unproven"].append({"line": unproven, "why": "CI carries the resend", "by": "root", "at": state.stamp()})
    state.take_result(data, "j1", "DONE #42's staging test passed", report)
    none = state.open_unproven(data["jobs"]["j1"], "DONE x\nUNPROVEN: none\n")
    assert (held, left, staging_reads(data), none) == ((False, None, "j1"), [unproven], (True, PROOF, "j1"), [])


def test_a_staging_case_run_on_a_cut_that_does_not_carry_the_prs_head_is_no_pass_naming_that_cut_and_one_on_cuts_that_carry_it_passes(
    named_repo: None, state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = staging_drive("j1")
    url, head = "https://github.com/Forge-AI/monorepo/pull/42", "b4df0ca15210aaaabbbbccccddddeeeeffff0000"
    data["prs"] = {"Forge-AI/monorepo#42": state.new_pr(url, "j2", "o1") | {"head": head}}
    cases = "CASE 1 pass (W2m c2475b75c178): the CronJobs run the cut's image\nCASE 2 pass (W2m): the dry run succeeded\nCASE 3 pass (D1b 9c560ac70113, #42 alone on dev): the drain resent\n"
    report = f"DONE #42 at {head[:12]} passed\nCHECK: {PROOF}\n{cases}"
    asked: list[tuple[str, str, str]] = []
    carried = {"c2475b75c178"}
    monkeypatch.setattr(github, "carries", lambda clone, commit, number, sha: asked.append((commit, number, sha)) or commit in carried)
    misses = cli.cut_misses(data, "j1", report)
    data["jobs"]["j1"]["cut_misses"] = misses
    state.take_result(data, "j1", "DONE #42 passed", report)
    missed = (staging_reads(data), state.unpassed(data["orders"]["o1"]["parts"][STAGING]))
    carried.add("9c560ac70113")
    data["jobs"]["j1"]["cut_misses"] = cli.cut_misses(data, "j1", report)
    state.take_result(data, "j1", "DONE #42 passed", report)
    assert sorted(set(asked)) == [("9c560ac70113", "42", head), ("c2475b75c178", "42", head)]
    assert missed == ((False, None, "j1"), f"CASE 3: fail: cut D1b 9c560ac70113 does not carry Forge-AI/monorepo#42 at {head[:12]}")
    assert staging_reads(data) == (True, PROOF, "j1")


def test_a_cut_carries_a_pr_head_in_its_history_or_as_the_commit_a_packed_cut_names_it_by(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True).stdout.strip()
    clone.mkdir()
    git("init", "-q", "-b", "dev")
    git("commit", "-q", "--allow-empty", "-m", "dev")
    git("checkout", "-q", "-b", "pr")
    git("commit", "-q", "--allow-empty", "-m", "the fix")
    head = git("rev-parse", "HEAD")
    git("checkout", "-q", "dev")
    git("checkout", "-q", "-b", "packed")
    git("commit", "-q", "--allow-empty", "-m", f"staging packed-w2m on dev 70818c48333b: #42 at {head[:12]}, tested by j1 (j2)")
    packed = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "other", "dev")
    git("commit", "-q", "--allow-empty", "-m", "staging packed-w2m on dev 70818c48333b: #43 at 0123456789ab, tested by j1 (j2)")
    other = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "merged", "dev")
    git("merge", "-q", "--no-ff", "-m", "merge", "pr")
    merged = git("rev-parse", "HEAD")
    assert [github.carries(str(clone), cut, "42", head) for cut in (packed, other, merged)] == [True, False, True]
