import hashlib
import itertools
import json
import os
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import LIVE_WORKER, Drawing, cloned, git_in, staged
from drive import cli, github, orca, place, reads, render, state, watch

pytestmark = pytest.mark.usefixtures("ccx_worktrees")

Run = Callable[..., tuple[int, str]]
T0 = state.parse_time("2026-11-09T07:30:00-08:00")
RUN = "run_drive0001"
FRESH_RUN = "run_relaunch1"
DRIVE_RUN = "run_coordinator1"
DISPATCH = "ctx_lane0001"
PLACE_TOOLS = orca.place_tools
BREAK_GLASS_READ = 'DRIVE break-glass j1 --stack sandsql/plat-usw2-prod --reason "drop the stranded copies" -- psql -c "drop table copy_1"'
ENDED_READER = 99_999_999
SEQUENCE = itertools.count(1)
FINISHED_WORKER = {
    "worker": {"state": "succeeded", "startOptions": {"worktree": "id:repo-1::/home/sprite/monorepo"}},
    "projection": {"liveness": {"verdict": "live"}, "outcome": "succeeded", "attention": {"categories": ["root_completion"]}},
    "terminal": {"handle": "term_lane", "branch": "refs/heads/ci-mac-box-prove", "connected": True},
    "dispatch": {"runId": RUN, "taskId": "task_1", "completedAt": state.stamp(T0 - timedelta(minutes=10))},
}
ROOT_DISPATCH = "ctx_ab821100f80b"
ROOT_WORKER = {
    "worker": {"state": "ready", "startOptions": {"worktree": "path:/work/root-tooling"}},
    "dispatch": {"runId": RUN, "taskId": "task_root"},
    "projection": {"liveness": {"verdict": "live"}, "outcome": "in_progress", "attention": {"categories": []}},
    "terminal": {"handle": "term_root", "lastOutputAt": None, "connected": True},
}
SPRITE_DISPATCH = "ctx_sprite0001"
SPRITE_WORKER = {
    "worker": {"state": "ready", "startOptions": {"worktree": "id:repo-1::/home/sprite/monorepo", "serverName": "pool-sprite-one"}},
    "dispatch": {"runId": RUN, "taskId": "task_sprite"},
    "projection": {
        "host": {"kind": "remote", "id": "env-sprite-one"},
        "liveness": {"verdict": "live"},
        "outcome": "in_progress",
        "attention": {"categories": []},
    },
    "server": {"environmentId": "env-sprite-one", "name": "pool-sprite-one"},
    "terminal": {"handle": "term_sprite", "lastOutputAt": None},
}
SPRITE_LANE = {"where": "remote", "route": None, "dispatch": SPRITE_DISPATCH, "workspace": "pool-sprite-one", "environment": "pool-sprite-one"}
REAL_LOCAL_GAP = watch.local_gap
REAL_LAST_WRITE = watch.last_write
UNSENT = (
    "no worker started on Sprite pool-one: orca orchestration worker-start failed twice before Orca recorded it, with"
    " runtime_error: Timed out waiting for the remote Orca runtime; its terminal term_lane waits idle, and launching this"
    " brief again retries worker-start in it"
)
DESIGN_PICK = "Root cause: none: a design pick, with no defect behind it"
ASK_IN_SHAPE = f"""ASK Where should the nightly store copy run?
Drive tooling: your pick decides where the nightly store copy runs; it reads every store once, on the primary or on its own worker.
- On the primary, beside the queries it serves (recommended)
  Each night `supervisor/copies.rs` on the primary copies every store once to the team's bucket between the queries it serves, for a restore to read; each query waits behind a running copy.
- On a separate worker that reads the bucket
  Each night a new worker in `supervisor/worker.rs` reads every store from the team's bucket and copies it there, so the primary serves only queries; each cluster runs one more process.
Findings:
{DESIGN_PICK}
Holds: none: no option changes code any job builds
One copy took 40 minutes on the primary.
"""


def kept_ask(drive: str) -> Path:
    return state.drive_dir(drive) / "asks" / f"j1-{state.ask_hash(ASK_IN_SHAPE)}.md"


def message(kind: str, text: str, **overrides: str | None) -> dict:
    base = {
        "id": f"msg_{kind}",
        "from_handle": f"dispatch:{DISPATCH}",
        "to_handle": f"run:{RUN}",
        "subject": text.splitlines()[0],
        "body": text,
        "type": kind,
        "sequence": next(SEQUENCE),
        "read": 0,
        "created_at": "2026-11-10T03:30:00Z",
    }
    return {**base, **overrides}


@pytest.fixture
def remote(run: Run, demo: str) -> str:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the remote builder")
    run("job", "add", "builder", "--order", "o1", "--goal", "remote builders", "--remote")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(run=RUN, dispatch=DISPATCH, ask=None, report=None)
        data["run"] = DRIVE_RUN
    return demo


@pytest.fixture
def rooted(run: Run, demo: str) -> str:
    """A root started by hand with the orca skill's local route and recorded with drive job set --agent <its dispatch>, as drives saved it before local lanes."""
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(agent=ROOT_DISPATCH, where="local", said="root-tooling: dispatch ctx_ab821100f80b")
        data["run"] = DRIVE_RUN
    return demo


def reply_refused(calls: list[tuple[str, ...]], code: str, message: str) -> Callable[..., dict]:
    def call(*args: str) -> dict:
        calls.append(args)
        if args[0] != "reply":
            return {}
        refusal = {"ok": False, "error": {"code": code, "message": message.format(question=args[2])}}
        return orca.result_of("orchestration reply", subprocess.CompletedProcess(["orca"], 0, json.dumps(refusal), ""))

    return call


def answered_elsewhere(calls: list[tuple[str, ...]]) -> Callable[..., dict]:
    return reply_refused(calls, "answer_conflict", "Question {question} already has a different answer.")


def deliver(monkeypatch: pytest.MonkeyPatch, *batches: list[dict]) -> list[str]:
    queue, calls = list(batches), []

    def fake(run: str, after: int) -> list[dict]:
        calls.append(run)
        return queue.pop(0) if queue else []

    monkeypatch.setattr(orca, "mail", fake)
    return calls


@pytest.fixture(autouse=True)
def reads_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each read the watch starts runs to its end before the pass goes on, as a reader process that ended before the next pass."""
    monkeypatch.setattr(watch, "spawn_reader", lambda name, job_id, message_id: watch.answer_read(name, job_id, message_id) and ENDED_READER)


@pytest.mark.parametrize(
    ("share", "local_builders", "where"),
    [(0.81, 0, "remote"), (0.5, 3, "remote"), (0.2, 8, "remote"), (0.2, 7, "local")],
)
def test_verdict_sends_a_builder_remote_over_the_limit_or_the_cap(
    share: float, local_builders: int, where: str
) -> None:
    assert place.verdict(share, 0.5, {f"j{number}": f"j{number} on o1" for number in range(local_builders)})[0] == where


def test_place_counts_only_running_builders_on_this_mac(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.2)
    monkeypatch.setenv(place.LIMIT_ENV, "0.5")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "many")
    for _ in range(7):
        run("job", "add", "builder", "--order", "o1", "--goal", "here", "--agent", "a")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    run("job", "add", "reader", "--order", "o1", "--goal", "read", "--agent", "r")
    run("job", "add", "builder", "--order", "o1", "--goal", "parked", "--agent", "p")
    with state.edit("demo") as data:
        data["jobs"]["j10"]["state"] = "waiting"
    code, out = run("place", "--json")
    assert (code, json.loads(out)["where"], json.loads(out)["local_builders"]) == (0, "local", 7)
    run("job", "add", "builder", "--order", "o1", "--goal", "eighth", "--agent", "e")
    monkeypatch.setattr(watch, "last_write", lambda job, data: (T0, None))
    wrote = render.when(state.stamp(T0), seconds=True)
    counted = "; ".join(f"j{number} on o1, its agent {agent} last wrote at {wrote}" for number, agent in [*((n, "a") for n in range(1, 8)), (11, "e")])
    assert run("place")[1] == f"remote: 8 builders already run on this Mac, its cap of 8 ({counted})"


def test_place_counts_a_builder_here_only_while_its_agent_is_in_its_turn(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(watch, "last_write", REAL_LAST_WRITE)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "three")
    turns = {
        "a1": {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "Bash"}]}},
        "a2": {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "WAITING on Forge-AI/monorepo#7"}]}},
        "a3": {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Should the old flag stay?"}]}},
    }
    for agent in turns:
        run("job", "add", "builder", "--order", "o1", "--goal", agent, "--agent", agent)
    data = state.load(demo)
    for job in data["jobs"].values():
        path = watch.transcript(watch.session_of(job, data), job["agent"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(turns[job["agent"]]) + "\n")
        os.utime(path, (T0.timestamp(), T0.timestamp()))
    wrote = render.when(state.stamp(T0.astimezone()), seconds=True)
    assert place.local_builders(data, state.now()) == {"j1": f"j1 on o1, its agent a1 last wrote at {wrote}"}


def test_place_counts_a_builder_lane_started_on_this_mac_while_it_launches_or_runs(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.2)
    monkeypatch.setenv(place.LIMIT_ENV, "0.5")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "many")
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER if dispatch == SPRITE_DISPATCH else LIVE_WORKER)
    for goal in ("lane here", "lane here ended", "launching here", "never started here", "on a Sprite", "recorded by hand on a Sprite"):
        run("job", "add", "builder", "--order", "o1", "--goal", goal, "--remote")
    run("job", "set", "j1", "--agent", "ctx_here1")
    run("job", "set", "j2", "--agent", "ctx_here2")
    run("job", "set", "j6", "--agent", SPRITE_DISPATCH)
    with state.edit(demo) as data:
        state.lane_died(data["jobs"]["j2"], "ctx_here2", "its worker finished", state.now())
        data["jobs"]["j3"].update(route=state.LOCAL_ROUTE, launching=state.stamp())
        data["jobs"]["j4"].update(route=state.LOCAL_ROUTE, launching=state.stamp(state.now() - timedelta(hours=1)))
        data["jobs"]["j5"].update(dispatch="ctx_sprite5", workspace="pool-one", environment="env-one")
    assert json.loads(run("place", "--json")[1])["local_builders"] == 2


@pytest.mark.parametrize(("refusal", "counted"), [(orca.Unstarted, {}), (orca.OrcaError, {"j1": "j1 on o1, its Orca lane launching since "})])
def test_a_local_launch_that_started_no_worker_stops_counting_as_a_builder_here(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: type[orca.OrcaError], counted: dict[str, str]
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)

    def refused(*args: object) -> dict:
        raise refusal("orca-worker-launch.sh exited 1")

    monkeypatch.setattr(orca, "launch", refused)
    assert cli.main(["job", "launch", "j1", "brief.md", "--local"]) == 2
    builders = place.local_builders(state.load(demo), state.now())
    assert {job_id: line[: len(counted[job_id])] for job_id, line in builders.items()} == counted


def test_a_launch_orca_makes_no_run_for_exits_non_zero_saying_no_worker_started_and_stops_reading_as_launching(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    unavailable = "orca orchestration run-create: runtime_unavailable: Could not read Orca runtime metadata. Start the Orca app first."

    def no_runtime(objective: str) -> str:
        raise orca.OrcaError(unavailable, "runtime_unavailable")

    monkeypatch.setattr(orca, "new_run", no_runtime)
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("no launcher runs without a Run"))
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    assert capsys.readouterr().err == f"drive: j1: no worker started, since Orca made it no Run: {unavailable}\n"
    job = state.load(demo)["jobs"]["j1"]
    assert (job["launching"], job.get("dispatch")) == (None, None)


def test_place_skips_a_running_builder_whose_agent_is_gone(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.2)
    monkeypatch.setenv(place.LIMIT_ENV, "0.5")
    monkeypatch.setattr(
        watch, "local_gap", lambda job_id, job, data, moment: f"{job_id}'s agent ended with an earlier session" if job_id == "j1" else None
    )
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "two")
    run("job", "add", "builder", "--order", "o1", "--goal", "gone", "--agent", "a")
    run("job", "add", "builder", "--order", "o1", "--goal", "alive", "--agent", "b")
    assert json.loads(run("place", "--json")[1])["local_builders"] == 1


def test_an_agent_id_moves_a_remote_job_to_this_mac_and_off_its_mail(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = deliver(monkeypatch, [message("question", "READY from the old lane")])
    run("job", "set", "j1", "--agent", "agent-9")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["where"], job["agent"]) == ("local", "agent-9")
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == []
    assert calls == []


def test_job_set_agent_with_the_jobs_own_dispatch_puts_an_agent_held_job_back_on_its_sprite(
    run: Run, remote: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="pool-one", environment="pool-one", terminal="term_lane", agent="a-doctor", where="local")
    assert cli.main(["job", "resume", "j1", "go"]) == 2
    assert capsys.readouterr().err == (
        f"drive: j1 runs on this Mac as agent a-doctor; resume it with SendMessage, or put it back on its lane with drive job set j1 --agent {DISPATCH}, then resume it\n"
    )
    monkeypatch.setattr(
        orca, "worker", lambda dispatch: SPRITE_WORKER | {"server": {"name": "pool-one"}, "terminal": {"handle": "term_lane"}, "dispatch": {"runId": RUN, "taskId": "task_1"}}
    )
    run("job", "set", "j1", "--agent", DISPATCH)
    data = state.load(remote)
    job = data["jobs"]["j1"]
    assert {field: job[field] for field in ("agent", "where", "route", "dispatch", "workspace", "environment", "run", "terminal")} == {
        "agent": None,
        "where": "remote",
        "route": None,
        "dispatch": DISPATCH,
        "workspace": "pool-one",
        "environment": "pool-one",
        "run": RUN,
        "terminal": "term_lane",
    }
    assert cli.remote_job(data, "j1") is job


@pytest.mark.parametrize("recorded", [SPRITE_DISPATCH, DISPATCH], ids=["the job's own dispatch", "another dispatch"])
def test_job_set_agent_records_the_run_task_terminal_and_lane_orca_shows_for_the_dispatch_so_resume_sends_on_its_run(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch, recorded: str
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(dispatch=recorded, task="task_old", terminal="term_old", lane="demo-j1-2", workspace="pool-old", environment="pool-old")
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER | {"dispatch": {"runId": FRESH_RUN, "taskId": "task_sprite"}})
    monkeypatch.setattr(orca, "task_title", lambda run_id, task: {(FRESH_RUN, "task_sprite"): "demo-j1-3"}[run_id, task])
    run("job", "set", "j1", "--agent", SPRITE_DISPATCH)
    job = state.load(remote)["jobs"]["j1"]
    assert {field: job[field] for field in ("agent", "run", "task", "terminal", "lane", *SPRITE_LANE)} == {
        "agent": None,
        "run": FRESH_RUN,
        "task": "task_sprite",
        "terminal": "term_sprite",
        "lane": "demo-j1-3",
        **SPRITE_LANE,
    }
    sent = []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    assert run("job", "resume", "j1", "PR checks failed") == (0, f"j1: sent to dispatch {SPRITE_DISPATCH}")
    assert sent == [("send", "--to", f"dispatch:{SPRITE_DISPATCH}", "--run", FRESH_RUN, "--subject", "From the coordinator", "--body", "PR checks failed")]


def test_launch_composes_the_vm_brief_and_records_the_lane(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Order (his words): ship it\nDrive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    objectives = []
    monkeypatch.setattr(orca, "new_run", lambda objective: objectives.append(objective) or RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), checkout=checkout, run=run_id)
        fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
        return {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "launch", "j1", "brief.md")
    assert (code, out) == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")
    assert (launched["lane"], launched["checkout"], launched["run"]) == ("demo-j1", tmp_path / "worktrees" / "drive-demo", RUN)
    assert launched["brief"].startswith("Remote lane: drive demo, job j1.")
    assert "## On a Sprite" in launched["brief"]
    assert "# Land a change" in launched["brief"]
    assert launched["brief"].endswith("# Brief\n\nOrder (his words): ship it\nDrive: demo, order o1, job j1\n")
    data = state.load(demo)
    assert objectives == ["drive demo j1: ship it"]
    assert data["jobs"]["j1"]["run"] == RUN
    assert data["jobs"]["j1"]["dispatch"] == DISPATCH
    assert "remote dispatch ctx_lane0001" in render.job_line("j1", data["jobs"]["j1"])
    assert run("job", "launch", "j1", "brief.md")[0] == 2


def test_launch_runs_from_the_drives_own_worktree_made_once_with_ccx_and_moved_with_one_fetch_to_the_default_branchs_head_never_the_coordinators_working_tree(
    run: Run, demo: str, tmp_path: Path, ccx_worktrees: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    for goal in ("ship it", "ship it again"):
        run("job", "add", "builder", "--order", "o1", "--goal", goal, "--remote")
    state_folder = state.drive_dir(demo)
    for job_id in ("j1", "j2"):
        (state_folder / f"{job_id}-brief.md").write_text(f"Drive: demo, order o1, job {job_id}\n")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    fetches, pins = [], []
    fetched = github.fetched
    monkeypatch.setattr(github, "fetched", lambda clone, branch: fetches.append(branch) or fetched(clone, branch))
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")

    def launch(drive: str, lane: str, brief: Path, checkout: Path, *rest: object) -> dict:
        pins.append((checkout, git_in(checkout, "rev-parse", "HEAD"), (checkout / "versions.toml").read_text()))
        return dict.fromkeys(fields, DISPATCH)

    monkeypatch.setattr(orca, "launch", launch)
    monkeypatch.chdir(state_folder)
    kept = ccx_worktrees / "drive-demo"
    unlaunchable = cloned(tmp_path, demo, {"versions.toml": "cc-remote 0.27.32\n"})
    assert cli.main(["job", "launch", "j1", "j1-brief.md"]) == 2
    assert capsys.readouterr().err == f"drive: {kept}, the drive's checkout at main {unlaunchable[:12]}, has no .orca.json, so no lane can be cloned from it\n"
    clone = tmp_path / "clone"
    (clone / "versions.toml").write_text("cc-remote 0.27.30\n")
    untouched = (git_in(clone, "rev-parse", "HEAD"), git_in(clone, "status", "--porcelain"))
    pinned = cloned(tmp_path, demo, {".orca.json": "{}", "versions.toml": "cc-remote 0.27.37\n"})
    assert run("job", "launch", "j1", "j1-brief.md")[0] == 0
    repinned = cloned(tmp_path, demo, {"versions.toml": "cc-remote 0.27.38\n"})
    assert run("job", "launch", "j2", "j2-brief.md")[0] == 0
    assert pins == [(kept, pinned, "cc-remote 0.27.37\n"), (kept, repinned, "cc-remote 0.27.38\n")]
    assert fetches == ["main", "main", "main"]
    assert (ccx_worktrees / "made").read_text() == "drive-demo\n"
    assert (git_in(clone, "rev-parse", "HEAD"), git_in(clone, "status", "--porcelain")) == untouched


def test_a_sprite_brief_carries_every_drive_file_it_names_whole_a_lanes_report_kept_only_in_the_drive_included(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = state.drive_dir(demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    for goal in ("research it", "design it", "list it"):
        run("job", "add", "reader", "--order", "o1", "--goal", goal)
    state.keep_reply(demo, "j2", {"created_at": state.stamp(), "id": "msg_design", "type": "worker_done", "subject": "DESIGN the whole design", "body": "DESIGN the whole design\n"})
    state.keep_report(demo, "j1", "DONE the reader's findings", "DONE the reader's findings\nexists: lib/thing.py\n")
    state.keep_report(demo, "j3", "DONE a job the brief only lists as a transcript", "DONE a job the brief only lists as a transcript\n")
    files = {
        folder / "asks" / "j2-0c1d2e3f.md": "ASK Which store?\n",
        state.repro_path(demo, "j1", ".sh"): "exit 1\n",
        folder / "scratch" / "j1" / "notes.md": "the notes\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (folder / "scratch" / "j4").mkdir()
    text = (
        "Order (his words): ship it\nResearched: j1\nPlan: Design j2, whole: reports/j2.md (drive job report j2)\n"
        f"Done when: j3 asked for `{folder}/asks/j2-0c1d2e3f.md` and repro/j1.sh, then ~/{folder.relative_to(folder.parents[1])}/scratch/j1/notes.md.\n"
        "Elsewhere: /other/long-running/other/asks/j9-1a2b3c4d.md\n"
        f"Scratch: {folder}/scratch/j4\nTranscripts:\n- j3 reader: x.jsonl"
    )
    monkeypatch.setattr(Path, "home", lambda: folder.parents[1])
    composed = orca.vm_brief(demo, "j5", text, None)
    assert "# Report of j1, which your brief names\n\nDONE the reader's findings\nexists: lib/thing.py\n" in composed
    assert "# Report of j2, which your brief names\n\nDESIGN the whole design\n" in composed
    assert "# Drive file asks/j2-0c1d2e3f.md, which your brief names\n\nASK Which store?\n" in composed
    assert "# Drive file repro/j1.sh, which your brief names\n\nexit 1\n" in composed
    assert "# Drive file scratch/j1/notes.md, which your brief names\n\nthe notes\n" in composed
    carried = composed.split(orca.BRIEF_HEADING)[0]
    assert (carried.count("\n# Report of j2"), carried.count("\n# Drive file"), "j9-1a2b3c4d" in carried, "a job the brief only lists" in carried) == (1, 3, False, False)
    assert orca.uncarried(demo, "j5", text) is None
    assert orca.brief_of(composed) == text


def test_launch_refuses_a_brief_naming_a_drive_file_the_drive_keeps_no_text_of_and_stamps_nothing(
    run: Run, demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    asked = state.drive_dir(demo) / "asks" / "j1290-702505b0.md"
    (tmp_path / "brief.md").write_text(f"Order (his words): ship it\nPlan: j404 kept the plan\nDone when: his card in {asked} draws\nDrive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    before = state.load(demo)["jobs"]["j1"]
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    refusal = capsys.readouterr().err
    assert refusal == (
        "drive: j1: refused, not launched: its brief names Report of j404; Drive file asks/j1290-702505b0.md, which the drive keeps no text of,"
        " so its lane would stop to ask for each; launch it on a brief file that leaves each out: drive job launch j1 <file>\n"
    )
    assert state.load(demo)["jobs"]["j1"] == before


def test_launch_of_a_brief_naming_a_finished_jobs_report_file_as_j2722s_named_j2102s_carries_its_kept_report_and_launches(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    folder = state.drive_dir(demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it back")
    run("job", "add", "builder", "--order", "o1", "--goal", "read it back again", "--remote")
    state.keep_report(demo, "j1", "DONE the read-back", "DONE the read-back\n- the delete half NOT PROVEN\n")
    (tmp_path / "brief.md").write_text(
        "Order (his words): ship it\n"
        "Goal: rerun the read-back to prove what reports/j1.md left unproven\n"
        f"Where: the earlier result {folder}/reports/j1.md, the research {folder}/scratch/j1/researched.md\n"
        "Drive: demo, order o1, job j2\n"
    )
    (folder / "scratch" / "j1").mkdir(parents=True)
    (folder / "scratch" / "j1" / "researched.md").write_text("the research\n")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    briefs = []
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda drive, lane, brief, *rest: briefs.append(brief.read_text()) or {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j2", "brief.md") == (0, f"j2 started on Sprite workspace-1 as dispatch {DISPATCH}")
    carried = briefs[0].split(orca.BRIEF_HEADING)[0]
    assert "\n# Report of j1, which your brief names\n\nDONE the read-back\n- the delete half NOT PROVEN\n" in carried
    assert (carried.count("\n# Report of j1"), carried.count("\n# Drive file")) == (1, 1)


def test_launch_of_a_brief_naming_a_jobs_repro_by_a_suffix_glob_as_j2817s_named_j2807s_carries_the_file_job_set_kept_and_launches(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    folder = state.drive_dir(demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "research it")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--remote")
    state.keep_report(demo, "j1", "DONE the cause", "DONE the cause\nREPRO: repro/j1.py\n")
    (tmp_path / "steps.py").write_text("raise SystemExit(1)\n")
    assert run("job", "set", "j1", "--repro", str(tmp_path / "steps.py"))[0] == 0
    (tmp_path / "brief.md").write_text(
        "Order (his words): ship it\n"
        f"Done when: on the installed release, {folder}/repro/j1.* exits 0\n"
        "Researched: j1\n"
        "Drive: demo, order o1, job j2\n"
    )
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    briefs = []
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda drive, lane, brief, *rest: briefs.append(brief.read_text()) or {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j2", "brief.md") == (0, f"j2 started on Sprite workspace-1 as dispatch {DISPATCH}")
    carried = briefs[0].split(orca.BRIEF_HEADING)[0]
    assert "\n# Drive file repro/j1.py, which your brief names\n\nraise SystemExit(1)\n" in carried
    assert (carried.count("\n# Report of j1"), carried.count("\n# Drive file")) == (1, 1)


def test_launch_refuses_a_job_on_this_mac_and_a_checkout_without_orca_values(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "here", "--agent", "a")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    assert capsys.readouterr().err == "drive: j1 runs on this Mac as agent a; resume it with SendMessage\n"
    assert run("job", "launch", "j2", "brief.md")[0] == 2
    assert "dispatch" not in state.load(demo)["jobs"]["j2"]


def test_launch_refuses_a_brief_file_that_is_not_there_and_stamps_nothing(
    run: Run, demo: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    before = state.load(demo)["jobs"]["j1"]
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    assert capsys.readouterr().err == f"drive: no brief file at {Path.cwd() / 'brief.md'}\n"
    assert state.load(demo)["jobs"]["j1"] == before


def test_launch_starts_a_job_added_with_no_agent_as_a_remote_lane(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    assert cli.main(["job", "resume", "j1", "next"]) == 2
    assert capsys.readouterr().err == (
        "drive: j1 has no agent and no lane yet: start it on a Sprite with drive job launch j1 <brief>, as an Orca"
        " worker on this Mac with drive job launch j1 <brief> --local, or start an Agent here whose brief ends Drive: <drive>, order <order>, job j1\n"
    )
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j1", "brief.md") == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")
    job = state.load(demo)["jobs"]["j1"]
    assert (job["where"], job["dispatch"]) == ("remote", DISPATCH)


def test_a_launch_holds_no_install_lock_while_its_launcher_runs_and_hands_its_receipt_to_the_newer_install_that_saved_meanwhile(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    held, handed = [], []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION

    def launch(*args: object) -> dict:
        held.append(dict(state.HELD_INSTALL))
        monkeypatch.setattr(state, "VERSION", "0.0.1")
        return {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH}

    real = subprocess.run

    def newer(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] != str(newest):
            return real(argv, **kwargs)
        handed.append(argv[1:])
        monkeypatch.setattr(state, "VERSION", version)
        code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capsys.readouterr().out, "")

    monkeypatch.setattr(orca, "launch", launch)
    monkeypatch.setattr(cli.install, "upgraded", lambda: newest)
    monkeypatch.setattr(cli.subprocess, "run", newer)
    state.hold_install()
    assert run("job", "launch", "j1", "brief.md") == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")
    receipt = state.drive_dir(demo) / "jobs" / f"j1.{DISPATCH}.json"
    assert (held, handed, receipt.exists()) == ([{}], [["job", "started", "j1", str(receipt), "--drive", demo, "--json"]], False)
    job = state.load(demo)["jobs"]["j1"]
    assert (job["where"], job["dispatch"], job["run"], job["state"]) == ("remote", DISPATCH, RUN, "running")


@pytest.mark.parametrize(
    ("step", "named"),
    [
        ("Repair: drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- aws s3 ls", "break-glass"),
        ("PROVE: tools/ci mac-box prove --commit f3163cd03c48", "mac-box"),
        ("PROVE: tools/ci db-read \"select count(*) from iris_sessions\"", "db-read"),
        ("INSTALL: tools/ci release start --order 680721", "ci release"),
        ("Goal: read the 500s in Datadog APM", "Datadog"),
    ],
)
def test_launch_starts_a_brief_naming_a_step_only_this_mac_runs_only_on_this_mac(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], step: str, named: str
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text(f"Goal: ship it\nHandoff: merged\n{step}\nDrive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    assert capsys.readouterr().err == (
        f"drive: j1: refused, not launched: its brief names {named}, a step only this Mac runs: start it here, with the Agent tool at any load or with --local\n"
    )
    assert state.load(demo)["jobs"]["j1"].get("launching") is None
    assert run("job", "launch", "j1", "brief.md", "--local") == (0, f"j1 started on this Mac as dispatch {DISPATCH}")
    assert state.load(demo)["jobs"]["j1"]["route"] == state.LOCAL_ROUTE


def test_launch_starts_on_a_sprite_a_brief_whose_mac_only_words_sit_only_in_his_order_the_incident_rule_and_earlier_jobs(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text(
        "Order (his words): <https://x.slack.com/archives/C0822AHFY3G/p1794521905561409|alert> I thoguth we fixed OOMs? Shared from Datadog in"
        " <#C0822AHFY3G>: *Monitor alert summary:* Executor OOM Run Crash Count (16Gi)\n"
        "Serves: his order\n"
        "Goal: a retry of a run forks the newest attempt in its chain whose journal is in the team's bucket: change 1 of design j2810, one PR off dev\n"
        "Done when: READY: one PR off dev, green, carrying the design's tests\n"
        "Where: Forge-AI/monorepo, one new branch off dev; go/sand/next/internal/vm_run/fork.go\n"
        "Decisions that bind:\n- his answer \"read the OOMs in Datadog first\"\n"
        "Plan: Design j2810 (drive job report j2810, read it whole first)\n"
        "Incident: Every release a drive job starts starts the regular way, `tools/ci release start <target> --from <sha>`\n"
        "Transcripts:\n- j2333 builder: read the 500s in Datadog APM, then tools/ci mac-box prove\n"
        "Drive: demo, order o1, job j1\n"
    )
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the retries")
    run("job", "add", "builder", "--order", "o1", "--goal", "retry forks the newest saved attempt")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j1", "brief.md") == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")


def test_launch_local_from_a_session_that_did_not_start_the_jobs_agent_starts_its_lane_on_a_handoff_as_that_sessions(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "a verb for owners")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it", "--agent", "a-1")
    run("job", "brief", "j1", "--serves", "drive tooling", "--done-when", "merged and installed", "--where", "yasyf/cc-skills main")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "meta-session")
    run("start", "demo", "--role", "meta")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    launched: list[str] = []
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda drive, lane, brief, *rest: launched.append(brief.read_text()) or {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j1", "brief.md", "--local") == (0, f"j1 started on this Mac as dispatch {DISPATCH}")
    assert "Drive: demo, order o1, job j1\n\n## Taking over from an agent of another session\n\nThe agent that held this job, a-1 of session session-under-test," in launched[0]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["agent"], job["session"], job["where"], job["route"], job["dispatch"]) == (None, "meta-session", "remote", state.LOCAL_ROUTE, DISPATCH)


STOPPED_TEXT = "API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This sometimes happens with safe, normal conversations."
STOPPED = {
    "type": "assistant",
    "timestamp": "2026-11-12T20:44:05.907Z",
    "message": {"model": "<synthetic>", "stop_reason": "refusal", "content": [{"type": "text", "text": STOPPED_TEXT}]},
}
CALLED = {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "Bash"}]}}
CALL_RESULT = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}
ENDED_TURN = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Pushed the fix."}]}}


def agent_on_this_mac(run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, last: dict, quiet: timedelta) -> list[str]:
    """A builder running as agent a1 of this session on this Mac and waiting on its own PR, as j1860 did, whose transcript ends on last and has been quiet for quiet; the brief of each lane a launch starts for it."""
    cloned(tmp_path, demo)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the red check")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix the red check", "--agent", "a1")
    first = tmp_path / "brief.md"
    first.write_text("Order (his words): fix the red check\nDrive: demo, order o1, job j1\n")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(brief=str(first), waits=["Forge-AI/monorepo#54870"], state="waiting")
    data = state.load(demo)
    path = watch.transcript(watch.session_of(data["jobs"]["j1"], data), "a1")
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in (CALLED, CALL_RESULT, last)))
    stamp = (state.now() - quiet).timestamp()
    os.utime(path, (stamp, stamp))
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    launched: list[str] = []
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda drive, lane, brief, *rest: launched.append(brief.read_text()) or {field: f"{field}-2" for field in fields})
    return launched


@pytest.mark.parametrize(
    ("argv", "said", "message"),
    [
        (("launch", "j1"), "j1 started on Sprite workspace-2 as dispatch dispatch-2", None),
        (
            ("resume", "j1", "PR #54870: checks failed"),
            "j1: the message went to the job's next worker, started on Sprite workspace-2 as dispatch dispatch-2 in a handoff carrying it",
            "PR #54870: checks failed",
        ),
    ],
    ids=["launch", "resume"],
)
def test_a_job_whose_agent_on_this_mac_stopped_on_a_refusal_relaunches_on_a_sprite_with_a_handoff_naming_the_stop(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: tuple[str, ...], said: str, message: str | None
) -> None:
    launched = agent_on_this_mac(run, demo, tmp_path, monkeypatch, STOPPED, timedelta(minutes=10))
    stop = f'its last response ended on refusal at {watch.when(STOPPED["timestamp"])}: "{render.cut(STOPPED_TEXT, 100)}"'
    assert run("job", *argv) == (0, f"{said}, taking over from its agent a1 on this Mac, which takes no message: {stop}")
    brief = orca.brief_of(launched[0])
    assert brief.startswith(
        "Order (his words): fix the red check\nDrive: demo, order o1, job j1\n\n## Taking over from an agent that stopped\n\n"
        f"The agent that held this job, a1 of session session-under-test, stopped, so it takes no message: {stop}."
    )
    assert brief.endswith(f"The message it never read, which you act on now:\n\n{message}") if message else "which you act on now" not in brief
    job = state.load(demo)["jobs"]["j1"]
    assert {field: job[field] for field in ("agent", "session", "where", "route", "dispatch", "state", "waits")} == {
        "agent": None,
        "session": "session-under-test",
        "where": "remote",
        "route": None,
        "dispatch": "dispatch-2",
        "state": "running",
        "waits": ["Forge-AI/monorepo#54870"],
    }


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (("launch", "j1"), "j1 started on Sprite workspace-2 as dispatch dispatch-2"),
        (("resume", "j1", "PR #54870: checks failed"), "j1: the message went to the job's next worker, started on Sprite workspace-2 as dispatch dispatch-2 in a handoff carrying it"),
    ],
    ids=["launch", "resume"],
)
def test_a_job_whose_agent_ended_with_an_earlier_session_relaunches_from_a_detached_call_through_the_one_start(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: tuple[str, ...], said: str
) -> None:
    launched = agent_on_this_mac(run, demo, tmp_path, monkeypatch, CALLED, timedelta(seconds=30))
    with state.edit(demo) as data:
        data["jobs"]["j1"]["session"] = "earlier-session"
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert run("job", *argv) == (0, f"{said}, taking over from its agent a1 on this Mac, which takes no message: it ended with its session, which no role holds any more")
    assert "The agent that held this job, a1 of session earlier-session, stopped, so it takes no message: it ended with its session" in launched[0]
    job = state.load(demo)["jobs"]["j1"]
    assert (job["agent"], job["where"], job["dispatch"], job["state"]) == (None, "remote", "dispatch-2", "running")


@pytest.mark.parametrize(("last", "quiet"), [(STOPPED, timedelta(seconds=30)), (ENDED_TURN, timedelta(minutes=10)), (CALLED, timedelta(minutes=10))])
def test_a_job_whose_agent_on_this_mac_works_or_ended_its_own_turn_keeps_its_agent(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last: dict, quiet: timedelta
) -> None:
    launched = agent_on_this_mac(run, demo, tmp_path, monkeypatch, last, quiet)
    for argv in (("launch", "j1"), ("resume", "j1", "PR #54870: checks failed")):
        assert cli.main(["job", *argv]) == 2
        assert capsys.readouterr().err == "drive: j1 runs on this Mac as agent a1; resume it with SendMessage\n"
    assert (launched, state.load(demo)["jobs"]["j1"]["agent"]) == ([], "a1")


@pytest.mark.parametrize(
    ("receipts", "said"),
    [
        (("worker-start.status", "worker-start.unsent"), UNSENT),
        (
            ("worker-start.status",),
            f"orca-worker-launch.sh exited 1 after worker-start ran, so the lane may be live: {UNSENT}; read {{attempt}}/worker-start.json before starting it anywhere else",
        ),
    ],
)
def test_a_launch_says_the_lane_may_be_live_only_when_orca_may_have_recorded_its_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipts: tuple[str, ...], said: str
) -> None:
    brief = tmp_path / "j1.md"
    brief.write_text("brief")
    attempt = brief.with_suffix(".worker")
    attempt.mkdir()
    for receipt in receipts:
        (attempt / receipt).write_text("1\n")
    monkeypatch.setattr(orca, "tool", lambda name: name)
    done = subprocess.CompletedProcess([], 1, stdout="", stderr=f"{UNSENT}\n")
    monkeypatch.setattr(orca.subprocess, "run", lambda argv, **kwargs: done)
    with pytest.raises(orca.OrcaError) as raised:
        orca.launch("demo", "demo-j1", brief, tmp_path, RUN, None, None)
    assert str(raised.value) == said.format(attempt=attempt)


def test_resume_answers_the_open_question_then_sends_to_the_dispatch(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(orca, "call", lambda *args: calls.append(args) or {})
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a live lane is never relaunched"))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="msg_q1", state="waiting", undelivered=[{"seq": "#61", "words": "Approve", "at": state.stamp(), "said": None}])
    assert run("job", "resume", "j1", "TAP Approve")[0] == 0
    assert state.load(remote)["jobs"]["j1"]["undelivered"] == []
    assert run("job", "resume", "j1", "PR checks failed")[0] == 0
    assert calls == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_q1", "--run", RUN, "--body", "TAP Approve"),
        ("run-use", "--id", DRIVE_RUN),
        (
            "send",
            "--to",
            f"dispatch:{DISPATCH}",
            "--run",
            RUN,
            "--subject",
            "From the coordinator",
            "--body",
            "PR checks failed",
        ),
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"]) == (None, "running")


def test_resume_sends_to_the_dispatch_when_orca_already_holds_another_answer_to_the_open_question(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(orca, "call", answered_elsewhere(calls))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="msg_q1", state="waiting")
    assert run("job", "resume", "j1", "PR checks failed") == (0, f"j1: sent to dispatch {DISPATCH}")
    assert calls == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_q1", "--run", RUN, "--body", "PR checks failed"),
        ("run-use", "--id", DRIVE_RUN),
        ("send", "--to", f"dispatch:{DISPATCH}", "--run", RUN, "--subject", "From the coordinator", "--body", "PR checks failed"),
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"]) == (None, "running")


def test_resume_answers_an_open_question_naming_logs_with_those_logs_ahead_of_its_text(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, asked = [], []
    monkeypatch.setattr(orca, "call", lambda *args: calls.append(args) or {})
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: asked.append((question, root, drive, job_id)) or "FAIL hold_test.go:41")
    report = "j1 READ, two reads: (1) LOG https://buildkite.com/forge/test/builds/1 (2) the field facts"
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="msg_q1", state="waiting", report=report)
    code, out = run("job", "resume", "j1", "The field facts: none")
    assert code == 0
    assert "with LOG https://buildkite.com/forge/test/builds/1 read ahead of your text" in out
    assert asked == [("LOG https://buildkite.com/forge/test/builds/1", tmp_path, remote, "j1")]
    assert ("reply", "--id", "msg_q1", "--run", RUN, "--body", "FAIL hold_test.go:41\n\nThe field facts: none") in calls


def dead(verdict: str = "exited") -> dict:
    return {"worker": {"state": "ready"}, "projection": {"liveness": {"verdict": verdict}, "outcome": "in_progress", "attention": {"categories": []}}}


def test_resume_to_a_dead_worker_relaunches_the_job_with_the_message_in_its_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(
            brief=str(first),
            workspace="sprite-old",
            ask="msg_q1",
            state="waiting",
            said="ASK which npm name?",
            prs=["https://github.com/Forge-AI/monorepo/pull/7"],
        )
    sent, launched, read, objectives = [], {}, [], []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    monkeypatch.setattr(orca, "new_run", lambda objective: objectives.append(objective) or FRESH_RUN)
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: read.append(job["workspace"]))

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), run=run_id, workspace=workspace)
        fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
        return {field: f"{field}-2" for field in fields}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "resume", "j1", "Use @poeticapps/poetic")
    assert (code, sent, read) == (0, [], ["sprite-old"])
    assert out == (
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite sprite-old was dead (its"
        " worker exited), and work not pushed from sprite-old is not carried over"
    )
    assert (launched["lane"], launched["run"], launched["workspace"], objectives) == ("demo-j1-2", FRESH_RUN, None, ["drive demo j1-2: remote builders"])
    brief = orca.brief_of(launched["brief"])
    assert brief.startswith("Order (his words): ship it\nDrive: demo, order o1, job j1\n\n## Taking over from a lane that died")
    assert "Commit and push before any long build." in brief
    assert "Its last line: ASK which npm name?\nIts PRs: https://github.com/Forge-AI/monorepo/pull/7\n" in brief
    assert brief.endswith("which you act on now:\n\nUse @poeticapps/poetic")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["run"], job["dispatch"], job["workspace"], job["state"], job["ask"], job["brief"]) == (
        FRESH_RUN,
        "dispatch-2",
        "workspace-2",
        "running",
        None,
        str(first),
    )
    assert [(death["dispatch"], death["workspace"], death["why"]) for death in job["deaths"]] == [
        (DISPATCH, "sprite-old", "its worker exited")
    ]


def test_a_resume_retried_after_its_bind_refused_relaunches_the_same_attempt_with_the_new_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", state="waiting", said="WAITING release")
    sent, launched, runs = [], [], iter(["run_try1", "run_try2"])
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    monkeypatch.setattr(orca, "new_run", lambda objective: next(runs))
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)

    def bind_refused(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.append((brief, brief.read_text(), run_id))
        raise orca.Unstarted(f"no worker started on Sprite pool-two: WORKER_BIND refused before worker-start ran: {NO_RUN}")

    monkeypatch.setattr(orca, "launch", bind_refused)
    assert cli.main(["job", "resume", "j1", "PR #12 merged: prove it live"]) == 2
    assert NO_RUN in capsys.readouterr().err

    def started(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.append((brief, brief.read_text(), run_id))
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", started)
    assert run("job", "resume", "j1", "PR #12 merged at abc123: prove it live") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite sprite-old was dead (its worker"
        " exited), and work not pushed from sprite-old is not carried over",
    )
    (prepared, kept, first_run), (relaunched, again, second_run) = launched
    assert (relaunched, first_run, second_run) == (prepared, "run_try1", "run_try2")
    assert orca.brief_of(kept).endswith("which you act on now:\n\nPR #12 merged: prove it live")
    assert orca.brief_of(again).endswith("which you act on now:\n\nPR #12 merged at abc123: prove it live")
    assert sent == []
    job = state.load(remote)["jobs"]["j1"]
    assert (job["run"], job["dispatch"], [death["dispatch"] for death in job["deaths"]]) == ("run_try2", "dispatch-2", [DISPATCH])


SAVED = (
    "uncommitted changes in /home/sprite/monorepo/.worktrees/conflict-tallies (8 files); commits on no remote on tallies-final, stash,"
    " now on Forge-AI/monorepo as saved/demo-j1/conflict-tallies at 1a2b3c4d5e, saved/demo-j1/stash at 2b3c4d5e6f,"
    " saved/demo-j1/tallies-final at 3c4d5e6f70"
)
UNREAD = "orca-reclaim.py exited 1: ssh to pool-sleeping could not read /home/sprite/monorepo: i/o timeout"


def unsaveable(why: str) -> Callable[..., str]:
    def save(drive: str, job_id: str, job: dict, root: Path) -> str:
        raise orca.OrcaError(why)

    return save


def dead_lane_holding(remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save: Callable[..., str | None]) -> dict:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="pool-sleeping", state="running", said="building the tallies")
    monkeypatch.setattr(orca, "worker", refused("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a lane whose Sprite is gone: {args}"))
    monkeypatch.setattr(orca, "save", save)
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")} | {"workspace": workspace or "workspace-2"}

    monkeypatch.setattr(orca, "launch", fake_launch)
    return launched


def test_resume_saves_what_a_dead_lanes_sprite_holds_to_github_and_hands_the_saved_branches_to_the_next_lane(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    saves = []
    launched = dead_lane_holding(remote, tmp_path, monkeypatch, lambda drive, job_id, job, root: saves.append((drive, job_id, job["workspace"])) or SAVED)
    assert run("job", "resume", "j1", "Build #4412 failed: fix the lint") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-sleeping was"
        f" dead (Orca no longer holds its Sprite's environment), and what pool-sleeping held on no remote is saved: {SAVED}",
    )
    assert saves == [(remote, "j1", "pool-sleeping")]
    assert (
        f"You take the job over on a fresh Sprite from its pushed branches; what it held on no remote was saved for you: {SAVED}."
        " Fetch each saved branch and carry its work on. Commit and push before any long build."
    ) in orca.brief_of(launched["brief"])
    job = state.load(remote)["jobs"]["j1"]
    assert [(death["dispatch"], death["saved"]) for death in job["deaths"]] == [(DISPATCH, SAVED)]


@pytest.mark.parametrize(
    "why",
    [UNREAD, "orca-reclaim.py exited 1: the save on pool-sleeping failed: remote: Permission to Forge-AI/monorepo.git denied"],
)
def test_resume_continues_a_dead_lane_on_its_own_sprite_when_what_that_sprite_holds_cannot_be_read_or_saved(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    cloned(tmp_path, remote)
    launched = dead_lane_holding(remote, tmp_path, monkeypatch, unsaveable(why))
    assert run("job", "resume", "j1", "Build #4412 failed: fix the lint") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite pool-sleeping as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-sleeping was"
        " dead (Orca no longer holds its Sprite's environment), and it continues in the checkout the dead lane left on pool-sleeping,"
        f" since what that Sprite holds on no remote could not be saved: {why}",
    )
    assert (launched["lane"], launched["workspace"]) == ("demo-j1-2", "pool-sleeping")
    brief = orca.brief_of(launched["brief"])
    assert (
        "You continue the job on the same Sprite, in the checkout it left as it was, since what it holds on no remote could not be"
        f" saved to GitHub: {why}. Read its git status and log before anything else." in brief
    )
    assert orca.UNPUSHED not in brief
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], [(death["dispatch"], death["workspace"]) for death in job["deaths"]]) == (
        "dispatch-2",
        "pool-sleeping",
        [(DISPATCH, "pool-sleeping")],
    )


def test_resume_to_a_worker_that_finished_starts_the_next_one_in_its_terminal_with_the_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    lane = {"terminal": "term_lane", "workspace": "pool-one", "environment": "pool-one", "lane": "demo-j1"}
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(
            lane,
            brief=str(first),
            said="WAITING security review of #7",
            report="WAITING security review of #7\nPR: https://github.com/Forge-AI/monorepo/pull/7",
            prs=["https://github.com/Forge-AI/monorepo/pull/7"],
            reads={"relay_old": "READ ccn answer search x"},
        )
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a finished worker on a Sprite still up is never relaunched elsewhere"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    started = {}
    steps = []
    monkeypatch.setattr(orca, "place_tools", lambda workspace: steps.append(f"tools on {workspace}"))

    def place_brief(workspace: str, lane: str, text: bytes) -> str:
        steps.append(f"brief on {workspace}")
        started.update(spec=text.decode(), sha=hashlib.sha256(text).hexdigest(), bytes=len(text))
        return f"/home/sprite/.local/state/long-running/briefs/{lane}.md"

    monkeypatch.setattr(orca, "place_brief", place_brief)

    def worker_start(drive: str, run_id: str, argv: list[str]) -> subprocess.CompletedProcess:
        steps.append("worker-start")
        started.update(run=run_id, argv=argv)
        receipt = {"ok": True, "result": {"state": "ready", "runId": run_id, "taskId": "task_2", "dispatchId": "ctx_lane0002"}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt), stderr="")

    monkeypatch.setattr(orca, "bound_command", worker_start)
    code, out = run("job", "resume", "j1", "The review: one medium finding, fix it")
    assert steps == ["tools on pool-one", "brief on pool-one", "worker-start"]
    assert (code, out) == (
        0,
        "j1: the message went to the job's next worker, started in its terminal on the same Sprite as dispatch ctx_lane0002 in"
        " a handoff carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one had"
        " finished (its worker succeeded, and Orca sends nothing to a finished worker on a Sprite)",
    )
    argv = started["argv"]
    options = {argv[index]: argv[index + 1] for index in range(3, len(argv) - 2, 2)}
    assert (started["run"], argv[:3], argv[-2:]) == (RUN, ["orca", "orchestration", "worker-start"], ["--full", "--json"])
    assert options == {
        "--run": RUN,
        "--on": "pool-one",
        "--worktree": "id:repo-1::/home/sprite/monorepo",
        "--terminal": "term_lane",
        "--task-title": "demo-j1-2",
        "--spec": (
            "Lane demo-j1-2: read the whole brief at /home/sprite/.local/state/long-running/briefs/demo-j1-2.md, verify its"
            f" SHA-256 is {started['sha']} and its size is {started['bytes']} bytes, then follow it exactly."
        ),
        "--timeout-ms": "600000",
    }
    assert started["spec"].startswith("Remote lane: drive demo, job j1.")
    brief = orca.brief_of(started["spec"])
    assert brief.startswith("Order (his words): prove it\nDrive: demo, order o1, job j1\n\n## Taking over from a lane that finished")
    assert "You continue the job in its terminal and checkout on the same Sprite, as it left them." in brief
    assert "Its branch: ci-mac-box-prove\nIts last line: WAITING security review of #7\n" in brief
    assert "Its last report:\n\nWAITING security review of #7\nPR: https://github.com/Forge-AI/monorepo/pull/7\n" in brief
    assert brief.endswith("which you act on now:\n\nThe review: one medium finding, fix it")
    job = state.load(remote)["jobs"]["j1"]
    assert {field: job[field] for field in ("dispatch", "task", "terminal", "workspace", "lane", "state", "report", "reads")} == {
        "dispatch": "ctx_lane0002",
        "task": "task_2",
        "terminal": "term_lane",
        "workspace": "pool-one",
        "lane": "demo-j1-2",
        "state": "running",
        "report": None,
        "reads": {},
    }
    assert [(ended["dispatch"], ended["workspace"]) for ended in job["deaths"]] == [(DISPATCH, "pool-one")]


def test_resume_to_a_finished_worker_whose_terminal_orca_no_longer_finds_relaunches_the_job_as_a_dead_lanes(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one")
    reply = {"ok": False, "error": {"code": "selector_not_found", "message": "No terminal matches term_lane."}}
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    monkeypatch.setattr(orca, "bound_command", lambda drive, run_id, argv: subprocess.CompletedProcess(argv, 1, stdout=json.dumps(reply), stderr=""))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "place_tools", lambda workspace: None)
    monkeypatch.setattr(orca, "place_brief", lambda workspace, lane, text: f"/home/sprite/{lane}.md")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), run=run_id, workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "resume", "j1", "The review: one medium finding, fix it") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one was dead (Orca no"
        " longer holds its worker's terminal or worktree), and work not pushed from pool-one is not carried over",
    )
    assert (launched["lane"], launched["run"], launched["workspace"]) == ("demo-j1-2", FRESH_RUN, None)
    brief = orca.brief_of(launched["brief"])
    assert "## Taking over from a lane that died" in brief
    assert brief.endswith("which you act on now:\n\nThe review: one medium finding, fix it")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], [death["dispatch"] for death in job["deaths"]]) == ("dispatch-2", "workspace-2", [DISPATCH])


def test_a_finished_worker_whose_terminal_is_no_longer_connected_relaunches_the_job_whatever_its_liveness_reads(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one", state="waiting")
    shown = {
        **FINISHED_WORKER,
        "projection": {"liveness": {"verdict": "unverifiable", "reason": "missing_status"}, "outcome": "succeeded", "attention": {"categories": []}},
        "terminal": {**FINISHED_WORKER["terminal"], "connected": False},
    }
    monkeypatch.setattr(orca, "worker", lambda dispatch: shown)
    monkeypatch.setattr(orca, "bound_command", lambda *args: pytest.fail("no worker starts in a terminal that is no longer connected"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, run=run_id, workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "resume", "j1", "TAP Approve") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one was dead (its worker"
        " succeeded, and its terminal term_lane is no longer connected), and work not pushed from pool-one is not carried over",
    )
    assert (launched["lane"], launched["run"], launched["workspace"]) == ("demo-j1-2", FRESH_RUN, None)
    assert state.load(remote)["jobs"]["j1"]["dispatch"] == "dispatch-2"


REFUSED = "install: error writing '/home/sprite/.local/bin/ccx': No space left on device"
UNFIT = f"demo-j1-2.worker.1 keeps the earlier attempt, whose Sprite pool-two refused the tools a worker needs: {REFUSED}, and its Sprite pool-two is destroyed; launching this brief again prepares a fresh Sprite"
UNFIT_KEPT = (
    f"demo-j1-2.worker.1 keeps the earlier attempt, whose Sprite sprite-old refused the tools a worker needs: {REFUSED}, and its Sprite"
    " sprite-old is kept, since this launch continues the lane on it; launching this brief again prepares a fresh Sprite once WORKER_WORKSPACE is unset"
)


def answered_dead_lane(remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save: Callable[..., str | None], refusals: int) -> list[tuple[str, str, str | None]]:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", state="waiting")
        data["answers"]["a1"] = {"words": "Approve", "link": "https://acme.slack.com/archives/D123/p1794300000000300", "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {"j1": None}}
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a dead lane: {args}"))
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", save)
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = []

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.append((lane, run_id, workspace))
        if len(launched) <= refusals:
            raise orca.Unfit(UNFIT_KEPT if workspace else UNFIT)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    return launched


def test_a_resume_whose_fresh_sprite_refuses_the_tools_launches_once_more_on_another_and_marks_his_answer_sent_once_it_started(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched = answered_dead_lane(remote, tmp_path, monkeypatch, lambda drive, job_id, job, root: None, 1)
    assert run("job", "resume", "j1", "His answer, verbatim: Approve") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite sprite-old was dead (its"
        " worker exited), and work not pushed from sprite-old is not carried over",
    )
    assert launched == [("demo-j1-2", FRESH_RUN, None), ("demo-j1-2", FRESH_RUN, None)]
    assert state.load(remote)["answers"]["a1"]["jobs"] == {"j1": {"mark": "sent"}}


def test_a_resume_whose_fresh_sprites_both_refuse_the_tools_exits_2_and_leaves_his_answer_unsent(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launched = answered_dead_lane(remote, tmp_path, monkeypatch, lambda drive, job_id, job, root: None, 2)
    assert cli.main(["job", "resume", "j1", "His answer, verbatim: Approve"]) == 2
    assert capsys.readouterr().err == f"drive: {UNFIT}\n"
    assert launched == [("demo-j1-2", FRESH_RUN, None), ("demo-j1-2", FRESH_RUN, None)]
    data = state.load(remote)
    assert (data["jobs"]["j1"]["dispatch"], data["answers"]["a1"]["jobs"]) == (DISPATCH, {"j1": None})


def test_a_resume_continuing_on_a_dead_lanes_sprite_that_refuses_the_tools_keeps_it_and_relaunches_on_a_fresh_one(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched = answered_dead_lane(remote, tmp_path, monkeypatch, unsaveable(UNREAD), 1)
    assert run("job", "resume", "j1", "His answer, verbatim: Approve") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite sprite-old was dead (its"
        f" worker exited, and {UNFIT_KEPT}), and sprite-old is kept, since what it holds on no remote could not be saved: {UNREAD}",
    )
    assert launched == [("demo-j1-2", FRESH_RUN, "sprite-old"), ("demo-j1-2", FRESH_RUN, None)]
    assert state.load(remote)["answers"]["a1"]["jobs"] == {"j1": {"mark": "sent"}}


def test_a_message_to_a_live_sprite_lane_no_recorded_start_put_the_tools_on_places_them_first_and_only_once(
    run: Run, remote: str, tooled: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="pool-1895", environment="pool-1895")
    sent = []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    assert run("job", "resume", "j1", "DRIVE status works again") == (0, f"j1: sent to dispatch {DISPATCH}")
    assert run("job", "resume", "j1", "PR #7 merged") == (0, f"j1: sent to dispatch {DISPATCH}")
    assert (tooled, len(sent)) == (["pool-1895"], 2)
    assert state.load(remote)["jobs"]["j1"]["tools_on"] == "pool-1895"


def test_a_live_sprite_lane_whose_sprite_refuses_the_tools_still_gets_its_message_and_the_next_one_places_them_again(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="pool-1895", environment="pool-1895")
    refusal = f"orca-sprite-tools.sh exited 76 on pool-1895: {REFUSED}"
    tried = []

    def refused(workspace: str) -> None:
        tried.append(workspace)
        raise orca.Unfit(refusal)

    monkeypatch.setattr(orca, "place_tools", refused)
    monkeypatch.setattr(orca, "call", lambda *args: {})
    assert run("job", "resume", "j1", "PR #7 merged") == (0, f"j1: sent to dispatch {DISPATCH}; its Sprite still lacks the drive forwarder: {refusal}")
    assert run("job", "resume", "j1", "PR #8 merged")[0] == 0
    assert tried == ["pool-1895", "pool-1895"]
    assert "tools_on" not in state.load(remote)["jobs"]["j1"]


NO_SPACE = "orca-worker-tools exited 1 on pool-one, so no worker started: write /home/sprite/.local/bin/ccx: no space left on device"


@pytest.mark.parametrize(
    ("save", "kept"),
    [
        (lambda drive, job_id, job, root: None, "work not pushed from pool-one is not carried over"),
        (lambda drive, job_id, job, root: SAVED, f"what pool-one held on no remote is saved: {SAVED}"),
        (unsaveable(UNREAD), f"pool-one is kept, since what it holds on no remote could not be saved: {UNREAD}"),
    ],
    ids=["nothing to save", "saved", "save failed"],
)
def test_a_finished_worker_whose_sprite_refuses_its_tools_starts_the_next_on_a_fresh_sprite(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save: Callable[..., str | None], kept: str
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one")
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)

    def unfit(workspace: str) -> None:
        raise orca.Unfit(NO_SPACE)

    monkeypatch.setattr(orca, "place_tools", unfit)
    monkeypatch.setattr(orca, "bound_command", lambda *args: pytest.fail("no worker starts on a Sprite that refused its tools"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "save", save)
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "launch", "j1") == (
        0,
        "j1: the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff carrying its brief, its"
        " branch, its last line, its report and its PRs; its lane on Sprite pool-one was dead (its worker succeeded, and Orca sends"
        f" nothing to a finished worker on a Sprite, and {NO_SPACE}), and {kept}",
    )
    assert (launched["lane"], launched["workspace"]) == ("demo-j1-2", None)
    brief = orca.brief_of(launched["brief"])
    assert "## Taking over from a lane that died" in brief
    assert "which you act on now" not in brief
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], [(death["dispatch"], death["workspace"]) for death in job["deaths"]]) == ("dispatch-2", "workspace-2", [(DISPATCH, "pool-one")])


def test_a_launch_of_a_lane_whose_worker_finished_starts_its_next_in_its_terminal_with_no_message_and_one_that_works_is_refused(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one", lane="demo-j1")
    assert cli.main(["job", "launch", "j1"]) == 2
    assert capsys.readouterr().err == f"drive: j1 already started as dispatch {DISPATCH}, whose worker takes messages: drive job resume j1 <the event> sends it one\n"
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    monkeypatch.setattr(orca, "place_tools", lambda workspace: None)
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    started = {}
    monkeypatch.setattr(orca, "place_brief", lambda workspace, lane, text: started.update(spec=text.decode()) or f"/home/sprite/{lane}.md")

    def worker_start(drive: str, run_id: str, argv: list[str]) -> subprocess.CompletedProcess:
        receipt = {"ok": True, "result": {"state": "ready", "runId": run_id, "taskId": "task_2", "dispatchId": "ctx_lane0002"}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt), stderr="")

    monkeypatch.setattr(orca, "bound_command", worker_start)
    assert run("job", "launch", "j1") == (
        0,
        "j1: the job's next worker, started in its terminal on the same Sprite as dispatch ctx_lane0002 in a handoff carrying its"
        " brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one had finished (its worker succeeded,"
        " and Orca sends nothing to a finished worker on a Sprite)",
    )
    brief = orca.brief_of(started["spec"])
    assert "## Taking over from a lane that finished" in brief
    assert "which you act on now" not in brief
    assert state.load(remote)["jobs"]["j1"]["dispatch"] == "ctx_lane0002"


def test_a_resume_arriving_while_another_starts_the_jobs_next_worker_waits_for_it_and_sends_that_worker_its_text(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", state="waiting", said="WAITING CI")
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead() if dispatch == DISPATCH else LIVE_WORKER)
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    sent, launches = [], []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    launching, waiting, release = threading.Event(), threading.Event(), threading.Event()
    locked, entered = state.job_locked, itertools.count(1)

    def job_locked(name: str, job_id: str, seconds: float):
        if next(entered) == 2:
            waiting.set()
        return locked(name, job_id, seconds)

    def slow_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launches.append(brief.read_text())
        launching.set()
        assert release.wait(10)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(state, "job_locked", job_locked)
    monkeypatch.setattr(orca, "launch", slow_launch)
    codes = {}
    resumes = [threading.Thread(target=lambda text=text: codes.setdefault(text, cli.main(["job", "resume", "j1", text, "--drive", remote]))) for text in ("PR #12: checks failed", "TAP Approve #12")]
    resumes[0].start()
    assert launching.wait(10)
    resumes[1].start()
    assert waiting.wait(10)
    release.set()
    for resume in resumes:
        resume.join(10)
    assert codes == {"PR #12: checks failed": 0, "TAP Approve #12": 0}
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2")
    assert out[1] == "j1: sent to dispatch dispatch-2"
    assert len(launches) == 1 and orca.brief_of(launches[0]).endswith("which you act on now:\n\nPR #12: checks failed")
    assert sent == [("send", "--to", "dispatch:dispatch-2", "--run", FRESH_RUN, "--subject", "From the coordinator", "--body", "TAP Approve #12")]


@pytest.mark.parametrize(
    ("shown", "refusal"),
    [
        (
            None,
            "orca orchestration send: dispatch_inactive: Dispatch ctx_lane0001 finished on pool-one, which cannot take "
            "follow-ups for finished workers yet.",
        ),
        (
            FINISHED_WORKER,
            "orca orchestration worker-start: terminal_busy: The terminal is running another worker.",
        ),
    ],
)
def test_a_resume_orca_refuses_exits_2_with_the_refusal_verbatim(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], shown: dict | None, refusal: str
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one")
    verb, code, message = refusal.split(": ", 2)
    reply = {"ok": False, "error": {"code": code, "message": message}}
    if shown:
        monkeypatch.setattr(orca, "worker", lambda dispatch: shown)
    done = subprocess.CompletedProcess([], 1, stdout=json.dumps(reply), stderr="")
    monkeypatch.setattr(orca.subprocess, "run", lambda argv, **kwargs: done)
    monkeypatch.setattr(orca, "bound_command", lambda drive, run_id, argv: done)
    monkeypatch.setattr(orca, "place_tools", lambda workspace: None)
    monkeypatch.setattr(orca, "place_brief", lambda workspace, lane, text: f"/home/sprite/{lane}.md")
    assert cli.main(["job", "resume", "j1", "the review"]) == 2
    assert capsys.readouterr().err == f"drive: {refusal}\n"
    assert state.load(remote)["jobs"]["j1"]["dispatch"] == DISPATCH


@pytest.mark.parametrize("unknown", ["remote_runtime_unavailable", "unverifiable"])
def test_resume_sends_nothing_while_orca_cannot_tell_whether_the_lane_lives(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch, unknown: str
) -> None:
    sent = []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})

    def show(dispatch: str) -> dict:
        if unknown == "unverifiable":
            return dead(unknown)
        raise orca.OrcaError(f"orca orchestration worker-show: {unknown}: the host did not answer")

    monkeypatch.setattr(orca, "worker", show)
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a lane Orca cannot see is never relaunched"))
    assert (run("job", "resume", "j1", "PR checks failed")[0], sent) == (2, [])
    assert "deaths" not in state.load(remote)["jobs"]["j1"]


def test_the_watch_ends_a_waiting_jobs_dead_lane_on_a_sprite_once(remote: str, monkeypatch: pytest.MonkeyPatch, ended_lanes: list[str]) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="sprite-old", state="waiting", said="ASK which npm name?", ask="msg_q1")
    shown = []
    monkeypatch.setattr(orca, "worker", lambda dispatch: shown.append(dispatch) or dead())
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on Sprite sprite-old: its worker exited, while it waited on "ASK which npm name?". The watch ran drive job end j1, '
        "which saves to GitHub what its Sprite holds on no remote and destroys it, its output in the drive's relaunches.log; "
        'drive job resume j1 "<his answer>" relaunches it on a fresh Sprite with a handoff'
    ]
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert (shown, ended_lanes) == ([DISPATCH], ["j1"])
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], [(death["dispatch"], death["ended"]) for death in job["deaths"]]) == ("waiting", [(DISPATCH, {"pid": os.getpid(), "at": state.stamp(T0)})])


def test_the_watch_releases_the_sprite_a_lane_died_on_once_no_unfinished_job_runs_on_it(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    released = []
    monkeypatch.setattr(state, "scratch", lambda: False)
    monkeypatch.setattr(orca, "checkout", lambda name, data: Path(remote))
    monkeypatch.setattr(orca, "holds_sprite", lambda job: job["workspace"] != "sprite-gone")
    monkeypatch.setattr(orca, "release_detached", lambda workspace, checkout, log: released.append(workspace) or subprocess.Popen(["true"]))
    with state.edit(remote) as data:
        old = [{"dispatch": f"ctx_old{n}", "workspace": workspace, "why": "its worker exited", "at": state.stamp(T0)} for n, workspace in enumerate(["sprite-old", "sprite-gone", "sprite-now"])]
        data["jobs"]["j1"].update(state="waiting", workspace="sprite-now", deaths=old)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    watch.release_lanes(watching, T0)
    watch.release_lanes(watching, T0)
    assert released == ["sprite-old"]


def refused(code: str, message: str) -> Callable[[str], dict]:
    def show(dispatch: str) -> dict:
        reply = {"ok": False, "error": {"code": code, "message": message}}
        return orca.result_of("orchestration worker-show", subprocess.CompletedProcess(["orca"], 1, json.dumps(reply), ""))

    return show


@pytest.mark.parametrize(
    ("code", "message", "why"),
    [
        ("dispatch_not_found", "no such dispatch", "Orca no longer holds its worker"),
        ("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4", "Orca no longer holds its Sprite's environment"),
    ],
)
def test_the_watch_says_a_lane_orca_dropped_died_once(remote: str, monkeypatch: pytest.MonkeyPatch, code: str, message: str, why: str) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"]["workspace"] = "sprite-old"
    monkeypatch.setattr(orca, "worker", refused(code, message))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0)[0].startswith(f"LANE j1 o1 died on Sprite sprite-old: {why}, while it worked on")
    assert not any(line.startswith("LANE") for line in watch.run_pass(watching, T0 + timedelta(minutes=1)))
    assert [(death["dispatch"], death["why"]) for death in state.load(remote)["jobs"]["j1"]["deaths"]] == [(DISPATCH, why)]


def test_a_running_job_whose_lane_died_is_relaunched_that_pass_once(remote: str, monkeypatch: pytest.MonkeyPatch, relaunches: list[str]) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="sprite-old", said="PUSHED https://github.com/Forge-AI/monorepo/pull/9")
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on Sprite sprite-old: its worker exited, while it worked on "PUSHED https://github.com/Forge-AI/monorepo/pull/9". '
        "The watch ran drive job resume j1, which relaunches it on a fresh Sprite with a handoff, its output in the drive's relaunches.log"
    ]
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert relaunches == ["j1"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["deaths"][-1]["relaunch"]) == ("running", {"pid": os.getpid(), "at": state.stamp(T0)})
    assert "lane dead since" in render.job_line("j1", job)


def test_a_relaunch_that_ended_without_a_next_lane_is_said_at_once(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    monkeypatch.setattr(orca, "worker", lambda dispatch: pytest.fail("a lane already said to have ended is not read again"))
    with state.edit(remote) as data:
        relaunch = {"pid": ENDED_READER, "at": state.stamp(T0 - timedelta(minutes=2))}
        death = {"dispatch": DISPATCH, "workspace": "sprite-old", "why": "its worker exited", "at": state.stamp(T0 - timedelta(minutes=2)), "relaunch": relaunch}
        data["jobs"]["j1"].update(deaths=[death], updated=state.stamp(T0 - timedelta(minutes=2)))
    [line] = watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    assert line.startswith('ORPHAN o1 "ship the remote builder": j1\'s lane ended 2 minutes ago (its worker exited) and the watch\'s drive job resume at ')
    assert line.endswith("started no next lane, its output in the drive's relaunches.log")


def branches(monkeypatch: pytest.MonkeyPatch, prs: dict[str, dict]) -> list[list[tuple[str, str]]]:
    asked = []
    monkeypatch.setattr(github, "checkout_repo", lambda checkout: "Forge-AI/monorepo")
    monkeypatch.setattr(github, "open_on", lambda heads: asked.append(heads) or prs)
    monkeypatch.setattr(github, "fetch", lambda keys: ({}, None))
    return asked


def test_a_finished_lane_whose_job_still_runs_is_said_every_thirty_minutes_until_its_result_is_recorded(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch)
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    asked = branches(monkeypatch, {"Forge-AI/monorepo#53447": {"branch": "ci-mac-box-prove", "checks": "pass", "review": "approved"}})
    with state.edit(remote) as data:
        data["jobs"]["j1"]["updated"] = state.stamp(T0 - timedelta(minutes=10))
    said = (
        'ORPHAN o1 "ship the remote builder": j1\'s lane succeeded and its result is not recorded; Forge-AI/monorepo#53447 '
        "on its branch ci-mac-box-prove is not in the drive: checks pass, review approved"
    )
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [said]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [said]
    assert asked[0] == [("Forge-AI/monorepo", "ci-mac-box-prove")]
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", said="WAITING CI", due=state.stamp(T0 + timedelta(hours=2)), updated=state.stamp(T0))
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == []
    assert state.load(remote)["orders"]["o1"]["parts"]["ship the remote builder"]["orphan_said"] is None


def test_a_lane_whose_report_lands_between_the_mail_read_and_the_worker_read_is_not_an_orphan(run: Run, remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/53935", "--job", "j1")[0] == 0
    deliver(monkeypatch, [], [message("worker_done", "WAITING CI on #53935\nPR: https://github.com/Forge-AI/monorepo/pull/53935")])
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER | {"dispatch": {"completedAt": state.stamp(T0 + timedelta(seconds=20))}})
    branches(monkeypatch, {})
    with state.edit(remote) as data:
        data["jobs"]["j1"]["updated"] = state.stamp(T0 - timedelta(minutes=10))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ["JOB j1 o1 WAITING CI on #53935"]
    data = state.load(remote)
    assert (data["jobs"]["j1"]["state"], data["orders"]["o1"]["parts"]["ship the remote builder"]["orphan_said"]) == ("waiting", None)
    assert data["jobs"]["j1"]["waits"] == ["Forge-AI/monorepo#53935"]


def test_a_lanes_waiting_naming_nothing_the_watch_frees_it_on_is_refused_and_sets_nothing(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = "WAITING CI and the reviewer bot on the reworked chat-open tags PR"
    deliver(monkeypatch, [message("worker_done", sent)])
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER | {"dispatch": {"completedAt": state.stamp(T0)}})
    branches(monkeypatch, {})
    [line] = watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    assert line.startswith(f"JOB j1 o1 {sent}; refused, its state unset: its WAITING names nothing the watch frees it on")
    assert line.endswith('drive job resume j1 "<the refusal>" sends it back')
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["waits"]) == ("running", [])


def test_a_running_job_with_an_open_pr_the_drive_does_not_track_is_said_until_it_is_added(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch)
    branches(monkeypatch, {"Forge-AI/monorepo#9": {"branch": "lane-fix", "checks": "pending", "review": "none"}})
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(branch="lane-fix", updated=state.stamp(T0 - timedelta(minutes=10)))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        'ORPHAN o1 "ship the remote builder": j1 is running; Forge-AI/monorepo#9 on its branch lane-fix is not in the drive: checks pending, review none'
    ]
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/9", "--job", "j1")[0] == 0
    with state.edit(remote) as data:
        data["jobs"]["j1"]["updated"] = state.stamp(T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []


@pytest.mark.parametrize(
    ("ago", "said"),
    [
        (timedelta(minutes=10), []),
        (
            timedelta(minutes=31),
            ['ORPHAN o1 "ship the remote builder": j1\'s lane ended 31 minutes ago (its worker exited), was not resumed and its result is not recorded'],
        ),
    ],
)
def test_a_running_job_whose_lane_ended_is_said_once_a_relaunch_would_have_finished(
    remote: str, monkeypatch: pytest.MonkeyPatch, ago: timedelta, said: list[str]
) -> None:
    deliver(monkeypatch)
    monkeypatch.setattr(orca, "worker", lambda dispatch: pytest.fail("a lane already said to have ended is not read again"))
    with state.edit(remote) as data:
        death = {"dispatch": DISPATCH, "workspace": "sprite-old", "why": "its worker exited", "at": state.stamp(T0 - ago)}
        data["jobs"]["j1"].update(deaths=[death], updated=state.stamp(T0 - ago))
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == said


def test_a_lane_result_opens_with_the_subject_it_was_sent_under(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "Built and opened PR #53447: the serve marks queued card answers.\nLeft: build #122630 failed on the card test"
    deliver(monkeypatch, [message("worker_done", body, subject="BLOCKED CI on PR #53447 (build #122630)")])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 BLOCKED CI on PR #53447 (build #122630)"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["report"], job["state"], job["said"]) == (body, "failed", "BLOCKED CI on PR #53447 (build #122630)")


def test_resume_keeps_a_question_the_lane_asked_while_it_was_answered(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reply_then_ask_again(*args: str) -> dict:
        with state.edit(remote) as data:
            data["jobs"]["j1"].update(ask="msg_q2", state="waiting")
        return {}

    monkeypatch.setattr(orca, "call", reply_then_ask_again)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="msg_q1", state="waiting")
    run("job", "resume", "j1", "TAP Approve")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"]) == ("msg_q2", "waiting")


def test_resume_and_end_refuse_a_lane_that_never_started(run: Run, demo: str, tmp_path: Path) -> None:
    cloned(tmp_path, demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    assert run("job", "resume", "j1", "go")[0] == 2
    assert run("job", "end", "j1")[0] == 2


def carrying(run: Run, remote: str) -> list[str]:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "show the tallies")
    with state.edit(remote) as data:
        parts = list(data["orders"]["o2"]["parts"])
        state.carry(data, "j1", "o2", parts)
    return parts


def test_end_frees_a_waiting_lane_and_the_job_keeps_waiting_with_its_waits_prs_and_carried_parts(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    parts = carrying(run, remote)
    run("job", "add", "builder", "--order", "o1", "--goal", "restack onto the fix", "--remote")
    url = "https://github.com/Forge-AI/monorepo/pull/53430"
    key = state.pr_key(url)
    with state.edit(remote) as data:
        data["prs"][key] = state.new_pr(url, "j1", "o1")
        data["jobs"]["j1"].update(state="waiting", said=f"WAITING CI on {url}", prs=[url], waits=[key], workspace="pool-one")
        data["jobs"]["j2"].update(state="waiting", waits=["j1"])
    reclaimed = []
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: reclaimed.append((run_id, dispatch)) or "destroyed pool-one dispatch ctx_lane0001")
    assert run("job", "end", "j1") == (0, "destroyed pool-one dispatch ctx_lane0001\nj1's lane ended; j1 stays waiting")
    assert reclaimed == [(RUN, DISPATCH)]
    data = state.load(remote)
    job = data["jobs"]["j1"]
    assert (job["state"], job["waits"], job["prs"], job["carries"], data["orders"]["o2"]["jobs"]) == ("waiting", [key], [url], {"o2": parts}, ["j1"])
    assert (job["deaths"][-1]["dispatch"], job["deaths"][-1]["why"]) == (DISPATCH, "ended by drive job end")
    monkeypatch.setattr(orca, "worker", lambda dispatch: pytest.fail("a lane drive job end ended is never read again as one that died"))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    later = state.now() + timedelta(minutes=10)
    assert (watch.waits_lines(watching, later), staged(watch.agent_lines, watching, later), watch.orphan_lines(watching, later, set())) == ([], [], [])
    assert state.load(remote)["jobs"]["j2"]["waits"] == ["j1"]


def test_end_refuses_while_the_sprite_of_a_job_still_at_work_cannot_be_saved(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, remote)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", workspace="pool-ie7ugrhamhjtmnwa")
    why = "orca-reclaim.py exited 1: ssh to pool-ie7ugrhamhjtmnwa could not read /home/sprite/monorepo: i/o timeout"
    monkeypatch.setattr(orca, "save", unsaveable(why))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: pytest.fail("a refused end reclaims nothing"))
    assert cli.main(["job", "end", "j1"]) == 2
    assert (
        "j1: refused, nothing ended: it is waiting, and what pool-ie7ugrhamhjtmnwa holds on no remote could not be saved to GitHub"
        f" for its next lane: {why}. End it again once that Sprite answers"
    ) in capsys.readouterr().err
    assert state.load(remote)["jobs"]["j1"].get("deaths", []) == []


def test_end_saves_what_the_sprite_of_a_job_still_at_work_holds_and_its_next_lane_gets_the_saved_branches(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), state="waiting", said="WAITING CI", workspace="pool-ie7ugrhamhjtmnwa")
    saves = iter([SAVED, None])
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: next(saves))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "destroyed pool-ie7ugrhamhjtmnwa dispatch ctx_lane0001")
    assert run("job", "end", "j1") == (
        0,
        f"destroyed pool-ie7ugrhamhjtmnwa dispatch ctx_lane0001\nj1's lane ended; j1 stays waiting, and what its Sprite held on no remote is saved: {SAVED}",
    )
    monkeypatch.setattr(orca, "worker", refused("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4"))
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(brief=brief.read_text())
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "resume", "j1", "Build #4412 failed: fix the lint")
    assert (code, out.endswith(f"and what pool-ie7ugrhamhjtmnwa held on no remote is saved: {SAVED}")) == (0, True)
    assert f"what it held on no remote was saved for you: {SAVED}." in orca.brief_of(launched["brief"])
    assert [(death["why"], death["saved"]) for death in state.load(remote)["jobs"]["j1"]["deaths"]] == [("ended by drive job end", SAVED)]


def test_end_destroys_the_sprite_cc_remote_still_records_or_names_what_keeps_it(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    carrying(run, remote)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="done", workspace="pool-one")
    workspaces = tmp_path / "cc-remote-workspaces"
    workspaces.mkdir()
    (workspaces / "pool-one.json").write_text("{}")
    monkeypatch.setattr(orca, "CC_REMOTE_WORKSPACES", workspaces)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "nothing to reclaim")
    released = []
    monkeypatch.setattr(orca, "release", lambda workspace, root: released.append(workspace) or "destroyed pool-one")
    assert run("job", "end", "j1") == (0, "nothing to reclaim\ndestroyed pool-one\nj1's lane ended; j1 stays done")
    assert released == ["pool-one"]

    def kept(workspace: str, root: Path) -> str:
        raise orca.OrcaError("orca-reclaim.py exited 1: uncommitted changes")

    monkeypatch.setattr(orca, "release", kept)
    assert run("job", "end", "j1") == (0, "nothing to reclaim\npool-one is kept: orca-reclaim.py exited 1: uncommitted changes\nj1's lane ended; j1 stays done")


def test_the_watch_frees_each_finished_lane_once_a_sprite_by_destroy_and_a_quiet_one_on_this_mac_by_its_dispatch_never_one_a_live_job_shares(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for goal in ("on this mac", "still at work", "its run lives on", "just finished"):
        run("job", "add", "builder", "--order", "o1", "--goal", goal, "--remote")
    quiet = state.stamp(T0 - watch.QUIET_LANE)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="done", workspace="pool-one")
        data["jobs"]["j2"].update(state="failed", route=state.LOCAL_ROUTE, run="run_mac", dispatch="ctx_mac", updated=quiet)
        data["jobs"]["j3"].update(state="running", route=state.LOCAL_ROUTE, run="run_live", dispatch="ctx_live", updated=quiet)
        data["jobs"]["j4"].update(state="stopped", route=state.LOCAL_ROUTE, run="run_live", dispatch="ctx_old", updated=quiet)
        data["jobs"]["j5"].update(state="done", route=state.LOCAL_ROUTE, run="run_new", dispatch="ctx_new", updated=state.stamp(T0))
    workspaces = tmp_path / "cc-remote-workspaces"
    workspaces.mkdir()
    (workspaces / "pool-one.json").write_text("{}")
    monkeypatch.setattr(orca, "CC_REMOTE_WORKSPACES", workspaces)
    monkeypatch.setattr(state, "scratch", lambda: False)
    monkeypatch.setattr(orca, "checkout", lambda name, data: tmp_path)
    started: list[tuple[str, ...]] = []
    finished = subprocess.CompletedProcess([], 0)
    finished.poll = lambda: 0
    monkeypatch.setattr(orca, "release_detached", lambda workspace, root, log: started.append(("destroy", workspace)) or finished)
    monkeypatch.setattr(orca, "reclaim_detached", lambda run_id, dispatch, root, log: started.append(("done", run_id, dispatch)) or finished)
    watcher = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    watch.release_lanes(watcher, T0)
    watch.release_lanes(watcher, T0)
    assert started == [("destroy", "pool-one"), ("done", "run_mac", "ctx_mac")]


def test_end_of_a_finished_job_never_reads_its_sprite_and_drops_the_parts_it_carried(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    carrying(run, remote)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="stopped", workspace="pool-one")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: pytest.fail("a finished job's lane ends whatever its Sprite holds"))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "")
    assert run("job", "end", "j1") == (0, "j1's lane ended; j1 stays stopped")
    data = state.load(remote)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["carries"], data["orders"]["o2"]["jobs"]) == ("stopped", {}, [])


def test_resume_after_end_relaunches_the_job_on_a_fresh_sprite_with_the_message_in_its_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    url = "https://github.com/Forge-AI/monorepo/pull/53935"
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), state="waiting", said=f"WAITING CI on {url}", prs=[url], workspace="pool-rxo7catmn6cw4efb")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "")
    assert run("job", "end", "j1") == (0, "j1's lane ended; j1 stays waiting")
    monkeypatch.setattr(orca, "worker", refused("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a lane whose Sprite is gone: {args}"))
    monkeypatch.setattr(orca, "new_run", lambda objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), run=run_id)
        fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
        return {field: f"{field}-2" for field in fields}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "resume", "j1", "Build #4412 failed: fix the lint") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-rxo7catmn6cw4efb was"
        " dead (Orca no longer holds its Sprite's environment), and work not pushed from pool-rxo7catmn6cw4efb is not carried over",
    )
    assert (launched["lane"], launched["run"]) == ("demo-j1-2", FRESH_RUN)
    brief = orca.brief_of(launched["brief"])
    assert ": ended by drive job end. You take the job over on a fresh Sprite from its pushed branches;" in brief
    assert f"Its last line: WAITING CI on {url}\nIts PRs: {url}\n" in brief
    assert brief.endswith("which you act on now:\n\nBuild #4412 failed: fix the lint")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], job["state"]) == ("dispatch-2", "workspace-2", "running")
    assert [(death["dispatch"], death["why"]) for death in job["deaths"]] == [(DISPATCH, "ended by drive job end")]


def test_lane_mail_becomes_job_events_and_records_the_question(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(
        monkeypatch,
        [
            message("heartbeat", "alive"),
            message("status", "building the watch"),
            message("status", "no body", body=None, id="msg_status_empty"),
            message("question", "Which fixture keeps the hold test deterministic?\nLOG https://buildkite.com/forge/test/builds/1 is red on it"),
        ],
    )
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        "JOB j1 o1 Which fixture keeps the hold test deterministic?; drive job resume reads LOG https://buildkite.com/forge/test/builds/1 ahead of your answer"
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == ("msg_question", "waiting", job["report"].splitlines()[0])
    assert job["report"].endswith("is red on it")


@pytest.mark.parametrize(
    ("sent", "asked", "taken"),
    [
        ("BUILT lane/built, waits on demo:j1-7-a", None, "built"),
        ("DONE port of #53187 merged", None, "done"),
        ("ANSWER (1): #53302 is not a deletion", "msg_question", "waiting"),
    ],
)
def test_a_lane_question_opening_with_a_result_other_than_ask_is_that_result_and_never_unanswered(
    sent: str, asked: str | None, taken: str, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", sent)])
    monkeypatch.setattr(github, "fetch", lambda keys: ({}, None))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [f"JOB j1 o1 {sent}"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"]) == (asked, taken)
    assert (watch.unanswered("j1", job, T0 + timedelta(minutes=31)) is None) == (asked is None)


@pytest.mark.parametrize("kind", ["question", "worker_done"])
def test_a_lanes_ready_is_kept_as_pushed_waking_no_one_until_a_read_after_it_finds_its_required_checks_green(
    kind: str, run: Run, remote: str, monkeypatch: pytest.MonkeyPatch, pr_reads: dict[str, dict]
) -> None:
    monkeypatch.setattr(state, "now", lambda: T0)
    url = "https://github.com/Forge-AI/monorepo/pull/7"
    assert run("pr", "add", url, "--job", "j1")[0] == 0
    deliver(monkeypatch, [message(kind, f"READY {url}\nPR: Fix: x")])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"], job["prs"]) == (None, "waiting", f"PUSHED {url}", [url])
    required = {"context": "buildkite/test", "state": "SUCCESS", "targetUrl": "https://buildkite.com/forge/monorepo/builds/7"}
    pr_reads["Forge-AI/monorepo#7"] = {
        "state": "OPEN", "isDraft": False, "headRefOid": "abc1234", "baseRefName": "dev", "headRefName": "fix-x", "mergeCommit": None, "checks": [required], "reviewDecision": None,
        "approvals": [], "mergeable": "MERGEABLE", "title": "Fix: x", "author": "yasyf", "labels": [], "native": [], "required_checks": ["buildkite/test"], "required_reviews": 1,
    }
    taken = f"JOB j1 o1 READY {url}; Forge-AI/monorepo#7 is at abc1234 on GitHub, red, owed by its builder: forge-pr-reviewer approved nothing at abc1234"
    assert [line for line in watch.run_pass(watching, T0 + timedelta(minutes=1)) if line.startswith(taken)]


def test_a_lane_that_finishes_after_asking_ends_its_question_and_its_result_sets_the_job(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(
        monkeypatch,
        [message("question", "#53090 at b6f66c236d70: green (buildkite/test 123111)", id="relay_5678e2af0b2d")],
        [message("worker_done", "READY #53090 head b6f66c236d70", id="relay_495e9eedbf7c")],
    )
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 #53090 at b6f66c236d70: green (buildkite/test 123111)"]
    assert state.load(remote)["jobs"]["j1"]["ask"] == "relay_5678e2af0b2d"
    assert watch.run_pass(watching, T0 + timedelta(minutes=10)) == ["JOB j1 o1 READY #53090 head b6f66c236d70"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == (None, "waiting", "READY #53090 head b6f66c236d70")
    assert watch.unanswered("j1", job, T0 + timedelta(minutes=41)) is None


@pytest.mark.parametrize(
    ("said", "report", "kept"),
    [
        ("READY #53195 sent for his tap", "READY https://github.com/Forge-AI/monorepo/pull/53195", None),
        ("CI and reviewer on the reworked chat-open tags PR", "\nWAITING CI and the reviewer bot on the reworked chat-open tags PR", None),
        ("READY #53447 at 925dbd0c, rebased onto dev", "Rebased onto dev with ccx in the conflict worktree.", None),
        ("How should a table move copy a live SandSQL database?", "ASK How should a table move copy a live SandSQL database?", "relay_q"),
        ("Which fixture keeps the hold test deterministic?", "Which fixture keeps the hold test deterministic?", "relay_q"),
    ],
)
def test_a_drive_saved_with_a_lane_result_kept_as_its_question_loads_with_none(
    said: str, report: str, kept: str | None, remote: str
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="relay_q", state="waiting", said=said, report=report, updated=state.stamp(T0))
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"]) == (kept, "waiting")


@pytest.mark.parametrize(
    "said",
    [
        "LOG {url}",
        "j1 READ: LOG {url} (#7 head 1234abc), failing while it still runs. Give every failed job's name.",
    ],
)
def test_the_watch_answers_a_read_question_itself_and_keeps_the_job_as_it_was(
    said: str, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://buildkite.com/forge/test/builds/121531"
    deliver(monkeypatch, [message("question", said.format(url=url), id="msg_log")])
    asked, replies = [], []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: asked.append((question, root, drive, job_id)) or "ERROR: journal write")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    assert asked == [(f"LOG {url}", tmp_path, remote, "j1")]
    assert replies == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_log", "--run", RUN, "--body", "ERROR: journal write"),
        ("run-use", "--id", DRIVE_RUN),
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == (None, "running", None)


def test_a_question_naming_logs_past_its_first_line_goes_to_the_coordinator_saying_resume_reads_them(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", "j1 PUSHED #7\nLOG https://buildkite.com/forge/test/builds/1", id="msg_q")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: pytest.fail(f"the watch read {question}"))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        "JOB j1 o1 j1 PUSHED #7; drive job resume reads LOG https://buildkite.com/forge/test/builds/1 ahead of your answer"
    ]
    assert state.load(remote)["jobs"]["j1"]["ask"] == "msg_q"


def test_a_read_that_cannot_be_answered_tells_its_lane_why_and_says_so(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    question = "LOG https://buildkite.com/forge/test/builds/1"
    deliver(monkeypatch, [message("question", question, id="msg_log")])

    def refuse(question: str, root: Path, drive: str, job_id: str) -> str:
        raise reads.ReadError("tools/ci bk api exited 1: 404 Not Found")

    monkeypatch.setattr(reads, "answer", refuse)
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    why = f"{question}: tools/ci bk api exited 1: 404 Not Found"
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [f"JOB j1 o1 READ unanswered, its lane was told why: {why}"]
    assert ("reply", "--id", "msg_log", "--run", RUN, "--body", why) in replies
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []


def test_a_read_answer_orca_refuses_is_retried_until_it_goes_through_and_said_from_the_third_refusal(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", "LOG https://buildkite.com/forge/test/builds/1", id="msg_log")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "log")
    replies, refusals = [], iter(range(3))

    def fenced_three_times(*args: str) -> dict:
        if args[0] == "reply" and next(refusals, None) is not None:
            raise orca.OrcaError("orca orchestration reply: consumer_fenced: This caller is bound to Run run_other")
        replies.append(args)
        return {}

    monkeypatch.setattr(orca, "call", fenced_three_times)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    passes = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10)]
    assert passes == [
        [],
        [],
        ["WATCH the answer to j1's read failed 3 times in a row: orca orchestration reply: consumer_fenced: This caller is bound to Run run_other"],
    ]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {"msg_log": "LOG https://buildkite.com/forge/test/builds/1"}
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []
    assert replies[-2:] == [("reply", "--id", "msg_log", "--run", RUN, "--body", "log"), ("run-use", "--id", DRIVE_RUN)]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}


def test_a_read_someone_already_answered_is_dropped_with_no_line(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", "READ ccn answer search x", id="relay_6779eb1e29a6")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "x")
    replies = []
    monkeypatch.setattr(orca, "call", answered_elsewhere(replies))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []
    assert [args for args in replies if args[0] == "reply"] == [
        ("reply", "--id", "relay_6779eb1e29a6", "--run", RUN, "--body", "x")
    ]


def test_a_read_whose_dispatch_closed_is_dropped_and_said_once(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", "LOG https://buildkite.com/forge/test/builds/124676", id="relay_4d6bfb09372e")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "log")
    replies = []
    closed = f"Dispatch {DISPATCH} finished on pool-one, which cannot take follow-ups for finished workers yet."
    monkeypatch.setattr(orca, "call", reply_refused(replies, "dispatch_inactive", closed))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    passes = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 70)]
    assert passes == [
        [f"JOB j1 o1 READ dropped, its dispatch closed before its answer went: orca orchestration reply: dispatch_inactive: {closed}"],
        [],
        [],
        [],
    ]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}
    assert [args for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_4d6bfb09372e", "--run", RUN, "--body", "log")]


@pytest.mark.parametrize(
    ("question", "sent"),
    [
        ('READ ccn answer search "dom extraction"', f'READ ccn answer search "dom extraction": {watch.CUT_OFF}'),
        (BREAK_GLASS_READ, reads.lane_exit(1, watch.CUT_OFF)),
    ],
)
def test_a_read_stopped_before_it_finished_is_answered_as_cut_off_and_never_run_again(
    question: str, sent: str, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", question, id="relay_read")])

    def stopped(question: str, root: Path, drive: str, job_id: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(reads, "answer", stopped)
    with pytest.raises(KeyboardInterrupt):
        watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    replies = []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: pytest.fail(f"the next watch ran {question} again"))
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0 + timedelta(minutes=5)) == [
        f"JOB j1 o1 READ unanswered, its lane was told why: {watch.CUT_OFF}"
    ]
    assert ("reply", "--id", "relay_read", "--run", RUN, "--body", sent) in replies
    job = state.load(remote)["jobs"]["j1"]
    assert (job["reads"], job["read_answers"]) == ({}, {})


def test_a_read_still_running_holds_its_answer_starts_no_second_reader_and_its_answer_goes_once_kept(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", "DRIVE job report j1 -\n{\"stdin\": \"READY x\"}", id="relay_drive")])
    started, replies = [], []

    def report(question: str, root: Path, drive: str, job_id: str) -> str:
        with state.edit(drive) as data:
            state.take_result(data, job_id, "PUSHED x", "PUSHED x")
        return "drive exited 0\njobs/j1.replies.jsonl"

    monkeypatch.setattr(watch, "spawn_reader", lambda name, job_id, message_id: started.append(message_id) or os.getpid())
    monkeypatch.setattr(reads, "answer", report)
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert [staged(watch.lane_lines, watching, T0 + timedelta(seconds=s)) for s in (0, 5)] == [[], []]
    assert (started, [args for args in replies if args[0] == "reply"]) == (["relay_drive"], [])
    watch.answer_read(remote, "j1", "relay_drive")
    assert staged(watch.lane_lines, watching, T0 + timedelta(seconds=10)) == []
    assert [args for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_drive", "--run", RUN, "--body", "drive exited 0\njobs/j1.replies.jsonl")]
    job = state.load(remote)["jobs"]["j1"]
    assert (started, job["reads"], job["read_answers"], job["readers"]) == (["relay_drive"], {}, {}, {})


@pytest.mark.parametrize("cut_off_kept", [True, False])
def test_a_reader_that_finishes_as_the_watch_checks_it_starts_no_second_reader_and_its_final_answer_goes(
    cut_off_kept: bool, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = "READ ccn answer search repro cut"
    deliver(monkeypatch, [message("question", question, id="relay_read")])
    reader, started, replies = 4242, [], []

    def start(name: str, job_id: str, message_id: str) -> int:
        started.append(message_id)
        if cut_off_kept:
            watch.keep_answer(name, job_id, message_id, [f"{question}: {watch.CUT_OFF}", watch.CUT_OFF])
        return reader

    def finishes_as_checked(pid: int) -> bool:
        if pid == reader:
            watch.keep_answer(remote, "j1", "relay_read", [f"{question}: empty, the read printed nothing", None])
        return False

    monkeypatch.setattr(watch, "spawn_reader", start)
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    monkeypatch.setattr(state, "alive", lambda pid: pid == reader)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert staged(watch.lane_lines, watching, T0) == []
    monkeypatch.setattr(state, "alive", finishes_as_checked)
    assert staged(watch.lane_lines, watching, T0 + timedelta(seconds=5)) == []
    assert [args for args in replies if args[0] == "reply"] == [
        ("reply", "--id", "relay_read", "--run", RUN, "--body", f"{question}: empty, the read printed nothing")
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (started, job["reads"], job["read_answers"], job["readers"]) == (["relay_read"], {}, {}, {})


def test_a_break_glass_answer_orca_refuses_is_sent_again_and_its_command_never_runs_twice(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", BREAK_GLASS_READ, id="msg_glass")])
    ran = []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: ran.append(question) or "DROP TABLE")
    replies, refusals = [], iter(range(2))

    def refused_twice(*args: str) -> dict:
        if args[0] == "reply" and next(refusals, None) is not None:
            raise orca.OrcaError("orca orchestration reply: consumer_fenced: This caller is bound to Run run_other")
        replies.append(args)
        return {}

    monkeypatch.setattr(orca, "call", refused_twice)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    for seconds in (0, 5):
        watch.run_pass(watching, T0 + timedelta(seconds=seconds))
        assert state.load(remote)["jobs"]["j1"]["read_answers"] == {"msg_glass": ["DROP TABLE", None]}
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == []
    assert ran == [BREAK_GLASS_READ]
    assert ("reply", "--id", "msg_glass", "--run", RUN, "--body", "DROP TABLE") in replies
    job = state.load(remote)["jobs"]["j1"]
    assert (job["reads"], job["read_answers"]) == ({}, {})


def test_an_open_question_kept_before_the_watch_read_it_as_a_read_is_answered_and_its_lane_runs_again(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch)
    said = "LOG https://buildkite.com/forge/test/builds/123761 and LOG https://buildkite.com/forge/test/builds/123764 (#49869 and #53227)."
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="relay_kept", state="waiting", said=said, report=f"{said}\n#53313's two reds are fixed at 29d4d04e")
    asked, replies = [], []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: asked.append(question) or "FAIL hold_test.go:41")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == []
    assert asked == ["LOG https://buildkite.com/forge/test/builds/123761 https://buildkite.com/forge/test/builds/123764"]
    assert ("reply", "--id", "relay_kept", "--run", RUN, "--body", "FAIL hold_test.go:41") in replies
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["reads"]) == (None, "running", {})


def test_an_ask_naming_the_block_drive_job_ask_kept_and_posted_wakes_the_coordinator_with_its_file(
    remote: str, monkeypatch: pytest.MonkeyPatch, drawing: Drawing, asks_sent: list[dict]
) -> None:
    kept = cli.keep_ask(remote, "j1", ASK_IN_SHAPE, None)[0]
    deliver(monkeypatch, [message("question", f"ASK {kept}", id="msg_ask")])
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"the watch replied {args}"))
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == [f"JOB j1 o1 ASK {kept}"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], [(question["file"], question["waits"]) for question in job["questions"]]) == ("msg_ask", "waiting", [(str(kept), f"{remote}:j1-7")])
    assert [(sent["kind"], sent["to"], sent["attr"]["ask"]) for sent in asks_sent] == [("ask", [f"communicator@{remote}"], str(kept))]


@pytest.mark.parametrize("sent", [ASK_IN_SHAPE, "ASK /home/sprite/scratch/j1/ask.md"])
def test_an_ask_no_drive_job_ask_kept_goes_to_the_coordinator_saying_drive_job_ask_keeps_it_and_keeps_no_file(
    sent: str, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", sent, id="msg_ask")])
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"the watch replied {args}"))
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == [
        f"JOB j1 o1 {sent.splitlines()[0]}; {watch.UNKEPT_ASK.format(job='j1')}"
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["questions"], kept_ask(remote).exists()) == ("msg_ask", "waiting", [], False)


@pytest.mark.parametrize("named", ["#4127", "Forge-AI/monorepo#4127", "https://github.com/Forge-AI/monorepo/pull/4127"])
def test_a_holds_line_naming_a_pr_the_drive_tracks_on_the_jobs_order_is_kept_with_the_parts_its_job_works(named: str, remote: str) -> None:
    held = f'{named} "the nightly store copy runs on its own worker", where keeping copy times adds two store_copies columns'
    asked = ASK_IN_SHAPE.replace("Holds: none: no option changes code any job builds", f"Holds: {held}")
    with pytest.raises(state.DriveError, match="names no change of j1's orders"):
        cli.keep_ask(remote, "j1", asked, None)
    with state.edit(remote) as data:
        data["prs"]["Forge-AI/monorepo#41270"] = state.new_pr(state.pr_url("Forge-AI/monorepo#41270"), "j1", "o1")
    with pytest.raises(state.DriveError, match="names no change of j1's orders"):
        cli.keep_ask(remote, "j1", asked, None)
    with state.edit(remote) as data:
        data["prs"]["Forge-AI/monorepo#4127"] = state.new_pr(state.pr_url("Forge-AI/monorepo#4127"), "j1", "o1")
    question = cli.keep_ask(remote, "j1", asked, None)[2]
    assert question["holds"] == f"{held}; ship the remote builder"
    assert state.load(remote)["jobs"]["j1"]["questions"][-1]["holds"] == question["holds"]


def test_a_lane_ask_no_drive_job_ask_kept_is_an_orphan_saying_drive_job_ask_keeps_it_never_that_a_resume_answers_it(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", ASK_IN_SHAPE, id="relay_ask")])
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"the watch replied {args}"))
    watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    job = state.load(remote)["jobs"]["j1"]
    assert watch.unanswered("j1", job, T0 + timedelta(minutes=31)) == (
        f'j1\'s lane asked "ASK Where should the nightly store copy run?" 31 minutes ago, an ASK only his card answers: {watch.UNKEPT_ASK.format(job="j1")}'
    )
    kept = cli.keep_ask(remote, "j1", ASK_IN_SHAPE, None)[0]
    job = state.load(remote)["jobs"]["j1"] | {"report": f"ASK {kept}"}
    assert watch.UNKEPT_ASK.format(job="j1") not in (watch.unanswered("j1", job, T0 + timedelta(minutes=31)) or "")


def test_a_lane_question_nothing_answers_is_an_orphan_every_thirty_minutes_whatever_older_card_its_job_has(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cards(*drawn: tuple[str, datetime]) -> None:
        listed = [{"question": "Run the data pass?", "options": ["Yes", "No"], "recommended": "Yes", "asked": state.stamp(at), "url": url, "card": {"title": "Run the data pass?"}} for url, at in drawn]
        monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": listed, "closed": [], "pr_cards": []})

    older, newer = (T0 - timedelta(hours=8), T0 + timedelta(minutes=61))
    older_card, newer_card = "https://acme.slack.com/archives/D123/p1794345043324699", "https://acme.slack.com/archives/D123/p1794388741836339"
    cards((older_card, older))
    deliver(monkeypatch, [message("question", "Which fixture keeps the hold test deterministic?", id="relay_q")])
    with state.edit(remote) as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(None, "Run the data pass?", state.stamp(older), card=older_card)]
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 Which fixture keeps the hold test deterministic?"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=29)) == []
    said = (
        'ORPHAN o1 "ship the remote builder": j1\'s lane asked "Which fixture keeps the hold test deterministic?" {} minutes ago'
        ' and nothing answered it: drive job resume j1 "<the answer>" answers it'
    )
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [said.format(30)]
    assert watch.run_pass(watching, T0 + timedelta(minutes=45)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == [said.format(60)]
    cards((older_card, older), (newer_card, newer))
    with state.edit(remote) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Which fixture?", state.stamp(newer), card=newer_card))
    assert watch.run_pass(watching, T0 + timedelta(minutes=95)) == []
    assert state.load(remote)["orders"]["o1"]["parts"]["ship the remote builder"]["orphan_said"] is None


def test_every_worker_on_a_jobs_own_run_is_heard_the_one_it_replaced_and_one_it_never_recorded_included(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(dispatch="ctx_lane0003", deaths=[{"dispatch": DISPATCH, "workspace": "pool-one", "why": "x", "at": "y"}])
    pr = "https://github.com/Forge-AI/monorepo/pull/7"
    assert run("pr", "add", pr, "--job", "j1")[0] == 0
    deliver(
        monkeypatch,
        [
            message("question", "READ ccn answer search x", id="msg_read"),
            message("worker_done", f"READY {pr}\nI edited only the body", id="msg_unrecorded", from_handle="dispatch:ctx_lane0002"),
            message("worker_done", f"READY {pr}\nThis rewrote Show me", id="msg_newest", from_handle="dispatch:ctx_lane0003"),
        ],
    )
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "x")
    calls = []
    monkeypatch.setattr(orca, "call", lambda *args: calls.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert staged(watch.lane_lines, watching, T0) == []
    assert [args[:3] for args in calls if args[0] == "reply"] == [("reply", "--id", "msg_read")]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["said"], job["report"], job["prs"]) == ("waiting", f"PUSHED {pr}", f"READY {pr}\nThis rewrote Show me", [pr])
    assert [reply["id"] for reply in state.replies(remote, "j1")] == ["msg_read", "msg_unrecorded", "msg_newest"]


def test_a_watch_whose_mail_mark_another_install_lost_takes_none_of_the_mail_its_job_kept(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    kept = [
        message("question", ASK_IN_SHAPE, id="msg_ask"),
        message("question", "READ ccn answer search x", id="msg_read"),
        message("worker_done", "PUSHED https://github.com/Forge-AI/monorepo/pull/7", id="msg_pushed"),
    ]
    done = message("worker_done", "DONE merged and installed", id="msg_done")
    deliver(monkeypatch, kept, [*kept, done])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "x")
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert staged(watch.lane_lines, watching, T0) == [
        f"JOB j1 o1 ASK Where should the nightly store copy run?; {watch.UNKEPT_ASK.format(job='j1')}",
        "JOB j1 o1 PUSHED https://github.com/Forge-AI/monorepo/pull/7; the drive tracks no https://github.com/Forge-AI/monorepo/pull/7: drive pr add <url> --job j1 registers each",
    ]
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(mail_seen=0, state="running")
    assert staged(watch.lane_lines, watching, T0 + timedelta(seconds=5)) == ["JOB j1 o1 DONE merged and installed"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["mail_seen"]) == (None, "done", done["sequence"])
    assert len([args for args in replies if args[0] == "reply"]) == 1
    assert [json.loads(line)["id"] for line in state.replies_path(remote, "j1").read_text().splitlines()] == ["msg_ask", "msg_read", "msg_pushed", "msg_done"]


def test_every_result_and_question_a_lane_sends_stays_readable_after_the_next_one_replaces_its_report(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    alive = message("heartbeat", "alive")
    detail = message("worker_done", "FINDINGS: the proof identity is a full employee session", id="msg_1", created_at="2026-11-10T04:40:00Z")
    short = message("question", "BLOCKED on his pick (details in my last reply)", id="msg_2", created_at="2026-11-10T04:41:00Z")
    deliver(monkeypatch, [alive, detail], [short])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ["JOB j1 o1 BLOCKED on his pick (details in my last reply)"]
    assert state.load(remote)["jobs"]["j1"]["report"] == "BLOCKED on his pick (details in my last reply)"
    code, out = run("job", "replies", "j1")
    assert (code, out) == (
        0,
        "2026-11-10T04:40:00Z worker_done: FINDINGS: the proof identity is a full employee session\n\n"
        "2026-11-10T04:41:00Z question: BLOCKED on his pick (details in my last reply)",
    )
    assert [reply["id"] for reply in json.loads(run("job", "replies", "j1", "--json")[1])] == ["msg_1", "msg_2"]


def test_a_lane_that_stops_without_a_report_names_its_last_output(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "Worker term_1 ended its turn without sending worker_done.\nLast output:\npushed the fix\nFull output: x"
    deliver(monkeypatch, [message("escalation", body, subject="Worker stopped without reporting")])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 STOPPED without a report, last output: pushed the fix"]


def test_mail_is_read_only_while_a_remote_lane_is_live(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = deliver(monkeypatch)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    watch.run_pass(watching, T0)
    with state.edit(remote) as data:
        data["jobs"]["j1"]["state"] = "done"
    watch.run_pass(watching, T0 + timedelta(seconds=60))
    assert calls == [RUN]


def test_a_failed_mail_read_retries_next_poll_and_says_so_from_the_third_failure_in_a_row(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def broken(run: str, after: int) -> list[dict]:
        calls.append(run)
        raise orca.OrcaError("orca orchestration check: runtime_unavailable: no answer")

    monkeypatch.setattr(orca, "mail", broken)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    said = f"WATCH lane mail of {RUN} read failed {{}} times in a row: orca orchestration check: runtime_unavailable: no answer"
    assert [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 15)] == [[], [], [said.format(3)], []]
    assert calls == [RUN] * 3
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=10, seconds=10)) == [said.format(5)]


def test_lane_names_fit_a_sprite() -> None:
    assert orca.lane_name("Durable_Agents.v2", "j12") == "durable-agents-v2-j12"
    long = orca.lane_name("x" * 80, "j3")
    assert len(long) <= orca.LANE_LIMIT
    assert long.endswith("-j3")


def test_each_lane_is_read_from_its_own_run(run: Run, remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("job", "add", "builder", "--order", "o1", "--goal", "second lane", "--remote")
    with state.edit(remote) as data:
        data["jobs"]["j2"].update(run="run_drive0002", dispatch="ctx_lane0002", ask=None)
    mailboxes = {
        RUN: [message("question", "READY first", id="msg_1")],
        "run_drive0002": [message("question", "READY second", id="msg_2", from_handle="dispatch:ctx_lane0002")],
    }
    monkeypatch.setattr(orca, "mail", lambda run_id, after: mailboxes.pop(run_id))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 READY first", "JOB j2 o1 READY second"]
    assert mailboxes == {}


def test_a_run_a_finished_job_shares_gives_the_live_job_only_its_own_workers_mail(run: Run, remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("job", "add", "builder", "--order", "o1", "--goal", "an earlier lane on the drive's old shared Run", "--remote")
    with state.edit(remote) as data:
        data["jobs"]["j2"].update(run=RUN, dispatch="ctx_lane0002", state="done")
    deliver(monkeypatch, [message("worker_done", "DONE earlier", id="msg_old", from_handle="dispatch:ctx_lane0002"), message("worker_done", "BLOCKED CI", id="msg_own")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == ["JOB j1 o1 BLOCKED CI"]
    assert [reply["id"] for reply in state.replies(remote, "j1")] == ["msg_own"]


def test_two_watches_reading_one_shared_run_each_take_the_mail_of_the_jobs_they_hear_lose_none_and_take_no_other_workers(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o2", "--goal", "run page", "--remote")
    with state.edit(remote) as data:
        data["jobs"]["j2"].update(run=RUN, dispatch="ctx_lane0002", ask=None, report=None)
        cli.bind_subcoordinator(data, "Iris", {"session": "iris-1", "terminal": None, "cwd": "/work", "started": state.stamp(T0)})
    first = message("question", "BLOCKED run page", id="msg_iris", from_handle="dispatch:ctx_lane0002")
    second = message("question", "BLOCKED remote builder", id="msg_top")
    stray = message("worker_done", "DONE elsewhere", id="msg_stray", from_handle="dispatch:ctx_other")
    monkeypatch.setattr(orca, "mail", lambda run_id, after: [first, second, stray])
    top = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    iris = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0, project="Iris")
    assert staged(watch.lane_lines, top, T0) == ["JOB j1 o1 BLOCKED remote builder"]
    assert staged(watch.lane_lines, iris, T0) == ["JOB j2 o2 BLOCKED run page"]
    assert (staged(watch.lane_lines, top, T0), staged(watch.lane_lines, iris, T0)) == ([], [])
    jobs = state.load(remote)["jobs"]
    assert (jobs["j1"]["mail_seen"], jobs["j2"]["mail_seen"]) == (second["sequence"], first["sequence"])
    assert [reply["id"] for job_id in ("j1", "j2") for reply in state.replies(remote, job_id)] == ["msg_top", "msg_iris"]


def test_two_watches_reading_one_run_past_orcas_newest_hundred_each_take_every_message_of_the_jobs_they_hear(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o2", "--goal", "run page", "--remote")
    with state.edit(remote) as data:
        data["jobs"]["j2"].update(run=RUN, dispatch="ctx_lane0002", ask=None, report=None)
        cli.bind_subcoordinator(data, "Iris", {"session": "iris-1", "terminal": None, "cwd": "/work", "started": state.stamp(T0)})
    senders = [f"dispatch:{DISPATCH}", "dispatch:ctx_lane0002"]
    mailbox = [message("status", f"step {n}", id=f"msg_{n}", from_handle=senders[n % 2]) for n in range(1, 101)]
    limits = []

    def inbox(*args: str) -> dict:
        limits.append(int(args[args.index("--limit") + 1]))
        return {"messages": mailbox[::-1][: limits[-1]]}

    monkeypatch.setattr(orca, "call", inbox)
    top = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    iris = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0, project="Iris")
    staged(watch.lane_lines, top, T0)
    staged(watch.lane_lines, iris, T0)
    mailbox += [message("status", f"step {n}", id=f"msg_{n}", from_handle=senders[n % 2]) for n in range(101, 251)]
    staged(watch.lane_lines, top, T0)
    staged(watch.lane_lines, iris, T0)
    assert [reply["id"] for reply in state.replies(remote, "j1")] == [f"msg_{n}" for n in range(2, 251, 2)]
    assert [reply["id"] for reply in state.replies(remote, "j2")] == [f"msg_{n}" for n in range(1, 251, 2)]
    assert limits == [100, 1000] * 4
    staged(watch.lane_lines, top, T0)
    assert limits[-1] == 100


def test_a_lane_report_the_watch_takes_names_each_pr_the_drive_does_not_track_and_registers_none(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/Forge-AI/monorepo/pull/8"
    deliver(monkeypatch, [message("worker_done", f"PUSHED {url}\nPR: Fix: x {url}\nrebased on https://github.com/Forge-AI/monorepo/pull/9")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == [
        f"JOB j1 o1 PUSHED {url}; the drive tracks no {url}: drive pr add <url> --job j1 registers each"
    ]
    assert state.load(remote)["prs"] == {}


@pytest.mark.parametrize(("line", "woken"), [("DONE merged and installed", ["JOB j1 o1 DONE merged and installed"]), ("PUSHED https://github.com/Forge-AI/monorepo/pull/8", [])])
def test_a_lanes_drive_job_report_wakes_the_coordinator_with_its_line_as_its_answer_goes_whatever_state_it_set(
    line: str, woken: list[str], remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", f"DRIVE job report j1 -\n{json.dumps({'stdin': line})}", id="relay_report")])

    def report(question: str, root: Path, drive: str, job_id: str) -> str:
        with state.edit(drive) as data:
            state.take_result(data, job_id, line, line)
        return reads.lane_exit(0, str(state.replies_path(drive, job_id)))

    replies = []
    monkeypatch.setattr(reads, "answer", report)
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == woken
    assert [args[:3] for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_report")]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}


def test_a_lanes_drive_job_report_that_left_its_job_no_line_wakes_nobody_and_its_answer_goes(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", f"DRIVE job report j1 -\n{json.dumps({'stdin': 'x'})}", id="relay_report")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: reads.lane_exit(0, str(state.replies_path(drive, job_id))))
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert state.load(remote)["jobs"]["j1"]["said"] is None
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == []
    assert [args[:3] for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_report")]


def test_a_report_drive_job_report_took_is_not_taken_again_when_its_lane_sends_it(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/Forge-AI/monorepo/pull/8"
    report = f"PUSHED {url}\nPR: {url}\nCHANGE: x\n"
    with state.edit(remote) as data:
        state.keep_report(remote, "j1", f"PUSHED {url}", report)
        state.take_result(data, "j1", f"PUSHED {url}", report)
    taken = state.load(remote)["jobs"]["j1"]
    deliver(monkeypatch, [message("worker_done", report, id="msg_report")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == []
    job, prs = state.load(remote)["jobs"]["j1"], state.load(remote)["prs"]
    assert ({key: job[key] for key in ("said", "state", "waits", "returned")}, prs) == ({key: taken[key] for key in ("said", "state", "waits", "returned")}, {})
    assert [reply["id"].startswith(state.REPORT_REPLY) for reply in state.replies(remote, "j1")] == [True, False]


def test_a_lanes_ready_on_a_yasyf_pr_it_added_with_its_proof_file_kept_starts_the_lander_once_the_watch_takes_it(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]], tmp_path: Path
) -> None:
    with state.edit(remote) as data:
        data["orders"]["o1"]["text"] = "Drive tooling: ship the remote builder"
    url = "https://github.com/yasyf/cc-skills/pull/7"
    proof = tmp_path / "proof.sh"
    proof.write_text("#!/bin/sh\nexit 0\n")
    proof.chmod(0o755)
    assert run("pr", "add", url, "--job", "j1")[0] == 0
    assert run("job", "set", "j1", "--repro", str(proof))[0] == 0
    deliver(monkeypatch, [message("worker_done", f"READY {url}\nPR: {url}")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == []
    data = state.load(remote)
    pr, job = data["prs"]["yasyf/cc-skills#7"], data["jobs"]["j1"]
    assert (landers, pr["job"], pr["wants_tap"], pr["lander"]["head"]) == ([("yasyf/cc-skills#7",)], "j1", False, "abc")
    assert (job["said"], job["prs"], job["waits"]) == (f"PUSHED {url}", [url], ["yasyf/cc-skills#7"])


def test_a_lanes_ready_on_a_yasyf_pr_with_no_proof_file_is_refused_and_starts_no_lander(
    remote: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    url = "https://github.com/yasyf/cc-skills/pull/7"
    deliver(monkeypatch, [message("worker_done", f"READY {url}\nPR: {url}")])
    [line] = staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    assert line.startswith(f"JOB j1 o1 PUSHED {url}; refused, its state unset: it names yasyf/cc-skills#7, which merges once this is taken")
    assert (state.load(remote)["jobs"]["j1"]["state"], landers) == ("running", [])


def test_the_inbox_turns_each_message_into_one_event_even_one_a_consuming_check_took_first(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/7", "--job", "j1")[0] == 0
    taken = message("worker_done", "WAITING CI on #7\nPR: https://github.com/Forge-AI/monorepo/pull/7", id="msg_old", read=1)
    first = message("status", "building", id="msg_1")
    second = message("question", "WAITING review on #7", id="msg_2")
    deliver(monkeypatch, [taken, first], [taken, first, second], [taken, first, second])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    passes = [watch.run_pass(watching, T0 + timedelta(minutes=m)) for m in (0, 1, 2)]
    assert passes == [["JOB j1 o1 WAITING CI on #7"], ["JOB j1 o1 WAITING review on #7"], []]
    assert state.load(remote)["jobs"]["j1"]["mail_seen"] == second["sequence"]


class Started:
    """A worker-start as Popen runs it: exited with its output, or, given running, still running until communicate calls running for its output."""

    def __init__(self, argv: list[str], stdout: str = "{}", returncode: int = 0, running: Callable[[], str] | None = None) -> None:
        self.args, self.out, self.running = argv, stdout, running
        self.returncode = None if running else returncode

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self) -> tuple[str, str]:
        if self.running:
            self.out, self.returncode = self.running(), 0
        return self.out, ""


@pytest.mark.parametrize("given", [(), ("--retry-request", "req_launcher")])
def test_a_reply_during_a_launch_waits_only_until_orca_takes_its_worker_start_request(remote: str, monkeypatch: pytest.MonkeyPatch, given: tuple[str, ...]) -> None:
    binds, shown, started = [], [], []

    def orca_call(*args: str) -> dict:
        if args[0] == "run-use":
            binds.append(args[2])
        if args[0] != "request-show":
            return {}
        shown.append(args[2])
        return {"state": "absent" if len(shown) == 1 else "pending"}

    monkeypatch.setattr(orca, "call", orca_call)
    replying = threading.Thread(target=orca.reply, args=(remote, "run_other", "msg_1", "an answer"))

    def ready() -> str:
        replying.start()
        replying.join(timeout=5)
        return json.dumps({"reply_waiting": replying.is_alive()})

    monkeypatch.setattr(orca.subprocess, "Popen", lambda argv, **kwargs: started.append(argv) or Started(argv, running=ready))
    done = orca.bound_command(remote, RUN, ["orca", "orchestration", "worker-start", "--run", RUN, *given])
    replying.join()
    [argv] = started
    assert json.loads(done.stdout) == {"reply_waiting": False}
    assert binds == [RUN, DRIVE_RUN, "run_other", DRIVE_RUN]
    assert (argv.count("--retry-request"), shown) == (1, [argv[argv.index("--retry-request") + 1]] * 2)
    assert not given or shown[0] == "req_launcher"


def test_every_lane_call_leaves_the_terminal_on_the_drives_own_run_made_once_even_when_orca_refuses_the_call(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(remote) as data:
        del data["run"]
    calls = []

    def orca_call(*args: str) -> dict:
        calls.append(args[:3])
        if args[0] == "run-create":
            return {"run": {"id": DRIVE_RUN}}
        if args[0] == "reply":
            raise orca.OrcaError("orca orchestration reply: consumer_fenced: This caller is bound to Run run_other")
        return {}

    monkeypatch.setattr(orca, "call", orca_call)
    monkeypatch.setattr(orca.subprocess, "Popen", lambda argv, **kwargs: Started(argv))
    orca.bound_command(remote, RUN, ["orca", "orchestration", "worker-start", "--run", RUN])
    with pytest.raises(orca.OrcaError):
        orca.reply(remote, RUN, "msg_1", "an answer")
    assert calls == [
        ("run-create", "--objective", f"drive {remote}: the coordinator's own Run, which no lane mails"),
        ("run-use", "--id", RUN),
        ("run-use", "--id", DRIVE_RUN),
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_1"),
        ("run-use", "--id", DRIVE_RUN),
    ]
    assert state.load(remote)["run"] == DRIVE_RUN


def test_bound_keeps_the_worker_start_receipt_and_status_when_a_newer_install_saves_the_drive_while_it_runs(
    remote: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binds = []
    monkeypatch.setattr(orca, "call", lambda *args: binds.append(args[2]) if args[0] == "run-use" else {})
    receipt = json.dumps({"ok": True, "result": {"state": "ready", "dispatchId": DISPATCH}})

    def worker_start(argv: list[str], **kwargs: object) -> Started:
        state.store(remote, state.stored(remote) | {"saved_by": "9.9.9"})
        return Started(argv, stdout=receipt)

    monkeypatch.setattr(orca.subprocess, "Popen", worker_start)
    assert cli.main(["bound", "--drive", remote, RUN, "--", "orca", "orchestration", "worker-start"]) == 0
    assert capsys.readouterr().out == receipt
    assert binds == [RUN, DRIVE_RUN]


NO_RUN = "orca orchestration run-use: run_not_found: no such Run"


@pytest.mark.parametrize(
    ("refused", "starts", "binds"),
    [
        ("lane binding", 0, [RUN, DRIVE_RUN]),
        ("lane binding after a fence", 1, [RUN, RUN, DRIVE_RUN]),
    ],
)
def test_bound_exits_75_when_it_refuses_before_a_worker_start_could_start_anything(
    remote: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: str, starts: int, binds: list[str]
) -> None:
    bound = []

    def orca_call(*args: str) -> dict:
        bound.append(args[2])
        if bound == binds[:-1]:
            raise orca.OrcaError(NO_RUN)
        return {}

    fenced = json.dumps({"ok": False, "error": {"code": orca.FENCED}})
    started = []
    monkeypatch.setattr(orca, "call", orca_call)
    monkeypatch.setattr(orca.subprocess, "Popen", lambda argv, **kwargs: started.append(argv) or Started(argv, stdout=fenced, returncode=1))
    assert cli.main(["bound", "--drive", remote, RUN, "--", "orca", "orchestration", "worker-start"]) == orca.UNSTARTED
    out, err = capsys.readouterr()
    assert (out, err.startswith(f"drive: {NO_RUN}"), len(started), bound) == ("", True, starts, binds)


def test_a_root_recorded_by_its_orca_dispatch_is_read_through_worker_show_and_its_run_mail_and_is_no_orphan(
    run: Run, rooted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run("pr", "add", "https://github.com/Forge-AI/monorepo/pull/9", "--job", "j1")[0] == 0
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    shown = []
    monkeypatch.setattr(orca, "worker", lambda dispatch: shown.append(dispatch) or ROOT_WORKER)
    calls = deliver(monkeypatch, [message("worker_done", "WAITING CI on PR #9\nPR: https://github.com/Forge-AI/monorepo/pull/9", from_handle=f"dispatch:{ROOT_DISPATCH}")])
    watching = watch.Watcher(rooted, offset=state.events_end(rooted), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    assert (shown, calls, watching.agent_gaps) == ([ROOT_DISPATCH], [], {"j1": None})
    job = state.load(rooted)["jobs"]["j1"]
    assert {field: job[field] for field in ("agent", "dispatch", "where", "route", "run", "task", "terminal")} == {
        "agent": None,
        "dispatch": ROOT_DISPATCH,
        "where": "remote",
        "route": "local",
        "run": RUN,
        "task": "task_root",
        "terminal": "term_root",
    }
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ["JOB j1 o1 WAITING CI on PR #9"]
    assert calls == [RUN]


def test_an_orca_dispatch_set_as_a_jobs_agent_records_a_lane_on_this_mac(run: Run, demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    run("job", "set", "j1", "--agent", ROOT_DISPATCH)
    saved = state.stored(demo)["jobs"]["j1"]
    assert {field: saved[field] for field in ("agent", "dispatch", "where", "route", "workspace")} == {
        "agent": None,
        "dispatch": ROOT_DISPATCH,
        "where": "remote",
        "route": "local",
        "workspace": None,
    }


def test_an_orca_dispatch_on_a_sprite_set_as_a_jobs_agent_records_its_sprite_and_counts_no_builder_on_this_mac(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    run("job", "set", "j1", "--agent", SPRITE_DISPATCH)
    data = state.load(demo)
    assert {field: data["jobs"]["j1"][field] for field in ("agent", *SPRITE_LANE)} == {"agent": None, **SPRITE_LANE}
    assert place.local_builders(data, state.now()) == {}


def kept_attempt(drive: str, attempt: str, dispatch: str) -> None:
    """The attempt the launcher keeps for a brief whose worker started: worker-start exited 0 with a ready receipt."""
    kept = state.drive_dir(drive) / "jobs" / f"{attempt}.worker"
    kept.mkdir(parents=True)
    (kept / "worker-start.status").write_text("0\n")
    (kept / "worker-start.json").write_text(json.dumps({"ok": True, "error": None, "result": {"state": "ready", "runId": RUN, "taskId": "task_sprite", "dispatchId": dispatch}}))


def test_a_launch_whose_worker_started_unrecorded_is_recorded_from_its_kept_receipt_by_the_next_launch_which_starts_nothing(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    kept_attempt(demo, "j1", SPRITE_DISPATCH)
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER)
    monkeypatch.setattr(orca, "task_title", lambda run_id, task: {(RUN, "task_sprite"): "demo-j1"}[run_id, task])
    monkeypatch.setattr(orca, "new_run", lambda objective: pytest.fail("a worker already started gets no second Run"))
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a worker already started is not launched again"))
    assert run("job", "launch", "j1") == (0, f"j1 started on Sprite pool-sprite-one as dispatch {SPRITE_DISPATCH}")
    job = state.load(demo)["jobs"]["j1"]
    assert {field: job[field] for field in ("state", "run", "task", "terminal", "lane", *SPRITE_LANE)} == {
        "state": "running",
        "run": RUN,
        "task": "task_sprite",
        "terminal": "term_sprite",
        "lane": "demo-j1",
        **SPRITE_LANE,
    }


def test_a_relaunch_whose_worker_started_unrecorded_is_recorded_from_its_kept_receipt_by_the_next_resume_which_starts_nothing(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = state.drive_dir(remote) / "jobs" / "j1.md"
    first.parent.mkdir(exist_ok=True)
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", environment="sprite-old")
        state.lane_died(data["jobs"]["j1"], DISPATCH, "its worker exited", T0)
    kept_attempt(remote, "j1-2", SPRITE_DISPATCH)
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER if dispatch == SPRITE_DISPATCH else dead())
    monkeypatch.setattr(orca, "task_title", lambda run_id, task: {(RUN, "task_sprite"): "demo-j1-2"}[run_id, task])
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "new_run", lambda objective: pytest.fail("a worker already started gets no second Run"))
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a worker already started is not launched again"))
    code, out = run("job", "resume", "j1", "go")
    assert (code, f"relaunched on Sprite pool-sprite-one as dispatch {SPRITE_DISPATCH}" in out) == (0, True)
    job = state.load(remote)["jobs"]["j1"]
    assert {field: job[field] for field in ("state", "run", "dispatch", "lane", "workspace")} == {
        "state": "running",
        "run": RUN,
        "dispatch": SPRITE_DISPATCH,
        "lane": "demo-j1-2",
        "workspace": "pool-sprite-one",
    }
    assert [death["dispatch"] for death in job["deaths"]] == [DISPATCH]


def test_the_watch_files_a_lane_recorded_on_this_mac_on_the_sprite_its_worker_show_names(run: Run, demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state.local_lane(SPRITE_DISPATCH), run=RUN, task="task_sprite", terminal="term_sprite")
        data["run"] = DRIVE_RUN
    assert list(place.local_builders(state.load(demo), state.now())) == ["j1"]
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER)
    watch.run_pass(watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0), T0)
    data = state.load(demo)
    assert {field: data["jobs"]["j1"][field] for field in SPRITE_LANE} == SPRITE_LANE
    assert place.local_builders(data, state.now()) == {}


def test_a_local_launch_runs_the_launcher_on_its_local_route_in_the_checkouts_orca_repo_and_reads_no_prepared_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brief = tmp_path / "j1.md"
    brief.write_text("brief")
    monkeypatch.setattr(orca, "tool", lambda name: name)
    monkeypatch.setattr(orca, "main_checkout", lambda worktree: Path("/code/monorepo"))
    asked = []
    monkeypatch.setattr(orca, "request", lambda *args: asked.append(args) or {"repo": {"id": "repo-local"}})
    receipt = {"state": "ready", "task": "task_1", "dispatch": DISPATCH, "terminal": "term_root", "worktree": "/work/demo-j1-1a2b3c4d"}
    ran = {}

    def launcher(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        ran.update(argv=argv, env=kwargs["env"], cwd=kwargs["cwd"])
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt) + "\n", stderr="")

    monkeypatch.setattr(orca.subprocess, "run", launcher)
    launched = orca.launch("demo", "demo-j1", brief, tmp_path, RUN, state.LOCAL_ROUTE, None)
    assert launched == {**receipt, "lane": "demo-j1", "workspace": None, "environment": None}
    assert (ran["argv"], ran["cwd"]) == (["orca-worker-launch.sh", "demo-j1", orca.MODEL, orca.EFFORT, str(brief)], tmp_path)
    assert {key: ran["env"][key] for key in ("WORKER_LOCAL", "WORKER_REPO", "WORKER_RUN", "WORKER_ENV")} == {
        "WORKER_LOCAL": "1",
        "WORKER_REPO": "repo-local",
        "WORKER_RUN": RUN,
        "WORKER_ENV": orca.LANE_ENV,
    }
    assert asked == [("repo", "show", "--repo", "path:/code/monorepo")]
    assert "WORKER_TOOLS" not in ran["env"]


def test_a_sprite_launch_gives_the_sprite_the_drive_that_asks_each_verb_of_this_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    brief = tmp_path / "j1.md"
    brief.write_text("brief")
    monkeypatch.setattr(orca, "tool", lambda name: name)
    ran = {}

    def launcher(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        ran.update(env=kwargs["env"])
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no Sprite\n")

    monkeypatch.setattr(orca.subprocess, "run", launcher)
    with pytest.raises(orca.OrcaError):
        orca.launch("demo", "demo-j1", brief, tmp_path, RUN, None, None)
    assert ran["env"]["WORKER_TOOLS"] == f"drive={orca.PLUGIN / 'lib' / 'sprite' / 'drive'}"
    assert (orca.PLUGIN / "lib" / "sprite" / "drive").stat().st_mode & 0o111
    assert "WORKER_LOCAL" not in ran["env"]


def test_placing_tools_on_a_sprite_runs_the_orca_step_on_its_cc_remote_task_with_the_drive_a_launch_gives_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orca, "tool", lambda name: name)
    monkeypatch.setattr(orca, "CC_REMOTE_TASKS", tmp_path)
    ran = []

    def step(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        ran.append((argv, kwargs["env"]["WORKER_TOOLS"]))
        failed = len(ran) > 1
        return subprocess.CompletedProcess(argv, int(failed), stdout="drive placed\n", stderr="a login shell runs drive from nothing\n" if failed else "")

    monkeypatch.setattr(orca.subprocess, "run", step)
    PLACE_TOOLS("pool-one")
    assert ran == [(["orca-sprite-tools.sh", str(tmp_path / "pool-one.json")], f"drive={orca.PLUGIN / 'lib' / 'sprite' / 'drive'}")]
    with pytest.raises(orca.Unstarted, match="^orca-sprite-tools.sh exited 1 on pool-one: a login shell runs drive from nothing$"):
        PLACE_TOOLS("pool-one")


def test_placing_a_brief_on_a_sprite_sends_it_through_ssh_stdin_and_returns_the_path_only_when_the_sprite_kept_those_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orca, "CC_REMOTE_TASKS", tmp_path)
    (tmp_path / "pool-one.json").write_text(json.dumps({"forward": {"host": "pool-one", "config": "/ssh/pool-one.ssh"}}))
    text = b"Remote lane: drive demo, job j1.\n" * 4096
    path = "/home/sprite/.local/state/long-running/briefs/demo-j1-2.md"
    kept = [hashlib.sha256(text).hexdigest(), hashlib.sha256(b"torn").hexdigest()]
    ran = []

    def ssh(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        ran.append((argv[:3], argv[-2], kwargs["input"]))
        return subprocess.CompletedProcess(argv, 0, stdout=f"{kept[len(ran) - 1]}  {path}\n".encode(), stderr=b"")

    monkeypatch.setattr(orca.subprocess, "run", ssh)
    assert orca.place_brief("pool-one", "demo-j1-2", text) == path
    assert ran == [(["ssh", "-F", "/ssh/pool-one.ssh"], "pool-one", text)]
    with pytest.raises(orca.Unfit, match="^writing .local/state/long-running/briefs/demo-j1-2.md onto pool-one exited 0: "):
        orca.place_brief("pool-one", "demo-j1-2", text)


def test_a_long_answer_to_a_sprite_lane_goes_as_the_file_it_was_kept_in_there_after_its_exit_line_and_a_short_or_mac_one_goes_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    placed = []

    def put(workspace: str, folder: str, name: str, text: bytes) -> str:
        placed.append((workspace, folder, name, text))
        if workspace == "pool-gone":
            raise orca.Unfit("no Sprite")
        return f"/home/sprite/{folder}/{name}"

    monkeypatch.setattr(orca, "put_on_sprite", put)
    sprite = {"route": None, "workspace": "pool-one"}
    long = "drive exited 3\n" + "x" * orca.ANSWER_BYTES
    digest = hashlib.sha256(long.encode()).hexdigest()
    assert orca.sprite_answer(long, sprite, "msg_1") == (
        "drive exited 3\ndrive answer kept on this Sprite: /home/sprite/.local/state/long-running/answers/msg_1.txt"
        f" ({len(long)} bytes, SHA-256 {digest}); read it in full\n"
    )
    assert placed == [("pool-one", ".local/state/long-running/answers", "msg_1.txt", long.encode())]
    assert orca.sprite_answer("LOG x: " + "y" * orca.ANSWER_BYTES, sprite, "msg_2").startswith("drive answer kept on this Sprite: ")
    assert orca.sprite_answer("drive exited 0\nshort", sprite, "msg_3") == "drive exited 0\nshort"
    assert orca.sprite_answer(long, {"route": state.LOCAL_ROUTE, "workspace": None}, "msg_4") == long
    assert orca.sprite_answer(long, {"route": None, "workspace": "pool-gone"}, "msg_5") == long
    assert len(placed) == 3


def test_launch_local_records_a_lane_on_this_mac_that_reads_as_starting_while_it_launches(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None) -> dict:
        watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
        staged(watch.agent_lines, watching, state.now())
        launched.update(route=route, brief=brief.read_text(), gaps=watching.agent_gaps)
        return {"dispatch": DISPATCH, "task": "task_1", "terminal": "term_root", "lane": lane, "workspace": None, "environment": None}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "launch", "j1", "brief.md", "--local") == (0, f"j1 started on this Mac as dispatch {DISPATCH}")
    assert (launched["route"], launched["gaps"]) == (state.LOCAL_ROUTE, {"j1": None})
    assert launched["brief"].startswith("Remote lane: drive demo, job j1. You are the long-running builder below, running as an Orca worker on this Mac")
    job = state.load(demo)["jobs"]["j1"]
    assert {field: job[field] for field in ("where", "route", "dispatch", "run", "task", "terminal", "lane", "workspace", "environment")} == {
        "where": "remote",
        "route": "local",
        "dispatch": DISPATCH,
        "run": RUN,
        "task": "task_1",
        "terminal": "term_root",
        "lane": "demo-j1",
        "workspace": None,
        "environment": None,
    }


@pytest.mark.parametrize(("ago", "gap"), [(timedelta(minutes=29), None), (timedelta(minutes=31), "j1 never started on its Sprite")])
def test_a_job_with_no_dispatch_reads_as_starting_until_a_launch_would_have_finished(
    run: Run, demo: str, ago: timedelta, gap: str | None
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["launching"] = state.stamp(T0 - ago)
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    staged(watch.agent_lines, watching, T0)
    assert watching.agent_gaps == {"j1": gap}


def test_a_job_added_or_set_waiting_on_a_pr_or_a_release_waits_unlaunched_until_free_names_its_launch(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "Forge-AI/monorepo#7"
    shipping = f"release:api:{key}"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "the PR it builds on", "--remote")
    with state.edit(demo) as data:
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1")
    code, out = run("job", "add", "builder", "--order", "o1", "--goal", "after it ships", "--remote", "--waits", key, "--waits", shipping)
    assert (code, out.splitlines()[1]) == (0, f"j2 waits on {key}, {shipping}: start no agent for it until the watch's FREE line says its last wait ended")
    run("job", "add", "builder", "--order", "o1", "--goal", "given its wait after", "--remote")
    run("job", "set", "j3", "--waits", key)
    jobs = state.load(demo)["jobs"]
    assert [(jobs[job_id]["state"], jobs[job_id]["waits"]) for job_id in ("j2", "j3")] == [("waiting", [key, shipping]), ("waiting", [key])]
    assert cli.main(["job", "launch", "j2"]) == 2
    assert f"j2 waits on {key}, {shipping}: launch it on the watch's FREE line" in capsys.readouterr().err
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    staged(watch.agent_lines, watching, T0)
    assert watching.agent_gaps == {"j1": "j1 never started on its Sprite"}
    with state.edit(demo) as data:
        data["prs"][key].update(state="merged", merged_sha="abc123")
    shipped = f"release releases/2026-11-09/1 ships {key} on api"
    monkeypatch.setattr(watch.view, "shipped_by", lambda drive, wait: shipped)
    assert watch.waits_lines(watching, T0) == [
        f"FREE j2 o1: {key} answered or done, {shipped}, nothing else holds it, not started yet: drive job launch j2 starts it",
        f"FREE j3 o1: {key} answered or done, nothing else holds it, not started yet: drive job launch j3 starts it",
    ]


@pytest.mark.parametrize(
    "recorded",
    [{}, {"run": RUN, "task": "task_root", "terminal": "term_root"}, {"run": "run_earlier", "task": "task_earlier", "terminal": "term_earlier"}],
)
def test_resume_to_a_local_lane_that_finished_starts_the_next_worker_in_its_worktree_terminal_and_run_as_orca_shows_them(
    run: Run, rooted: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: dict[str, str]
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(rooted, "j1", "Order (his words): the tooling root", state.LOCAL_ROUTE))
    with state.edit(rooted) as data:
        data["jobs"]["j1"].update(recorded, lane="demo-j1", brief=str(first), ask=None, report=None)
    monkeypatch.setattr(orca, "worker", lambda dispatch: {**ROOT_WORKER, "worker": {**ROOT_WORKER["worker"], "state": "succeeded"}})
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a finished worker whose terminal Orca holds is never relaunched elsewhere"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "place_tools", lambda workspace: pytest.fail("a lane on this Mac runs this Mac's drive, so no tools are placed"))
    started = {}

    def worker_start(drive: str, run_id: str, argv: list[str]) -> subprocess.CompletedProcess:
        started.update(argv=argv)
        receipt = {"ok": True, "result": {"state": "ready", "runId": run_id, "taskId": "task_2", "dispatchId": "ctx_lane0002"}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt), stderr="")

    monkeypatch.setattr(orca, "bound_command", worker_start)
    assert run("job", "resume", "j1", "The review: fix the one finding") == (
        0,
        "j1: the message went to the job's next worker, started in its terminal on this Mac as dispatch ctx_lane0002 in a"
        " handoff carrying its brief, its branch, its last line, its report and its PRs; its lane on this Mac had finished"
        " (its worker succeeded, and Orca sends nothing to a finished worker on this Mac)",
    )
    argv = started["argv"]
    options = {argv[index]: argv[index + 1] for index in range(3, len(argv) - 2, 2)}
    kept = state.drive_dir(rooted) / "jobs" / "j1-2.md"
    assert options == {
        "--run": RUN,
        "--worktree": "path:/work/root-tooling",
        "--terminal": "term_root",
        "--task-title": "demo-j1-2",
        "--spec": f"Lane 'demo-j1-2'. Your full brief is the file {kept}: read it in full with the Read tool before anything else and execute it exactly.",
        "--timeout-ms": "600000",
    }
    assert "You continue the job in its terminal and checkout on this Mac, as it left them." in orca.brief_of(kept.read_text())
    job = state.load(rooted)["jobs"]["j1"]
    assert (job["run"], job["dispatch"], job["route"], job["terminal"], job["workspace"], job["state"]) == (RUN, "ctx_lane0002", "local", "term_root", None, "running")
    assert [(ended["dispatch"], ended["workspace"]) for ended in job["deaths"]] == [(ROOT_DISPATCH, None)]


def test_the_watch_says_a_dead_local_lane_died_on_this_mac(rooted: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    with state.edit(rooted) as data:
        data["jobs"]["j1"].update(run=RUN, task="task_root", terminal="term_root")
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    watching = watch.Watcher(rooted, offset=state.events_end(rooted), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on this Mac: its worker exited, while it worked on "root-tooling: dispatch ctx_ab821100f80b". The watch '
        "ran drive job resume j1, which relaunches it in a fresh worktree on this Mac with a handoff, its output in the drive's relaunches.log"
    ]
