import hashlib
import io
import itertools
import json
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import ANY

import pytest
from conftest import DRIVE_TERMINAL, LIVE_WORKER, REAL_DRIVE_TERMINAL, REAL_KEEP_LOGS, REAL_KEEP_TRANSCRIPTS, REAL_SAVED_BRANCHES, REAL_SESSION_OF, RESEARCH, Drawing, Jev, cloned, git_in, staged
from drive import artifacts, cli, github, orca, place, reads, render, state, watch

pytestmark = pytest.mark.usefixtures("ccx_worktrees")

Run = Callable[..., tuple[int, str]]
T0 = state.parse_time("2026-11-09T07:30:00-08:00")
RUN = "run_drive0001"
FRESH_RUN = "run_relaunch1"
DISPATCH = "ctx_lane0001"
J2914_BUILT = "BUILT sprite-volume-leaves-image-agents-and-mise, waits on c096bde8"
J2914_REPORT = f"{J2914_BUILT}\nNATIVE: none: he never sees it\nUNPROVEN: the screenshot half\n"
J2914_WORKER = "ctx_1f4d4c929c48"
J2914_NEXT_WORKER = "ctx_0f1e1c8feccb"
PLACE_TOOLS = orca.place_tools
LAUNCH = orca.launch
REPREPARED = "it continues in the checkout it left on pool-one, which cc-remote prepared again with the tools and keys a fresh Sprite gets"
RUNTIME_LISTENING = orca.runtime_listening
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
UNTOOLED = (
    "the Sprite did not take the released ccx and cc-notes, the tools WORKER_TOOLS names, or the wasm toolchain: gh could not read the latest"
    " yasyf/cc-context release, so no tool was installed; the prepared worker stays idle and unattached, {attempt} keeps tools.err, and launching"
    " this brief again reruns the tool check on that Sprite"
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
Simplest: On the primary, beside the queries it serves
Holds: none: no option changes code any job builds
One copy took 40 minutes on the primary.
"""

SECOND_ASK = ASK_IN_SHAPE.replace("ASK Where should the nightly store copy run?", "ASK Which worker should the nightly store copy run on?")


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
    return demo


@pytest.fixture
def rooted(run: Run, demo: str) -> str:
    """A root started by hand with the orca skill's local route and recorded with drive job set --agent <its dispatch>, as drives saved it before local lanes."""
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(agent=ROOT_DISPATCH, where="local", said="root-tooling: dispatch ctx_ab821100f80b")
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)

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

    def no_runtime(drive: str, objective: str) -> str:
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
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
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
    assert sent == [("send", "--to", f"dispatch:{SPRITE_DISPATCH}", "--run", FRESH_RUN, "--subject", "From the coordinator", "--body", "PR checks failed", "--retry-request", ANY)]


def test_launch_composes_the_vm_brief_and_records_the_lane(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Order (his words): ship it\nDrive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    objectives = []
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: objectives.append(objective) or RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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


def test_a_first_launch_marks_its_job_sent_the_answer_its_brief_carries_and_carries_the_one_it_lacks_under_his_answers_since(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    heard, unsaid = "keep the one snapshot file", "move the copy to the pool"
    (tmp_path / "brief.md").write_text(f"Order (his words): ship it\nDrive: demo, order o1, job j1\n\nHis answer, verbatim:\n\n{heard}\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    link = "https://acme.slack.com/archives/D123/p1794300000000300"
    with state.edit(demo) as data:
        for answer_id, words in (("a1", heard), ("a2", unsaid)):
            data["answers"][answer_id] = {"words": words, "link": link, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {"j1": None}}
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields})
    assert run("job", "launch", "j1", "brief.md")[0] == 0
    answers = state.load(demo)["answers"]
    assert (answers["a1"]["jobs"], answers["a2"]["jobs"]) == ({"j1": {"mark": "sent"}}, {"j1": {"mark": "sent"}})
    assert orca.brief_of((state.drive_dir(demo) / "jobs" / "j1.md").read_text()).endswith(f'{heard}\nHis answers since:\n- "{unsaid}" ({link})')


def test_launch_runs_from_the_drives_own_worktree_made_once_with_ccx_and_moved_with_one_fetch_to_the_default_branchs_head_never_the_coordinators_working_tree(
    run: Run, demo: str, tmp_path: Path, ccx_worktrees: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    for goal in ("ship it", "ship it again"):
        run("job", "add", "builder", "--order", "o1", "--goal", goal, "--remote")
    state_folder = state.drive_dir(demo)
    for job_id in ("j1", "j2"):
        (state_folder / f"{job_id}-brief.md").write_text(f"Drive: demo, order o1, job {job_id}\n")
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
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
        folder / "jobs" / "j3.brief.md": "Order (his words): list it\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (folder / "scratch" / "j4").mkdir()
    text = (
        "Order (his words): ship it\nResearched: j1\nPlan: Design j2, whole: reports/j2.md (drive job report j2)\n"
        f"Done when: j3 asked for `{folder}/asks/j2-0c1d2e3f.md` and repro/j1.sh, then ~/{folder.relative_to(folder.parents[1])}/scratch/j1/notes.md and jobs/j3.brief.md.\n"
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
    assert "# Drive file jobs/j3.brief.md, which your brief names\n\nOrder (his words): list it\n" in composed
    carried = composed.split(orca.BRIEF_HEADING)[0]
    assert (carried.count("\n# Report of j2"), carried.count("\n# Drive file"), "j9-1a2b3c4d" in carried, "a job the brief only lists" in carried) == (1, 4, False, False)
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
        "drive: j1: refused, not launched: its brief names Drive file asks/j1290-702505b0.md, which the drive keeps no text of,"
        " so its lane would stop to ask for each; launch it on a brief file that leaves each out: drive job launch j1 <file>\n"
    )
    assert state.load(demo)["jobs"]["j1"] == before


def test_a_builder_added_waiting_on_his_card_with_no_lane_starts_on_his_tap_from_its_drive_written_brief_and_hears_his_answer_from_that_launch_as_j3074_could_not(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    words, link, card = "One snapshot file per run", "https://acme.slack.com/archives/D123/p1794300000000700", "https://acme.slack.com/archives/D123/p1794300000000600"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "reader", "--order", "o1", "--goal", "read it")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it", "--remote")
    with state.edit(demo) as data:
        closed = state.new_close("answered", words, link, answer="a1")
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Which layout?", state.stamp(), card=card, closed=closed) | {"waits": "demo:j1-7"})
        data["jobs"]["j2"].update(state="waiting", waits=["demo:j1-7"])
        data["answers"]["a1"] = {"words": words, "link": link, "at": state.stamp(), "said": state.stamp(), "cards": {}, "jobs": {"j2": None}}
    monkeypatch.setattr(watch.view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [], "closed": []})
    watch.view.refresh_cards(demo)
    assert run("answer", "a1") == (0, "his answer a1 recorded: 0 standing, 0 superseded, 0 reopened by it")
    assert run("job", "brief", "j2", "--serves", "his order", "--done-when", "merged", "--where", "a new branch", "--plan", "finding 1 is j1's")[0] == 0
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in ("task", "terminal", "workspace", "environment", "lane")} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j2") == (0, f"j2 started on Sprite workspace-1 as dispatch {DISPATCH}")
    data = state.load(demo)
    assert (data["answers"]["a1"]["jobs"], data["jobs"]["j2"]["waits"]) == ({"j2": {"mark": "sent"}}, [])
    assert f'\nHis answers since:\n- "{words}" ({link})\n' in (state.drive_dir(demo) / "jobs" / "j2.md").read_text()


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j1", "brief.md") == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")
    job = state.load(demo)["jobs"]["j1"]
    assert (job["where"], job["dispatch"]) == ("remote", DISPATCH)


def test_a_readers_return_briefs_the_builder_waiting_on_it_so_its_free_line_launches_it_with_no_hand_brief(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, demo)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run page", "--kind", "build")
    run("job", "add", "reader", "--order", "o1", "--goal", "research: the run page")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "show the run's log", "--kind", "build")
    run("job", "add", "reader", "--order", "o2", "--goal", "research: the run's log")
    monkeypatch.setattr("sys.stdin", io.StringIO(RESEARCH))
    code, out = run("job", "report", "j1", "-")
    jobs = state.load(demo)["jobs"]
    assert (code, out.splitlines()[1], jobs["j4"].get("brief")) == (0, f"j2: Read your brief first: {jobs['j2']['brief']}", None)
    brief = Path(jobs["j2"]["brief"]).read_text()
    assert ("Serves: his order\n" in brief, "Researched: j1\n" in brief, "Done when:" in brief) == (True, True, False)
    assert watch.waits_lines(watch.Watcher(demo, offset=state.events_end(demo)), T0) == [f"FREE j2 o1: j1 answered or done, nothing else holds it; Read your brief first: {jobs['j2']['brief']}"]
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j2") == (0, f"j2 started on Sprite workspace-1 as dispatch {DISPATCH}")
    assert run("job", "brief", "j2", "--plan", "the run page section")[0] == 0
    assert ("Serves: his order\n" in (rewritten := Path(jobs["j2"]["brief"]).read_text()), "Plan: the run page section\n" in rewritten) == (True, True)


def test_a_launch_hands_its_receipt_to_the_newer_install_that_saved_meanwhile(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    handed = []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION

    def launch(*args: object) -> dict:
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
    monkeypatch.setattr(cli.install.subprocess, "run", newer)
    assert run("job", "launch", "j1", "brief.md") == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")
    receipt = state.drive_dir(demo) / "jobs" / f"j1.{DISPATCH}.json"
    assert (handed, receipt.exists()) == ([["job", "started", "j1", str(receipt), "--drive", demo, "--json"]], False)
    job = state.load(demo)["jobs"]["j1"]
    assert (job["where"], job["dispatch"], job["run"], job["state"]) == ("remote", DISPATCH, RUN, "running")


@pytest.mark.parametrize(
    ("flag", "named"),
    [
        ("--mac", "drive break-glass j1 --stack sandsql/plat-usw2-prod --reason stuck -- aws s3 ls"),
        ("--mac", "tools/ci mac-box prove --commit f3163cd03c48"),
        ("--mac", 'tools/ci db-read "select count(*) from iris_sessions"'),
        ("--mac", "read the 500s in Datadog APM"),
        ("--mac", "drive install long-running@skills --sha 7765bd2"),
        ("--mac", "record the drive's dashboard walking each part"),
        ("--release", "tools/ci release start api --env prod --order 680721"),
    ],
)
def test_launch_starts_a_brief_declaring_a_step_only_this_mac_runs_only_on_this_mac(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jev: Jev, flag: str, named: str
) -> None:
    cloned(tmp_path, demo)
    jev.picks("680721")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    run("job", "brief", "j1", "--serves", "his order", "--done-when", "merged", "--where", "yasyf/cc-skills main", flag, named)
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert cli.main(["job", "launch", "j1"]) == 2
    assert capsys.readouterr().err == (
        f"drive: j1: refused, not launched: its brief's Mac: or Release: line declares {named}, a step only this Mac runs: start it here,"
        " with the Agent tool at any load or with --local\n"
    )
    assert state.load(demo)["jobs"]["j1"].get("launching") is None
    assert run("job", "launch", "j1", "--local") == (0, f"j1 started on this Mac as dispatch {DISPATCH}")
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
        "Plan: change 1 of the retry design, read whole first\n"
        "Incident: Every release a drive job starts starts the regular way, `tools/ci release start <target> --from <sha>`\n"
        "Transcripts:\n- j2333 builder: read the 500s in Datadog APM, then tools/ci mac-box prove\n"
        "Drive: demo, order o1, job j1\n"
    )
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the retries")
    run("job", "add", "builder", "--order", "o1", "--goal", "retry forks the newest saved attempt")
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
    monkeypatch.setattr(orca, "launch", lambda *args: {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH})
    assert run("job", "launch", "j1", "brief.md") == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")


@pytest.mark.parametrize(
    ("refused", "declared"),
    [
        ("Incident: Every release a drive job starts starts the regular way, `tools/ci release start <target> --from <sha>`, whose `Release-With` trailer", None),
        ("Would he ask: that every env value a Claude session has (Datadog, GitHub, AWS profile) reaches the launchd watch the same way", None),
        ("Goal: build j2624's fix: one drive check asks tools/ci release select --pr for a release wait's service", None),
        ("Order (his words): Drive tooling: one long command of a break-glass job on an older install held every newer verb", None),
        ("Order (his words): I thoguth we fixed OOMs? Shared from Datadog in <#C0822AHFY3G>: Executor OOM Run Crash Count (16Gi)", None),
        (
            "Order (his words): Drive tooling: a Sprite cannot reach a Mac box or run ci db-read\n"
            "Done when: j2412.py exits 0 on the installed long-running release, showing a brief naming ci mac-box or ci db-read placed on this Mac",
            None,
        ),
        ("Order (his words): Drive tooling: drive job launch refused a builder because its order text names break-glass as the cause of a stall", None),
        ("Handoff: Found by job j2806 on a test Mac during a 48-subagent fan-out, verbatim from its record", None),
        (
            "Handoff: On his Install now, run drive install captain-hook@captain-hook and drive job prove for j2206's PRs",
            "drive install captain-hook@captain-hook on his Install now",
        ),
        ("Goal: build reader j2939's fold so no drive session, handlers included, can read Datadog with pup", None),
        ("Goal: j2266 still waits on release:api of Forge-AI/monorepo#55152, which tools/ci release live shows live", None),
        ("Handoff: fix forward on main. INSTALL: drive install once the fix merges.", "drive install of the long-running release holding the fix"),
        ("Plan: His words, verbatim, typed on the drive's dashboard under the card", None),
        ("Handoff: Rebase onto main, resolve, push, merge. INSTALL: drive install of the release.", "drive install of the long-running release holding the merge"),
        (
            "Goal: prove Forge-AI/monorepo#55444 on ci mac-box, the test Mac's screen recording, once its release is out",
            "tools/ci mac-box record of Forge-AI/monorepo#55444's desktop build",
        ),
        ("Goal: immediate fix: the release start hook passes j2666's clock release on his picks", None),
        (
            "Goal: change 3 of j2152's design: a Datadog alert on SandSQL read breaker trips and the SandSQL CPU monitor's message reworded to today's read gate\n"
            "Done when: one PR on dev: a Datadog monitor alerts on read breaker trips, and monitor 344179363's message no longer says the per-team share caps streams\n"
            "Where: Forge-AI/monorepo, base dev; the Datadog monitors' IaC beside monitor 344179363\n"
            "Runs as: none: an IaC and code change graded by CI",
            None,
        ),
    ],
    ids=["j2776", "j2773", "j2678", "j2729", "j2821", "j2775", "j2845", "j2885", "j2941", "j2940", "j3003", "j3038", "j3065", "j3100", "j3104", "j3120", "j3222"],
)
def test_each_refused_mac_only_launch_replayed_stays_here_only_when_its_brief_declares_a_mac_line(refused: str, declared: str | None) -> None:
    written = f"{refused}\nServes: his order\nDrive: durable-agents, order o1, job j1\n"
    declaring = written.replace("Drive:", f"Mac: {declared}\nDrive:") if declared else written
    assert (place.mac_step(written), place.mac_step(declaring)) == (None, declared)


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
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
    launched = agent_on_this_mac(run, demo, tmp_path, monkeypatch, STOPPED, timedelta(seconds=13))
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


def test_a_job_whose_stopped_agent_has_a_brief_declaring_a_step_only_this_mac_runs_takes_over_on_this_mac(run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent_on_this_mac(run, demo, tmp_path, monkeypatch, STOPPED, timedelta(seconds=45))
    brief = Path(state.load(demo)["jobs"]["j1"]["brief"])
    brief.write_text(brief.read_text() + "Mac: proves the desktop change on a Mac box with ci mac-box prove\n")
    stop = f'its last response ended on refusal at {watch.when(STOPPED["timestamp"])}: "{render.cut(STOPPED_TEXT, 100)}"'
    assert run("job", "launch", "j1") == (0, f"j1 started on this Mac as dispatch dispatch-2, taking over from its agent a1 on this Mac, which takes no message: {stop}")
    job = state.load(demo)["jobs"]["j1"]
    assert (job["agent"], job["where"], job["route"], job["dispatch"]) == (None, "remote", state.LOCAL_ROUTE, "dispatch-2")


ANSWERED = {"type": "assistant", "timestamp": "2026-11-12T20:44:30.000Z", "message": {"model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "j1's agent stopped."}]}}
FORBIDDEN_TEXT = "API Error: 403 Attention Required! | Cloudflare (error type authentication_failed). Please run /login"
FORBIDDEN = {"type": "assistant", "timestamp": "2026-11-12T20:44:30.000Z", "message": {"model": "<synthetic>", "stop_reason": "stop_sequence", "content": [{"type": "text", "text": FORBIDDEN_TEXT}]}}
EARLIER = {**ANSWERED, "timestamp": "2026-11-12T20:43:00.000Z"}


def stopped_agent_watched(run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: list[dict]) -> watch.Watcher:
    """A running builder whose agent a1 on this Mac stopped on an API error 13 seconds ago, as j577's did, beside the transcript of the session holding it, which ends on session."""
    agent_on_this_mac(run, demo, tmp_path, monkeypatch, STOPPED, timedelta(seconds=13))
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(state="running", waits=[], said="PUSHED https://github.com/Forge-AI/monorepo/pull/54870")
    data = state.load(demo)
    watch.session_transcript(watch.session_of(data["jobs"]["j1"], data)).write_text("".join(json.dumps(row) + "\n" for row in session))
    return watch.Watcher(demo, offset=state.events_end(demo))


def lane_lines(watching: watch.Watcher, moment: datetime) -> list[str]:
    return [line for line in watch.run_pass(watching, moment) if line.startswith("LANE")]


def test_the_watch_hands_a_job_whose_agent_on_this_mac_stopped_on_an_api_error_to_a_fresh_worker_that_pass_once(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relaunches: list[str]
) -> None:
    watching = stopped_agent_watched(run, demo, tmp_path, monkeypatch, [STOPPED, ANSWERED])
    stop = f'its last response ended on refusal at {watch.when(STOPPED["timestamp"])}: "{render.cut(STOPPED_TEXT, 100)}"'
    assert lane_lines(watching, T0) == [
        f'LANE j1 o1 died as agent a1 on this Mac: {stop}, while it worked on "PUSHED https://github.com/Forge-AI/monorepo/pull/54870". The watch ran'
        " drive job resume j1, which hands the job to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs, with a handoff,"
        " its output in the drive's relaunches.log"
    ]
    assert lane_lines(watching, T0 + timedelta(minutes=1)) == []
    assert relaunches == ["j1"]
    job = state.load(demo)["jobs"]["j1"]
    assert [(death["agent"], death["dispatch"], death["relaunch"]) for death in job["deaths"]] == [("a1", None, {"pid": os.getpid(), "at": state.stamp(T0)})]
    assert "lane dead since" in render.job_line("j1", job)


@pytest.mark.parametrize("session", [[STOPPED, FORBIDDEN], [EARLIER]], ids=["refused-since", "nothing-since"])
def test_the_watch_holds_a_stopped_agents_job_until_the_session_holding_it_has_a_response_answered(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relaunches: list[str], session: list[dict]
) -> None:
    watching = stopped_agent_watched(run, demo, tmp_path, monkeypatch, session)
    assert (lane_lines(watching, T0), relaunches, state.load(demo)["jobs"]["j1"].get("deaths", [])) == ([], [], [])


@pytest.mark.parametrize(("last", "quiet"), [(ENDED_TURN, timedelta(minutes=10)), (CALLED, timedelta(minutes=10))])
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
        orca.launch("demo", "demo-j1", brief, tmp_path, RUN, None, None, None)
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
        ("reply", "--id", "msg_q1", "--run", RUN, "--body", "TAP Approve", "--retry-request", ANY),
        ("check", "--run", RUN),
        ("send", "--to", f"dispatch:{DISPATCH}", "--run", RUN, "--subject", "From the coordinator", "--body", "PR checks failed", "--retry-request", ANY),
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
        ("reply", "--id", "msg_q1", "--run", RUN, "--body", "PR checks failed", "--retry-request", ANY),
        ("send", "--to", f"dispatch:{DISPATCH}", "--run", RUN, "--subject", "From the coordinator", "--body", "PR checks failed", "--retry-request", ANY),
    ]
    assert calls[1][-1] != calls[2][-1]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"]) == (None, "running")


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: objectives.append(objective) or FRESH_RUN)
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: read.append(job["workspace"]))

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: next(runs))
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)

    def bind_refused(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.append((brief, brief.read_text(), run_id))
        raise orca.Unstarted(f"no worker started on Sprite pool-two: WORKER_BIND refused before worker-start ran: {NO_RUN}")

    monkeypatch.setattr(orca, "launch", bind_refused)
    assert cli.main(["job", "resume", "j1", "PR #12 merged: prove it live"]) == 2
    assert NO_RUN in capsys.readouterr().err

    def started(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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
    assert orca.brief_of(again).endswith("which you act on now:\n\nPR #12 merged: prove it live\n\nPR #12 merged at abc123: prove it live")
    assert sent == []
    job = state.load(remote)["jobs"]["j1"]
    assert (job["run"], job["dispatch"], [death["dispatch"] for death in job["deaths"]]) == ("run_try2", "dispatch-2", [DISPATCH])


SAVED = (
    "uncommitted changes in /home/sprite/monorepo/.worktrees/conflict-tallies (8 files); commits on no remote on tallies-final, stash,"
    " saved to Forge-AI/monorepo under saved/demo-j1/"
)
SAVED_BRANCHES = ["saved/demo-j1/conflict-tallies at 1a2b3c4d5e", "saved/demo-j1/stash at 2b3c4d5e6f", "saved/demo-j1/tallies-final at 3c4d5e6f70"]
ON_GITHUB = ", ".join(SAVED_BRANCHES)
RESTACK = "commits on no remote on restack, saved to Forge-AI/monorepo under saved/demo-j1/"
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")} | {"workspace": workspace or "workspace-2"}

    monkeypatch.setattr(orca, "launch", fake_launch)
    return launched


def test_resume_saves_what_a_dead_lanes_sprite_holds_to_github_and_hands_the_saved_branches_to_the_next_lane(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved_on_github: dict[str, list[str]]
) -> None:
    cloned(tmp_path, remote)
    saved_on_github["demo-j1"] = SAVED_BRANCHES
    saves = []
    launched = dead_lane_holding(remote, tmp_path, monkeypatch, lambda drive, job_id, job, root: saves.append((drive, job_id, job["workspace"])) or SAVED)
    assert run("job", "resume", "j1", "Build #4412 failed: fix the lint") == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-sleeping was"
        f" dead (Orca no longer holds its Sprite's environment), and what pool-sleeping held on no remote is saved on GitHub as {ON_GITHUB}",
    )
    assert saves == [(remote, "j1", "pool-sleeping")]
    assert (
        f"You take the job over on a fresh Sprite from its pushed branches; what it held on no remote was saved for you on GitHub as {ON_GITHUB}."
        " Fetch each saved branch and carry its work on. Commit and push before any long build."
    ) in orca.brief_of(launched["brief"])
    job = state.load(remote)["jobs"]["j1"]
    assert [(death["dispatch"], death["saved"]) for death in job["deaths"]] == [(DISPATCH, SAVED)]


def test_resume_copies_a_dead_lanes_sprite_logs_and_transcripts_before_it_saves_and_destroys_that_sprite(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept_logs: list[tuple[str, str]], kept_transcripts: list[tuple[str, str]]
) -> None:
    cloned(tmp_path, remote)
    steps = []
    dead_lane_holding(remote, tmp_path, monkeypatch, lambda drive, job_id, job, root: steps.append(("save", list(kept_logs), list(kept_transcripts))) or SAVED)
    monkeypatch.setattr(orca, "holds_sprite", lambda job: True)
    monkeypatch.setattr(orca, "release", lambda drive, job_id, job, root: steps.append(("destroy", list(kept_logs), list(kept_transcripts))) or f"destroyed {job['workspace']}")
    assert run("job", "resume", "j1", "Build #4412 failed: fix the lint")[0] == 0
    kept = [("j1", DISPATCH)]
    assert steps == [("save", kept, kept), ("destroy", kept, kept)]


def test_a_dead_sprites_logs_are_copied_once_and_an_unreadable_sprite_leaves_why(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    (state.drive_dir(remote) / "jobs").mkdir(exist_ok=True)
    read = []
    full = b"== df -k ~\n/dev/loop0 12281444 12208668 72776 100% /home/sprite\n"
    monkeypatch.setattr(orca, "on_sprite", lambda workspace, command, given, seconds: read.append(workspace) or subprocess.CompletedProcess([], 0, full, b""))
    REAL_KEEP_LOGS(remote, "j1", {"workspace": "pool-full"}, DISPATCH)
    REAL_KEEP_LOGS(remote, "j1", {"workspace": "pool-gone"}, DISPATCH)
    assert (read, orca.death_logs(remote, "j1", DISPATCH).read_text()) == (["pool-full"], full.decode())
    refused = b"ssh: connect to host pool-asleep port 22: Connection refused\n"
    monkeypatch.setattr(orca, "on_sprite", lambda workspace, command, given, seconds: subprocess.CompletedProcess([], 255, b"", refused))
    REAL_KEEP_LOGS(remote, "j1", {"workspace": "pool-asleep"}, "ctx_lane0002")
    assert orca.death_logs(remote, "j1", "ctx_lane0002").read_text() == "ssh exited 255: ssh: connect to host pool-asleep port 22: Connection refused\n"


J2504_SESSION = "dadd5ffb-610a-4626-876f-74ac6fba88be"
J2504_DISPATCH = "ctx_7165cdeff304"
J2504_SPRITES = ["pool-ebsaxmwottdlhmsj", "pool-zniamnmp7lpube5f"]


def test_a_dead_lanes_session_is_the_transcript_on_its_sprite_that_holds_its_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects = tmp_path / ".claude" / "projects" / "-home-sprite-monorepo"
    projects.mkdir(parents=True)
    (projects / f"{J2504_SESSION}.jsonl").write_text(f'{{"content":"orca orchestration send --dispatch-id {J2504_DISPATCH}"}}\n')
    (projects / "5d576546-4c87-4a78-94b4-945eb9e49c86.jsonl").write_text('{"content":"orca orchestration send --dispatch-id ctx_other"}\n')
    sprite = {**os.environ, "HOME": str(tmp_path)}
    monkeypatch.setattr(orca, "on_sprite", lambda workspace, command, given, seconds: subprocess.run(["sh", "-c", command], input=given, capture_output=True, env=sprite, check=False))
    assert (REAL_SESSION_OF(J2504_SPRITES[1], J2504_DISPATCH), REAL_SESSION_OF(J2504_SPRITES[1], "ctx_never")) == (J2504_SESSION, None)

    def asleep(workspace: str, command: str, given: bytes, seconds: float) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(command, seconds)

    monkeypatch.setattr(orca, "on_sprite", asleep)
    assert REAL_SESSION_OF(J2504_SPRITES[1], J2504_DISPATCH) is None


def restarted_lane(remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: dict[str, str], workspace: str, refusal: orca.OrcaError | None = None) -> list[tuple]:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace=workspace, dispatch=J2504_DISPATCH, state="running", said="building the redo volumes")
    sessions[workspace] = J2504_SESSION
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a dead lane: {args}"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    monkeypatch.setattr(orca, "holds_sprite", lambda job: True)
    launched: list[tuple] = []

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.append((brief.name, brief.read_text(), workspace, session))
        if session and refusal:
            raise refusal
        return {field: f"{field}-{len(launched) + 1}" for field in ("dispatch", "task", "terminal", "environment", "lane")} | {"workspace": workspace or "workspace-2"}

    monkeypatch.setattr(orca, "launch", fake_launch)
    return launched


@pytest.mark.parametrize("workspace", J2504_SPRITES)
def test_a_lane_whose_sprite_restarted_resumes_its_own_claude_session_there_each_time_with_nothing_saved_or_destroyed(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: dict[str, str], kept_logs: list[tuple[str, str]], workspace: str
) -> None:
    cloned(tmp_path, remote)
    launched = restarted_lane(remote, tmp_path, monkeypatch, sessions, workspace)
    monkeypatch.setattr(orca, "save", lambda *args: pytest.fail("a resumed lane's Sprite holds its work, so nothing is saved off it"))
    monkeypatch.setattr(orca, "release", lambda *args: pytest.fail("a resumed lane's Sprite is never destroyed"))
    assert run("job", "resume", "j1", "Build #4412 failed: fix the lint") == (
        0,
        f"j1: the message went to the job's worker, which resumed its own Claude session {J2504_SESSION} on Sprite {workspace} as dispatch dispatch-2,"
        " in the checkout its lane left there dead (its worker exited)",
    )
    assert run("job", "resume", "j1", "carry on")[1].startswith(f"j1: the message went to the job's worker, which resumed its own Claude session {J2504_SESSION} on Sprite {workspace} as dispatch dispatch-3,")
    assert [(name, on, session) for name, _, on, session in launched] == [("j1-2.resume.md", workspace, J2504_SESSION), ("j1-3.resume.md", workspace, J2504_SESSION)]
    brief = launched[0][1]
    assert f": its worker exited. This is your own Claude session, resumed on Sprite {workspace} in the checkout you left" in brief
    assert brief.endswith("which you act on now:\n\nBuild #4412 failed: fix the lint\n")
    assert kept_logs == [("j1", J2504_DISPATCH), ("j1", "dispatch-2")]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], job["state"], job["brief"]) == ("dispatch-3", workspace, "running", str(tmp_path / "first.md"))
    assert [(death["dispatch"], death["workspace"], death.get("saved")) for death in job["deaths"]] == [(J2504_DISPATCH, workspace, None), ("dispatch-2", workspace, None)]


@pytest.mark.parametrize(
    ("refusal", "relaunched_on"),
    [
        (orca.Unstarted("orca-worker-launch.sh exited 1 before a worker started: cc-remote orca prepare exited 1"), J2504_SPRITES[1]),
        (orca.Unfit("its Sprite refused the tools a worker needs"), None),
    ],
)
def test_a_session_that_does_not_resume_relaunches_the_lane_as_a_dead_lanes(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: dict[str, str], refusal: orca.OrcaError, relaunched_on: str | None
) -> None:
    cloned(tmp_path, remote)
    launched = restarted_lane(remote, tmp_path, monkeypatch, sessions, J2504_SPRITES[1], refusal)
    monkeypatch.setattr(orca, "save", unsaveable(UNREAD))
    code, out = run("job", "resume", "j1", "Build #4412 failed: fix the lint")
    assert code == 0
    assert f"(its worker exited, and its Claude session {J2504_SESSION} did not resume there: {refusal})" in out
    assert [(name, on, session) for name, _, on, session in launched] == [("j1-2.resume.md", J2504_SPRITES[1], J2504_SESSION), ("j1-2.md", relaunched_on, None)]
    assert "## Taking over from a lane that died" in orca.brief_of(launched[1][1])


LANE_SESSION = [
    {"type": "user", "message": {"role": "user", "content": "Lane durable-agents-j1: read the whole brief"}, "timestamp": "2026-11-13T08:00:00Z"},
    {"type": "assistant", "message": {"role": "assistant", "id": "msg_1", "content": [{"type": "text", "text": "DEFECT: cci post refused it, full output below"}]}, "timestamp": "2026-11-13T08:01:00Z"},
]


def test_end_keeps_the_lanes_whole_transcript_before_its_sprite_is_destroyed_and_a_later_brief_names_the_kept_copy(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    home = tmp_path / "pool-one"
    session = home / ".claude" / "projects" / "-home-sprite-monorepo" / "0b1c9e.jsonl"
    (session.parent / "memory").mkdir(parents=True)
    written = "".join(json.dumps(line) + "\n" for line in LANE_SESSION)
    session.write_text(written)

    def on_sprite(workspace: str, command: str, given: bytes, seconds: float) -> subprocess.CompletedProcess[bytes]:
        if not home.exists():
            return subprocess.CompletedProcess([], 255, b"", b"Can't open user config file pool-one.ssh: No such file or directory")
        return subprocess.run(["sh", "-c", command], input=given, capture_output=True, env={**os.environ, "HOME": str(home)}, check=False)

    monkeypatch.setattr(orca, "keep_transcripts", REAL_KEEP_TRANSCRIPTS)
    monkeypatch.setattr(orca, "on_sprite", on_sprite)
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: shutil.rmtree(home) or f"destroyed pool-one dispatch {dispatch}")
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", workspace="pool-one")
    assert run("job", "end", "j1") == (0, f"destroyed pool-one dispatch {DISPATCH}\nj1's lane ended; j1 stays waiting")
    assert not home.exists()
    job = state.load(remote)["jobs"]["j1"]
    kept = orca.transcripts(remote, "j1", DISPATCH) / "-home-sprite-monorepo" / "0b1c9e.jsonl"
    assert (artifacts.transcript(job), kept.read_text(), "transcript" in artifacts.kept("j1", job, set())) == (kept, written, True)
    assert [turn["who"] for turn in artifacts.read(remote, "j1", job, "transcript")["turns"]] == ["Coordinator", "Claude"]
    line = cli.transcript_line("j1", job)
    assert line == f"j1 builder: {kept}"
    assert orca.carried(remote, f"Transcripts:\n- {line}") == ""
    assert "DEFECT: cci post refused it" in reads.read_drive_files(f"READ {kept}", remote)


def test_a_sprite_whose_transcripts_cannot_be_read_leaves_why_where_they_would_be_kept(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refused = b"ssh: connect to host pool-asleep port 22: Connection refused\n"
    monkeypatch.setattr(orca, "on_sprite", lambda workspace, command, given, seconds: subprocess.CompletedProcess([], 255, b"", refused))
    assert REAL_KEEP_TRANSCRIPTS(remote, "j1", {"workspace": "pool-asleep"}, DISPATCH) is None
    assert (orca.transcripts(remote, "j1", DISPATCH) / orca.UNKEPT).read_text() == "ssh exited 255: ssh: connect to host pool-asleep port 22: Connection refused\n"


@pytest.mark.parametrize("seen", [False, True])
def test_end_copies_the_sprite_logs_only_of_a_lane_the_watch_saw_die(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept_logs: list[tuple[str, str]], seen: bool
) -> None:
    cloned(tmp_path, remote)
    died = {"dispatch": DISPATCH, "workspace": "pool-sleeping", "why": GONE_SPRITE, "at": state.stamp(T0)}
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", workspace="pool-sleeping", deaths=[died] if seen else [])
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "")
    assert run("job", "end", "j1")[0] == 0
    assert kept_logs == ([("j1", DISPATCH)] if seen else [])


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
        f" since what that Sprite holds on no remote could not be saved to GitHub: {why}",
    )
    assert (launched["lane"], launched["workspace"]) == ("demo-j1-2", "pool-sleeping")
    brief = orca.brief_of(launched["brief"])
    assert (
        "You continue the job on the same Sprite, in the checkout it left as it was, since what that Sprite holds on no remote could not be"
        f" saved to GitHub: {why}. Read its git status and log before anything else." in brief
    )
    assert orca.UNPUSHED not in brief
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], [(death["dispatch"], death["workspace"]) for death in job["deaths"]]) == (
        "dispatch-2",
        "pool-sleeping",
        [(DISPATCH, "pool-sleeping")],
    )


J1409_LANES = {
    "j242": ("pool-oqjx23ri66atfgfg", "ctx_c44e96f422c4", 65303),
    "j489": ("pool-2hwh2ntcbx2vi2jh", "ctx_df202ff0ae15", 53666),
    "j414": ("pool-cmvrj533rully62b", "ctx_de61a1b48f4c", 56897),
    "j601": ("pool-ereynip6pqkdwptf", "ctx_cdf88856d82c", 60892),
    "j290": ("pool-2ga7fnjkv5qghs7b", "ctx_7ce8b24fa959", 52522),
}
J2504_LANE = ("pool-k2iqtr6xv3wolr6f", "ctx_0afc9f8512dd", 59582)
J982_10 = ("durable-agents-j982-10", "ctx_d345002d9b07", 63715)
NO_WAKE = "failed to start sprite command: failed to connect: read tcp [2600:1700:2f71:844f:98cd:4e66:a709:7381]:49893->[2a09:8280:1::99:d7b3:0]:443: i/o timeout"


def unreachable(port: int) -> str:
    return f"Orca could not reach its Sprite: orca orchestration worker-show: remote_runtime_unavailable: Could not connect to the remote Orca runtime at ws://127.0.0.1:{port}"


def unreachable_lane(remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: tuple[str, str, int], after_wake: Callable[[], dict] | None) -> tuple[list[tuple[str, ...]], list[dict]]:
    """j1's lane on the Sprite, dispatch and port j1409 read remote_runtime_unavailable on; a wake that reads the lane as after_wake does, or one that fails as j489's did, whose Sprite then refuses prepare --existing too."""
    workspace, dispatch, port = lane
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), dispatch=dispatch, workspace=workspace, branch="durable-agents-j2504/sandsql-two-redo-volumes", said="building the tallies")
    wakes, launches = [], []
    down = refused("remote_runtime_unavailable", f"Could not connect to the remote Orca runtime at ws://127.0.0.1:{port}")

    def reclaimer(checkout: Path, *args: str) -> str:
        wakes.append(args)
        if after_wake is None:
            raise orca.OrcaError(f"orca-reclaim.py exited 1: {NO_WAKE}")
        return f"woke {args[1]}"

    def launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launches.append({"workspace": workspace, "session": session, "brief": brief.read_text()})
        if workspace and after_wake is None:
            raise orca.Unstarted(f"orca-worker-launch.sh exited 1 before a worker started: cc-remote orca prepare {workspace} --existing: {NO_WAKE}")
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "environment", "lane")} | {"workspace": workspace or "workspace-2"}

    monkeypatch.setattr(orca, "reclaimer", reclaimer)
    monkeypatch.setattr(orca, "worker", lambda shown: after_wake() if wakes and after_wake else down(shown))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    monkeypatch.setattr(orca, "launch", launch)
    return wakes, launches


@pytest.mark.parametrize("job_id", J1409_LANES)
@pytest.mark.parametrize(
    "after_wake",
    [dead, lambda: refused("remote_runtime_unavailable", "Could not connect to the remote Orca runtime at ws://127.0.0.1:61207")(DISPATCH)],
    ids=["exited once woken", "unreachable once woken"],
)
def test_a_lane_orca_could_not_reach_restarts_on_its_own_sprite_once_the_wake_reaches_it(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_id: str, after_wake: Callable[[], dict]
) -> None:
    workspace, dispatch, port = J1409_LANES[job_id]
    wakes, launches = unreachable_lane(remote, tmp_path, monkeypatch, J1409_LANES[job_id], after_wake)
    monkeypatch.setattr(orca, "save", lambda *args: pytest.fail("a lane continuing on its own Sprite saves nothing"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a lane that ended: {args}"))
    code, out = run("job", "resume", "j1", watch.UNREACHED)
    assert (code, out.startswith(f"j1: the message went to the job's next worker, relaunched on Sprite {workspace} as dispatch dispatch-2")) == (0, True)
    assert out.endswith(f"and it continues in the checkout the dead lane left on {workspace}, since Orca could not reach that Sprite")
    assert f"was dead ({unreachable(port)}, and " in out
    assert (wakes, [launched["workspace"] for launched in launches]) == ([("--wake", workspace)], [workspace])
    brief = orca.brief_of(launches[0]["brief"])
    assert "You continue the job on the same Sprite, in the checkout it left as it was, since Orca could not reach that Sprite." in brief
    assert brief.endswith(f"which you act on now:\n\n{watch.UNREACHED}")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], [(death["dispatch"], death["workspace"]) for death in job["deaths"]]) == ("dispatch-2", workspace, [(dispatch, workspace)])


def test_a_lane_orca_could_not_reach_resumes_its_own_claude_session_on_its_sprite_once_the_wake_reaches_it(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: dict[str, str]
) -> None:
    workspace, dispatch, port = J1409_LANES["j290"]
    wakes, launches = unreachable_lane(remote, tmp_path, monkeypatch, J1409_LANES["j290"], dead)
    sessions[workspace] = "4f9cdc9b-50e4-429f-ad12-d06b9798712a"
    monkeypatch.setattr(orca, "save", lambda *args: pytest.fail("a lane resuming its own session saves nothing"))
    code, out = run("job", "resume", "j1", watch.UNREACHED)
    assert (code, out.startswith(f"j1: the message went to the job's worker, which resumed its own Claude session {sessions[workspace]} on Sprite {workspace}")) == (0, True)
    assert (wakes, [(launched["workspace"], launched["session"]) for launched in launches]) == ([("--wake", workspace)], [(workspace, sessions[workspace])])
    assert launches[0]["brief"].rstrip().endswith(watch.UNREACHED)


@pytest.mark.parametrize("lane", [J1409_LANES["j489"], J1409_LANES["j414"], J1409_LANES["j601"], J2504_LANE])
def test_a_pushed_job_whose_sprite_will_not_wake_restarts_on_a_fresh_sprite_from_its_pushed_branch_with_its_held_text(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: tuple[str, str, int]
) -> None:
    workspace, dispatch, port = lane
    wakes, launches = unreachable_lane(remote, tmp_path, monkeypatch, lane, None)
    monkeypatch.setattr(orca, "save", unsaveable(UNREAD))
    monkeypatch.setattr(orca, "release", lambda *args: pytest.fail("a Sprite holding what no save reached is kept"))
    held = "Next event for builder j2504, from the coordinator. Design job j3161 picked the two redo volumes."
    with state.edit(remote) as data:
        data["jobs"]["j1"]["undelivered"] = [{"seq": None, "words": held, "at": state.stamp(T0), "said": None, "request": "req-held"}]
    code, out = run("job", "resume", "j1")
    assert (code, out.startswith("j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2")) == (0, True)
    assert f"was dead ({unreachable(port)}, and its wake read orca-reclaim.py exited 1: {NO_WAKE}, and orca-worker-launch.sh exited 1" in out
    assert out.endswith(f"{workspace} is kept, since what it holds on no remote could not be saved: {UNREAD}")
    assert (wakes, [launched["workspace"] for launched in launches]) == ([("--wake", workspace)], [workspace, None])
    brief = orca.brief_of(launches[-1]["brief"])
    assert f"You take the job over on a fresh Sprite from its pushed branches; {orca.UNPUSHED}" in brief
    assert "Its branch: durable-agents-j2504/sandsql-two-redo-volumes\n" in brief and brief.endswith(f"which you act on now:\n\n{held}")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], job["undelivered"], [death["workspace"] for death in job["deaths"]]) == ("dispatch-2", "workspace-2", [], [workspace])


def test_a_lane_that_takes_messages_once_its_sprite_wakes_gets_the_text_and_nothing_restarts(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes, launches = unreachable_lane(remote, tmp_path, monkeypatch, ("pool-ochwykolpjebecve", DISPATCH, 61207), lambda: LIVE_WORKER)
    sent = []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    assert run("job", "resume", "j1", watch.UNREACHED) == (0, f"j1: sent to dispatch {DISPATCH}")
    assert (wakes, launches) == ([("--wake", "pool-ochwykolpjebecve")], [])
    assert sent == [("send", "--to", f"dispatch:{DISPATCH}", "--run", RUN, "--subject", "From the coordinator", "--body", watch.UNREACHED, "--retry-request", ANY)]


@pytest.mark.parametrize("saved", [SAVED, None], ids=["saved", "unsaveable"])
def test_j982s_full_disk_sprite_whose_claude_lives_while_nothing_listens_where_orcas_runtime_serves_restarts_on_a_fresh_sprite_with_its_branches_saved_and_its_held_texts(
    run: Run,
    remote: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtimes_listening: dict[str, bool],
    sessions: dict[str, str],
    saved_on_github: dict[str, list[str]],
    saved: str | None,
) -> None:
    workspace, dispatch, port = J982_10
    wakes, launches = unreachable_lane(remote, tmp_path, monkeypatch, J982_10, lambda: refused("remote_runtime_unavailable", f"Could not connect to the remote Orca runtime at ws://127.0.0.1:{port}")(dispatch))
    runtimes_listening[workspace] = False
    sessions[workspace] = "5a1f0c3e-7d2b-4e8a-9c61-0b2d4f6e8a1c"
    saved_on_github["demo-j1"] = SAVED_BRANCHES if saved else []
    monkeypatch.setattr(orca, "save", (lambda drive, job_id, job, root: saved) if saved else unsaveable(UNREAD))
    monkeypatch.setattr(orca, "holds_sprite", lambda job: True)
    released = []
    monkeypatch.setattr(orca, "release", lambda drive, job_id, job, root: released.append(job["workspace"]) or f"destroyed {job['workspace']}")
    held = "Next event for builder j982, from the coordinator. TAP Approve #12"
    with state.edit(remote) as data:
        data["jobs"]["j1"]["undelivered"] = [{"seq": None, "words": held, "at": state.stamp(T0), "said": None, "request": "req-held"}]
    code, out = run("job", "resume", "j1", watch.UNREACHED)
    assert (code, out.startswith("j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2")) == (0, True)
    assert f"was dead ({unreachable(port)}, and its wake read {unreachable(port).removeprefix('Orca could not reach its Sprite: ')}, and its Sprite has nothing listening on the port Orca's runtime serves on there)" in out
    assert out.endswith(f"what {workspace} held on no remote is saved on GitHub as {ON_GITHUB}; destroyed {workspace}" if saved else f"{workspace} is kept, since what it holds on no remote could not be saved: {UNREAD}")
    assert (wakes, [(launched["workspace"], launched["session"]) for launched in launches], released) == ([("--wake", workspace)], [(None, None)], [workspace] if saved else [])
    assert orca.brief_of(launches[0]["brief"]).endswith(f"which you act on now:\n\n{held}\n\n{watch.UNREACHED}")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], job["undelivered"], [(death["workspace"], death.get("saved")) for death in job["deaths"]]) == ("dispatch-2", "workspace-2", [], [(workspace, saved)])


def test_a_lane_orca_could_not_reach_whose_woken_sprite_says_orcas_runtime_listens_holds_the_text_and_restarts_nothing(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtimes_listening: dict[str, bool]
) -> None:
    workspace, dispatch, port = J982_10
    wakes, launches = unreachable_lane(remote, tmp_path, monkeypatch, J982_10, lambda: refused("remote_runtime_unavailable", f"Could not connect to the remote Orca runtime at ws://127.0.0.1:{port}")(dispatch))
    runtimes_listening[workspace] = True
    monkeypatch.setattr(orca, "save", lambda *args: pytest.fail("a lane whose Sprite's runtime listens is never saved"))
    code, out = run("job", "resume", "j1", watch.UNREACHED)
    assert (code, out.startswith(f"j1: held, not sent yet: orca orchestration worker-show: remote_runtime_unavailable: Could not connect to the remote Orca runtime at ws://127.0.0.1:{port}")) == (0, True)
    assert (wakes, launches) == ([("--wake", workspace)], [])
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], "deaths" in job, [kept["words"] for kept in state.held(job)]) == (dispatch, False, [watch.UNREACHED])


def reprepared(monkeypatch: pytest.MonkeyPatch, dispatch: str = "dispatch-2") -> list[tuple[str, str | None]]:
    """Each launch a resume of a finished Sprite lane starts, by lane and the workspace it continues on, its worker started on that Sprite."""
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    monkeypatch.setattr(orca, "bound_command", lambda *args: pytest.fail("no worker starts in the terminal of a finished worker on a Sprite"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "save", lambda *args: pytest.fail("a Sprite prepared again keeps its checkout, so nothing is saved off it"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = []

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.append((lane, workspace))
        return {"dispatch": dispatch, "task": "task_2", "terminal": "term_fresh", "lane": lane, "workspace": workspace, "environment": workspace}

    monkeypatch.setattr(orca, "launch", fake_launch)
    return launched


def test_resume_to_a_worker_that_finished_on_a_sprite_prepares_that_sprite_again_and_starts_the_next_one_in_its_checkout_with_the_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tooled: list[str]
) -> None:
    cloned(tmp_path, remote)
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
    reprepared(monkeypatch)
    monkeypatch.setattr(orca, "launch", LAUNCH)
    monkeypatch.setattr(orca, "tool", lambda name: name)
    ran, started = subprocess.run, {}

    def launcher(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if argv[0] != orca.LAUNCHER:
            return ran(argv, **kwargs)
        env = kwargs["env"]
        started.update(lane=argv[1], brief=Path(argv[-1]).read_text(), run=env["WORKER_RUN"], workspace=env.get("WORKER_WORKSPACE"))
        attempt = Path(argv[-1]).with_suffix(".worker")
        attempt.mkdir()
        (attempt / "prepared.json").write_text(json.dumps({"environment": "pool-one"}))
        receipt = {"state": "ready", "task": "task_2", "dispatch": "ctx_lane0002", "terminal": "term_fresh", "lane": argv[1], "workspace": "pool-one", "allocation": "existing"}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt) + "\n", stderr="")

    monkeypatch.setattr(orca.subprocess, "run", launcher)
    code, out = run("job", "resume", "j1", "The review: one medium finding, fix it")
    assert (code, out) == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite pool-one as dispatch ctx_lane0002 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one had finished (its"
        f" worker succeeded, and Orca sends nothing to a finished worker on a Sprite), and {REPREPARED}",
    )
    assert (started["lane"], started["run"], started["workspace"], tooled) == ("demo-j1-2", FRESH_RUN, "pool-one", [])
    assert started["brief"].startswith("Remote lane: drive demo, job j1.")
    brief = orca.brief_of(started["brief"])
    assert brief.startswith("Order (his words): prove it\nDrive: demo, order o1, job j1\n\n## Taking over from a lane that finished")
    assert "You continue the job on the same Sprite, in the checkout it left as it was." in brief
    assert "Its branch: ci-mac-box-prove\nIts last line: WAITING security review of #7\n" in brief
    assert "Its last report:\n\nWAITING security review of #7\nPR: https://github.com/Forge-AI/monorepo/pull/7\n" in brief
    assert brief.endswith("which you act on now:\n\nThe review: one medium finding, fix it")
    job = state.load(remote)["jobs"]["j1"]
    assert {field: job[field] for field in ("run", "dispatch", "task", "terminal", "workspace", "environment", "lane", "state", "report", "reads")} == {
        "run": FRESH_RUN,
        "dispatch": "ctx_lane0002",
        "task": "task_2",
        "terminal": "term_fresh",
        "workspace": "pool-one",
        "environment": "pool-one",
        "lane": "demo-j1-2",
        "state": "running",
        "report": None,
        "reads": {},
    }
    assert [(ended["dispatch"], ended["workspace"]) for ended in job["deaths"]] == [(DISPATCH, "pool-one")]


def test_resume_to_a_finished_worker_on_this_mac_whose_terminal_orca_no_longer_finds_relaunches_the_job_as_a_dead_lanes(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", state.LOCAL_ROUTE))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), route=state.LOCAL_ROUTE, terminal="term_lane", workspace=None, environment=None)
    reply = {"ok": False, "error": {"code": "selector_not_found", "message": "No terminal matches term_lane."}}
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    monkeypatch.setattr(orca, "bound_command", lambda drive, run_id, argv: subprocess.CompletedProcess(argv, 1, stdout=json.dumps(reply), stderr=""))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), run=run_id, route=route, workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "lane")} | {"workspace": None, "environment": None}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "resume", "j1", "The review: one medium finding, fix it") == (
        0,
        "j1: the message went to the job's next worker, relaunched in a fresh worktree on this Mac as dispatch dispatch-2 in a"
        " handoff carrying its brief, its branch, its last line, its report and its PRs; its lane on this Mac was dead (Orca no"
        " longer holds its worker's terminal or worktree), and work not pushed from its worktree is not carried over",
    )
    assert (launched["lane"], launched["run"], launched["route"], launched["workspace"]) == ("demo-j1-2", FRESH_RUN, state.LOCAL_ROUTE, None)
    brief = orca.brief_of(launched["brief"])
    assert "## Taking over from a lane that died" in brief
    assert brief.endswith("which you act on now:\n\nThe review: one medium finding, fix it")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["route"], [death["dispatch"] for death in job["deaths"]]) == ("dispatch-2", state.LOCAL_ROUTE, [DISPATCH])


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = []

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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


def untooled(monkeypatch: pytest.MonkeyPatch, launch: Callable[..., dict]) -> None:
    ran = subprocess.run
    monkeypatch.setattr(orca, "launch", launch)
    monkeypatch.setattr(orca, "tool", lambda name: name)

    def launcher(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if argv[0] != orca.LAUNCHER:
            return ran(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=UNTOOLED.format(attempt=Path(argv[-1]).with_suffix(".worker")) + "\n")

    monkeypatch.setattr(orca.subprocess, "run", launcher)


def test_a_launch_whose_sprite_took_no_tools_exits_2_naming_the_tool_step_and_stops_reading_as_launching(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, demo)
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    untooled(monkeypatch, orca.launch)
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    attempt = state.drive_dir(demo) / "jobs" / "j1.worker"
    assert capsys.readouterr().err == f"drive: {orca.LAUNCHER} exited 1 before a worker started: {UNTOOLED.format(attempt=attempt)}; attempt in {attempt}\n"
    job = state.load(demo)["jobs"]["j1"]
    assert (job["launching"], job.get("dispatch")) == (None, None)


def test_a_resume_whose_fresh_sprite_took_no_tools_exits_2_naming_the_tool_step_and_leaves_his_answer_unsent(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launch = orca.launch
    answered_dead_lane(remote, tmp_path, monkeypatch, lambda drive, job_id, job, root: None, 0)
    untooled(monkeypatch, launch)
    assert cli.main(["job", "resume", "j1", "His answer, verbatim: Approve"]) == 2
    attempt = state.drive_dir(remote) / "jobs" / "j1-2.worker"
    assert capsys.readouterr().err == f"drive: {orca.LAUNCHER} exited 1 before a worker started: {UNTOOLED.format(attempt=attempt)}; attempt in {attempt}\n"
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


def test_a_resume_to_a_live_worker_hands_the_text_it_took_to_the_newer_install_that_saved_meanwhile_so_it_goes_out_once(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", ask="q-1")
    sent, handed = [], []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION

    def took(verb: str, *args: object) -> dict:
        sent.append(verb)
        if verb == "reply":
            monkeypatch.setattr(state, "VERSION", "0.0.1")
        return {}

    real = subprocess.run

    def newer(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] != str(newest):
            return real(argv, **kwargs)
        handed.append(argv[1:])
        monkeypatch.setattr(state, "VERSION", version)
        code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capsys.readouterr().out, "")

    monkeypatch.setattr(orca, "call", took)
    monkeypatch.setattr(cli.install, "upgraded", lambda: newest)
    monkeypatch.setattr(cli.install.subprocess, "run", newer)
    assert run("job", "resume", "j1", "PR #7 merged") == (0, "j1: answered its question q-1")
    [argv] = handed
    assert (argv[:3], argv[4:], Path(argv[3]).exists(), sent.count("reply")) == (["job", "sent", "j1"], ["--drive", remote, "--json"], False, 1)
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["undelivered"]) == (None, "running", [])


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


@pytest.mark.parametrize(
    ("save", "branches", "kept"),
    [
        (lambda drive, job_id, job, root: None, [], "work not pushed from pool-one is not carried over"),
        (lambda drive, job_id, job, root: SAVED, SAVED_BRANCHES, f"what pool-one held on no remote is saved on GitHub as {ON_GITHUB}"),
        (unsaveable(UNREAD), [], f"pool-one is kept, since what it holds on no remote could not be saved: {UNREAD}"),
    ],
    ids=["nothing to save", "saved", "save failed"],
)
def test_a_finished_worker_whose_sprite_refuses_its_tools_starts_the_next_on_a_fresh_sprite(
    run: Run,
    remote: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved_on_github: dict[str, list[str]],
    save: Callable[..., str | None],
    branches: list[str],
    kept: str,
) -> None:
    cloned(tmp_path, remote)
    saved_on_github["demo-j1"] = branches
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one")
    reprepared(monkeypatch)
    monkeypatch.setattr(orca, "save", save)
    refusal = UNFIT_KEPT.replace("sprite-old", "pool-one")
    launched = []

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.append((lane, brief.read_text(), workspace))
        if workspace:
            raise orca.Unfit(refusal)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "launch", "j1") == (
        0,
        "j1: the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2 in a handoff carrying its brief, its"
        " branch, its last line, its report and its PRs; its lane on Sprite pool-one was dead (its worker succeeded, and Orca sends"
        f" nothing to a finished worker on a Sprite, and {refusal}), and {kept}",
    )
    assert [(lane, workspace) for lane, _, workspace in launched] == [("demo-j1-2", "pool-one"), ("demo-j1-2", None)]
    brief = orca.brief_of(launched[-1][1])
    assert "## Taking over from a lane that died" in brief
    assert "which you act on now" not in brief
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], [(death["dispatch"], death["workspace"]) for death in job["deaths"]]) == ("dispatch-2", "workspace-2", [(DISPATCH, "pool-one")])


def test_a_launch_of_a_lane_whose_worker_finished_prepares_its_sprite_again_with_no_message_and_one_that_works_is_refused(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one", lane="demo-j1")
    assert cli.main(["job", "launch", "j1"]) == 2
    assert capsys.readouterr().err == f"drive: j1 already started as dispatch {DISPATCH}, whose worker takes messages: drive job resume j1 <the event> sends it one\n"
    launched = reprepared(monkeypatch)
    assert run("job", "launch", "j1") == (
        0,
        "j1: the job's next worker, relaunched on Sprite pool-one as dispatch dispatch-2 in a handoff carrying its brief, its"
        " branch, its last line, its report and its PRs; its lane on Sprite pool-one had finished (its worker succeeded, and Orca"
        f" sends nothing to a finished worker on a Sprite), and {REPREPARED}",
    )
    assert launched == [("demo-j1-2", "pool-one")]
    brief = orca.brief_of((state.drive_dir(remote) / "jobs" / "j1-2.md").read_text())
    assert "## Taking over from a lane that finished" in brief
    assert "which you act on now" not in brief
    assert state.load(remote)["jobs"]["j1"]["dispatch"] == "dispatch-2"


def test_a_launch_local_of_a_job_whose_sprite_worker_finished_saves_that_sprite_and_starts_its_next_in_a_fresh_worktree_on_this_mac(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], saved_on_github: dict[str, list[str]]
) -> None:
    cloned(tmp_path, remote)
    saved_on_github["demo-j1"] = ["saved/demo-j1/wip at 1a2b3c4d5e"]
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), terminal="term_lane", workspace="pool-one", environment="pool-one", lane="demo-j1")
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    monkeypatch.setattr(orca, "bound_command", lambda *args: pytest.fail("a move to this Mac never starts its next worker in the Sprite's terminal"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)

    def unsaved(drive: str, job_id: str, job: dict, root: Path) -> str:
        raise orca.OrcaError("cc-remote-reclaim exited 1: pool-one unreachable")

    monkeypatch.setattr(orca, "save", unsaved)
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("nothing moves to this Mac while its Sprite's work is unsaved"))
    assert cli.main(["job", "launch", "j1", "--local"]) == 2
    assert capsys.readouterr().err == (
        "drive: j1: not moved to this Mac, since what its Sprite pool-one holds on no remote could not be saved to GitHub:"
        " cc-remote-reclaim exited 1: pool-one unreachable\n"
    )
    job = state.load(remote)["jobs"]["j1"]
    assert (job["route"], job["dispatch"], "deaths" in job) == (None, DISPATCH, False)
    saves, launched = [], {}
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: saves.append(job["workspace"]) or "uncommitted changes in /home/sprite/monorepo (1 file), saved to Forge-AI/monorepo under saved/demo-j1/")

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), route=route, workspace=workspace)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    assert run("job", "launch", "j1", "--local") == (
        0,
        "j1: the job's next worker, relaunched in a fresh worktree on this Mac as dispatch dispatch-2 in a handoff carrying its"
        " brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one had finished (its worker succeeded,"
        " and Orca sends nothing to a finished worker on a Sprite), and what pool-one held on no remote is saved on GitHub as saved/demo-j1/wip at 1a2b3c4d5e",
    )
    assert (saves, launched["lane"], launched["route"], launched["workspace"]) == (["pool-one"], "demo-j1-2", state.LOCAL_ROUTE, None)
    brief = orca.brief_of(launched["brief"])
    assert "## Taking over from a lane that finished\n\nThe lane that held this job finished on Sprite pool-one at " in brief
    assert "You take the job over in a fresh worktree on this Mac from its pushed branches; what it held on no remote was saved for you on GitHub as saved/demo-j1/wip at 1a2b3c4d5e." in brief
    job = state.load(remote)["jobs"]["j1"]
    assert (job["route"], job["dispatch"], [death["workspace"] for death in job["deaths"]]) == (state.LOCAL_ROUTE, "dispatch-2", ["pool-one"])


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    sent, launches = [], []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    launching, waiting, release = threading.Event(), threading.Event(), threading.Event()
    locked, entered = state.job_locked, itertools.count(1)

    def job_locked(name: str, job_id: str, seconds: float):
        if next(entered) == 2:
            waiting.set()
        return locked(name, job_id, seconds)

    def slow_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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
    assert sent == [("send", "--to", "dispatch:dispatch-2", "--run", FRESH_RUN, "--subject", "From the coordinator", "--body", "TAP Approve #12", "--retry-request", ANY)]


def orca_refuses(monkeypatch: pytest.MonkeyPatch, refusal: str) -> None:
    verb, code, message = refusal.split(": ", 2)
    done = subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": {"code": code, "message": message}}), stderr="")
    monkeypatch.setattr(orca.subprocess, "run", lambda argv, **kwargs: done)
    monkeypatch.setattr(orca, "bound_command", lambda drive, run_id, argv: done)


def test_a_relaunch_orca_refuses_exits_2_with_the_refusal_verbatim_and_keeps_the_text_held(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it", state.LOCAL_ROUTE))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), route=state.LOCAL_ROUTE, terminal="term_lane", workspace=None, environment=None)
    refusal = "orca orchestration worker-start: terminal_busy: The terminal is running another worker."
    orca_refuses(monkeypatch, refusal)
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    assert cli.main(["job", "resume", "j1", "the review"]) == 2
    assert capsys.readouterr().err == f"drive: {refusal}\n"
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], [kept["words"] for kept in state.held(job)]) == (DISPATCH, ["the review"])


def test_a_send_orca_refuses_keeps_the_text_held_under_its_request_id(run: Run, remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = "orca orchestration send: dispatch_inactive: Dispatch ctx_lane0001 finished on pool-one, which cannot take follow-ups for finished workers yet."
    orca_refuses(monkeypatch, refusal)
    monkeypatch.setattr(orca, "place_tools", lambda workspace: None)
    code, out = run("job", "resume", "j1", "the review")
    [kept] = state.held(state.load(remote)["jobs"]["j1"])
    assert (code, out, kept["words"]) == (
        0,
        f"j1: held, not sent yet: {refusal}; the job keeps its text under the Orca request id each was held with, the watch sends them"
        " once Orca reads its lane on its Sprite alive, and its next worker gets them in its handoff",
        "the review",
    )


def test_resume_holds_the_text_and_sends_nothing_while_orca_cannot_tell_whether_the_lane_lives(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead("unverifiable"))
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a lane Orca cannot see is never relaunched"))
    code, out = run("job", "resume", "j1", "PR checks failed")
    assert (code, sent, out.startswith("j1: held, not sent yet: ")) == (0, [], True)
    job = state.load(remote)["jobs"]["j1"]
    assert ("deaths" in job, [kept["words"] for kept in state.held(job)]) == (False, ["PR checks failed"])


def test_a_held_text_goes_once_under_its_request_id_when_a_bare_resume_finds_the_lane_alive(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead("unverifiable"))
    with state.edit(remote) as data:
        data["jobs"]["j1"]["undelivered"] = [{"seq": "#61", "words": "ship it", "at": state.stamp(), "said": None}]
    assert run("job", "resume", "j1", "ship it, he said")[0] == 0
    assert run("job", "resume", "j1", "ship it, he said")[0] == 0
    [kept] = state.held(state.load(remote)["jobs"]["j1"])
    monkeypatch.setattr(orca, "worker", lambda dispatch: LIVE_WORKER)
    assert run("job", "resume", "j1") == (0, f"j1: sent to dispatch {DISPATCH}")
    assert run("job", "resume", "j1") == (0, "j1 holds no message to send")
    assert sent == [
        ("send", "--to", f"dispatch:{DISPATCH}", "--run", RUN, "--subject", "From the coordinator", "--body", "ship it, he said", "--retry-request", str(uuid.uuid5(uuid.UUID(kept["request"]), "send")))
    ]
    assert state.load(remote)["jobs"]["j1"]["undelivered"] == []


def test_a_relaunch_carries_each_held_text_in_its_handoff_ahead_of_its_own(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old")
        data["jobs"]["j1"]["undelivered"] = [{"seq": None, "words": "TAP Approve #12", "at": state.stamp(), "said": None, "request": "req-held"}]
    launched = []
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"a dead lane is sent nothing: {args}"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(
        orca,
        "launch",
        lambda drive, lane, brief, checkout, run_id, route, workspace, session: launched.append(brief.read_text())
        or {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")},
    )
    assert run("job", "resume", "j1", watch.CARRY_ON)[0] == 0
    [brief] = launched
    assert orca.brief_of(brief).endswith(f"which you act on now:\n\nTAP Approve #12\n\n{watch.CARRY_ON}")
    assert state.load(remote)["jobs"]["j1"]["undelivered"] == []


def test_a_pr_wake_to_a_dead_lane_relaunches_its_job_with_the_line_in_its_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    url = "https://github.com/Forge-AI/monorepo/pull/7"
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", prs=[url])
        data["prs"][state.pr_key(url)] = state.new_pr(url, "j1", "o1")
    launched = []
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"a dead lane is sent nothing: {args}"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(
        orca,
        "launch",
        lambda drive, lane, brief, checkout, run_id, route, workspace, session: launched.append(brief.read_text())
        or {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")},
    )
    code, out = run("pr", "wake", url, "--text", "PR #7 is red: fix it")
    assert (code, out.startswith("j1: the message went to the job's next worker, relaunched on Sprite workspace-2")) == (0, True)
    [brief] = launched
    assert orca.brief_of(brief).endswith("which you act on now:\n\nPR #7 is red: fix it")


@pytest.mark.parametrize(
    ("pr_state", "line", "stops_while_waiting"),
    [
        ("open", "Forge-AI/monorepo#7 red, owed by its builder: its required checks failed", False),
        ("closed", "PR Forge-AI/monorepo#7: NOT READY: Forge-AI/monorepo#7 go against his words [o1 j1]", False),
        ("open", "Forge-AI/monorepo#7 red, owed by its builder: its required checks failed", True),
    ],
    ids=["j2076-red-pr-of-a-stopped-job", "j1989-closed-pr-of-a-stopped-job", "stopped-while-the-wake-waited-on-its-lock"],
)
def test_a_pr_wake_for_a_job_his_answer_stopped_starts_no_worker_and_hands_the_line_to_the_session_holding_its_order(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pr_state: str, line: str, stops_while_waiting: bool
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    url = "https://github.com/Forge-AI/monorepo/pull/7"
    key = state.pr_key(url)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", prs=[url], state="running" if stops_while_waiting else "stopped")
        data["prs"][key] = state.new_pr(url, "j1", "o1") | {"state": pr_state}
    locked = state.job_locked

    def stopped_on_the_way(name: str, job_id: str, seconds: float):
        with state.edit(name) as data:
            data["jobs"][job_id]["state"] = "stopped"
        return locked(name, job_id, seconds)

    if stops_while_waiting:
        monkeypatch.setattr(state, "job_locked", stopped_on_the_way)
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"a stopped job is sent nothing: {args}"))
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a stopped job takes no worker start from a PR wake"))
    posted = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: posted.append(argv) or "")
    assert run("pr", "wake", url, "--text", line) == (0, f"{key}: sent to root@{remote} for j1")
    [argv] = posted
    assert argv[argv.index("--to") + 1] == f"root@{remote}"
    assert argv[argv.index("--text") + 1] == (
        f"STAGE {line}; no live job answers for it and its job j1 is stopped, so no wake starts it: close it, or move it to a live job with"
        f" drive pr add {url} --job <j>"
    )
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["dispatch"], bool(job.get("deaths"))) == ("stopped", DISPATCH, False)


def test_a_second_pr_wake_arriving_while_the_first_relaunches_the_dead_lane_waits_for_it_and_sends_that_worker_its_line(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    url = "https://github.com/Forge-AI/monorepo/pull/7"
    key = state.pr_key(url)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), workspace="sprite-old", prs=[url])
        data["prs"][key] = state.new_pr(url, "j1", "o1")
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead() if dispatch == DISPATCH else LIVE_WORKER)
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    sent, launches = [], []
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    launching, waiting, release = threading.Event(), threading.Event(), threading.Event()
    locked, entered = state.job_locked, itertools.count(1)

    def job_locked(name: str, job_id: str, seconds: float):
        if next(entered) == 2:
            waiting.set()
        return locked(name, job_id, seconds)

    def slow_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launches.append(brief.read_text())
        launching.set()
        assert release.wait(10)
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(state, "job_locked", job_locked)
    monkeypatch.setattr(orca, "launch", slow_launch)
    verdicts = [f"PR {key}: NOT READY: {key} go against his words: first [o1 j1]", f"PR {key}: NOT READY: {key} go against his words: second [o1 j1]"]
    codes = {}
    wakes = [threading.Thread(target=lambda text=text: codes.setdefault(text, cli.main(["pr", "wake", url, "--text", text, "--drive", remote]))) for text in verdicts]
    wakes[0].start()
    assert launching.wait(10)
    wakes[1].start()
    assert waiting.wait(10)
    release.set()
    for wake in wakes:
        wake.join(10)
    assert codes == dict.fromkeys(verdicts, 0)
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("j1: the message went to the job's next worker, relaunched on Sprite workspace-2 as dispatch dispatch-2")
    assert out[1] == f"{key}: j1 sent to dispatch dispatch-2"
    assert len(launches) == 1 and orca.brief_of(launches[0]).endswith(f"which you act on now:\n\n{verdicts[0]}")
    assert sent == [("send", "--to", "dispatch:dispatch-2", "--run", FRESH_RUN, "--subject", f"{key}: its next step is yours", "--body", verdicts[1])]


def test_a_pr_wake_to_a_finished_lane_prepares_its_sprite_again_and_starts_its_jobs_next_worker_with_the_line_in_its_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    url, above = "https://github.com/Forge-AI/monorepo/pull/55220", "https://github.com/Forge-AI/monorepo/pull/55285"
    ready = f"READY {url} {above}"
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(
            {"terminal": "term_lane", "workspace": "pool-one", "environment": "pool-one", "lane": "demo-j1"},
            brief=str(first),
            state="waiting",
            said=ready,
            report=f"{ready}\nPR: {url}; {above}",
            prs=[url, above],
        )
        data["prs"][state.pr_key(url)] = state.new_pr(url, "j1", "o1")
    line = "Forge-AI/monorepo#55220 red, owed by its builder: it conflicts with its base dev"
    launched = reprepared(monkeypatch)
    code, out = run("pr", "wake", url, "--text", line)
    assert (code, out) == (
        0,
        "j1: the message went to the job's next worker, relaunched on Sprite pool-one as dispatch dispatch-2 in a handoff"
        " carrying its brief, its branch, its last line, its report and its PRs; its lane on Sprite pool-one had finished (its"
        f" worker succeeded, and Orca sends nothing to a finished worker on a Sprite), and {REPREPARED}",
    )
    assert launched == [("demo-j1-2", "pool-one")]
    brief = orca.brief_of((state.drive_dir(remote) / "jobs" / "j1-2.md").read_text())
    assert f"Its last line: {ready}\n" in brief
    assert brief.endswith(f"which you act on now:\n\n{line}")
    assert {field: state.load(remote)["jobs"]["j1"][field] for field in ("run", "dispatch", "terminal", "workspace", "state")} == {
        "run": FRESH_RUN,
        "dispatch": "dispatch-2",
        "terminal": "term_fresh",
        "workspace": "pool-one",
        "state": "running",
    }


def test_the_watch_sends_a_jobs_held_texts_once_orca_reads_its_lane_alive(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    senders = []
    monkeypatch.setattr(watch, "send_held", lambda name, job_id: senders.append(job_id) or os.getpid())
    with state.edit(remote) as data:
        data["jobs"]["j1"]["undelivered"] = [{"seq": None, "words": "TAP Approve #12", "at": state.stamp(T0), "said": None, "request": "req-held"}]

    def down(dispatch: str) -> dict:
        raise orca.OrcaError("orca orchestration worker-show: remote_runtime_unavailable: the host did not answer")

    monkeypatch.setattr(orca, "worker", down)
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    watch.run_pass(watching, T0)
    assert senders == []
    monkeypatch.setattr(orca, "worker", lambda dispatch: LIVE_WORKER)
    watch.run_pass(watching, T0 + timedelta(minutes=1))
    watch.run_pass(watching, T0 + timedelta(minutes=2))
    assert senders == ["j1"]


def test_the_watch_has_drive_job_resume_wake_a_running_lane_orca_could_not_reach_three_reads_running(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    workspace, dispatch, port = J1409_LANES["j242"]
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(dispatch=dispatch, workspace=workspace, state="running", said="building the tallies")
    reads, woken = [], []
    monkeypatch.setattr(orca, "worker", lambda shown: reads.append(shown) or refused("remote_runtime_unavailable", f"Could not connect to the remote Orca runtime at ws://127.0.0.1:{port}")(shown))
    monkeypatch.setattr(watch, "wake", lambda name, job_id: woken.append((name, job_id)) or os.getpid())
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    said = (
        f"LANE j1 o1 unreachable on Sprite {workspace}: orca orchestration worker-show: remote_runtime_unavailable: Could not connect to the remote Orca runtime"
        f' at ws://127.0.0.1:{port}, while it worked on "building the tallies". The watch ran drive job resume j1, which wakes that Sprite and restarts the lane there,'
        f" else on a fresh Sprite, its output in the drive's {watch.RELAUNCHES_LOG}"
    )
    assert [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10, 15)] == [[], [], [said], []]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert (len(reads), woken) == (4, [(remote, "j1")])


def test_a_held_text_no_send_took_is_said_as_the_watchs_to_send(remote: str) -> None:
    held = {"seq": None, "words": "TAP Approve #12", "at": state.stamp(T0), "said": None, "request": "req-held"}
    assert watch.unreached("j1", held) == (
        f'the message "TAP Approve #12" a resume held at {render.when(held["at"])} has not reached j1; the watch sends it once Orca reads its lane alive,'
        " its next worker gets it in its handoff, and drive job resume j1 delivers it now"
    )


def test_the_watch_ends_a_waiting_jobs_dead_lane_on_a_sprite_once(remote: str, monkeypatch: pytest.MonkeyPatch, ended_lanes: list[str]) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="sprite-old", state="waiting", said="ASK which npm name?", ask="msg_q1")
    shown = []
    monkeypatch.setattr(orca, "worker", lambda dispatch: shown.append(dispatch) or dead())
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on Sprite sprite-old: its worker exited, while it waited on "ASK which npm name?". The watch ran drive job end j1, '
        f"which copies its Sprite's Orca runtime and worker logs to {orca.death_logs(remote, 'j1', DISPATCH)}, saves to GitHub what its Sprite holds"
        " on no remote and destroys it, its output in the drive's relaunches.log; "
        'drive job resume j1 "<his answer>" relaunches it on a fresh Sprite with a handoff'
    ]
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert (shown, ended_lanes) == ([DISPATCH], ["j1"])
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], [(death["dispatch"], death.get("ended")) for death in job["deaths"]]) == ("waiting", [(DISPATCH, None)])


def test_the_watch_releases_the_sprite_a_lane_died_on_once_no_unfinished_job_runs_on_it(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    released = []
    monkeypatch.setattr(state, "scratch", lambda: False)
    monkeypatch.setattr(orca, "checkout", lambda name, data: Path(remote))
    monkeypatch.setattr(orca, "holds_sprite", lambda job: job["workspace"] != "sprite-gone")
    monkeypatch.setattr(orca, "release_detached", lambda drive, job_id, job, checkout, log: released.append(job["workspace"]) or subprocess.Popen(["true"]))
    with state.edit(remote) as data:
        old = [{"dispatch": f"ctx_old{n}", "workspace": workspace, "why": "its worker exited", "at": state.stamp(T0)} for n, workspace in enumerate(["sprite-old", "sprite-gone", "sprite-now"])]
        data["jobs"]["j1"].update(state="waiting", workspace="sprite-now", deaths=old)
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0)[0].startswith(f"LANE j1 o1 died on Sprite sprite-old: {why}, while it worked on")
    assert not any(line.startswith("LANE") for line in watch.run_pass(watching, T0 + timedelta(minutes=1)))
    assert [(death["dispatch"], death["why"]) for death in state.load(remote)["jobs"]["j1"]["deaths"]] == [(DISPATCH, why)]


def test_a_running_job_whose_lane_died_is_relaunched_that_pass_once(remote: str, monkeypatch: pytest.MonkeyPatch, relaunches: list[str]) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="sprite-old", said="PUSHED https://github.com/Forge-AI/monorepo/pull/9")
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on Sprite sprite-old: its worker exited, while it worked on "PUSHED https://github.com/Forge-AI/monorepo/pull/9". '
        f"The watch ran drive job resume j1, which copies its Sprite's Orca runtime and worker logs to {orca.death_logs(remote, 'j1', DISPATCH)},"
        " resumes its Claude session on its Sprite, or else relaunches it on a fresh Sprite with a handoff, its output in the drive's relaunches.log"
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
    [line] = watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0)
    assert line.startswith('ORPHAN o1 "ship the remote builder": j1\'s lane ended 2 minutes ago (its worker exited) and the watch\'s drive job resume at ')
    assert line.endswith("started no next lane, its output in the drive's relaunches.log")


def branches(monkeypatch: pytest.MonkeyPatch, prs: dict[str, dict]) -> list[list[tuple[str, str]]]:
    asked = []
    monkeypatch.setattr(github, "checkout_repo", lambda checkout: "Forge-AI/monorepo")
    monkeypatch.setattr(github, "open_on", lambda heads: asked.append(heads) or prs)
    monkeypatch.setattr(github, "fetch", lambda keys: ({}, None))
    return asked


def test_a_finished_lane_whose_job_still_runs_is_said_once_until_its_result_is_recorded(
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [said]
    assert watch.run_pass(watching, T0 + timedelta(minutes=5)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    [line] = watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0)
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0) == said


def test_a_lane_result_opens_with_the_subject_it_was_sent_under(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "Built and opened PR #53447: the serve marks queued card answers.\nLeft: build #122630 failed on the card test"
    deliver(monkeypatch, [message("worker_done", body, subject="BLOCKED CI on PR #53447 (build #122630)")])
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    later = state.now() + timedelta(minutes=10)
    assert (watch.waits_lines(watching, later), staged(watch.agent_lines, watching, later), watch.orphan_lines(watching, later)) == ([], [], [])
    assert state.load(remote)["jobs"]["j2"]["waits"] == ["j1"]


def test_an_end_hands_its_end_to_the_newer_install_that_saved_meanwhile(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], saved_on_github: dict[str, list[str]]
) -> None:
    cloned(tmp_path, remote)
    saved_on_github["demo-j1"] = ["saved/demo-j1/restack at 1a2b3c4d5e"]
    carrying(run, remote)
    run("job", "add", "builder", "--order", "o1", "--goal", "restack onto the fix", "--remote")
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", said="WAITING CI", ask="a question", workspace="pool-one")
    handed = []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION

    def saved(drive: str, job_id: str, job: dict, root: Path) -> str:
        monkeypatch.setattr(state, "VERSION", "0.0.1")
        return RESTACK

    real = subprocess.run

    def newer(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] != str(newest):
            return real(argv, **kwargs)
        handed.append(argv[1:])
        monkeypatch.setattr(state, "VERSION", version)
        code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capsys.readouterr().out, "")

    monkeypatch.setattr(orca, "save", saved)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "destroyed pool-one dispatch ctx_lane0001")
    monkeypatch.setattr(cli.install, "upgraded", lambda: newest)
    monkeypatch.setattr(cli.install.subprocess, "run", newer)
    assert run("job", "end", "j1")[1].endswith("j1's lane ended; j1 stays waiting, and what its Sprite held on no remote is saved on GitHub as saved/demo-j1/restack at 1a2b3c4d5e")
    [argv] = handed
    assert (argv[:3], argv[4:], Path(argv[3]).exists()) == (["job", "ended", "j1"], ["--drive", remote, "--json"], False)
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["deaths"][-1]["saved"], job["deaths"][-1]["why"]) == (None, RESTACK, "ended by drive job end")


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


def test_end_of_a_job_whose_sprite_never_answers_is_refused_at_the_reclaim_bound_ending_nothing_and_leaving_nothing_running(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloned(tmp_path, remote)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", workspace="pool-mute")
    group = tmp_path / "group"
    reclaimer = tmp_path / "orca-reclaim.py"
    reclaimer.write_text(f"#!/bin/sh\necho $$ >{group}\nsleep 600 &\nwait\n")
    reclaimer.chmod(0o755)
    monkeypatch.setattr(orca, "tool", lambda name: str(reclaimer))
    monkeypatch.setattr(orca, "RECLAIM_SECONDS", 1)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: pytest.fail("a refused end reclaims nothing"))
    assert cli.main(["job", "end", "j1"]) == 2
    assert (
        "what pool-mute holds on no remote could not be saved to GitHub for its next lane:"
        " orca-reclaim.py --save ran past 1 s, so it was killed with every process it started. End it again once that Sprite answers"
    ) in capsys.readouterr().err
    assert not reads.live(int(group.read_text()))
    assert state.load(remote)["jobs"]["j1"].get("deaths", []) == []


def test_end_refuses_a_running_job_whose_lane_stopped_without_its_line_naming_resume(
    remote: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="running", said="WAITING #49869 merged; his pick on the open card lands as a follow-up PR", waits=["demo:j1-5-a"], workspace="pool-one")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: pytest.fail("a refused end saves nothing"))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: pytest.fail("a refused end reclaims nothing"))
    assert cli.main(["job", "end", "j1"]) == 2
    assert "j1: refused, nothing ended: it is running, so its lane has returned no line to end it on; drive job resume j1 sends" in capsys.readouterr().err
    assert (state.load(remote)["jobs"]["j1"]["state"], state.load(remote)["jobs"]["j1"].get("deaths", [])) == ("running", [])


def test_end_saves_what_the_sprite_of_a_job_still_at_work_holds_and_its_next_lane_gets_the_saved_branches(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved_on_github: dict[str, list[str]]
) -> None:
    cloned(tmp_path, remote)
    saved_on_github["demo-j1"] = SAVED_BRANCHES
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), state="waiting", said="WAITING CI", workspace="pool-ie7ugrhamhjtmnwa")
    saves = iter([SAVED, None])
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: next(saves))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "destroyed pool-ie7ugrhamhjtmnwa dispatch ctx_lane0001")
    assert run("job", "end", "j1") == (
        0,
        f"destroyed pool-ie7ugrhamhjtmnwa dispatch ctx_lane0001\nj1's lane ended; j1 stays waiting, and what its Sprite held on no remote is saved on GitHub as {ON_GITHUB}",
    )
    monkeypatch.setattr(orca, "worker", refused("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.update(brief=brief.read_text())
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "resume", "j1", "Build #4412 failed: fix the lint")
    assert (code, out.endswith(f"and what pool-ie7ugrhamhjtmnwa held on no remote is saved on GitHub as {ON_GITHUB}")) == (0, True)
    assert f"what it held on no remote was saved for you on GitHub as {ON_GITHUB}." in orca.brief_of(launched["brief"])
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
    monkeypatch.setattr(orca, "release", lambda drive, job_id, job, root: released.append(job["workspace"]) or "destroyed pool-one")
    assert run("job", "end", "j1") == (0, "nothing to reclaim\ndestroyed pool-one\nj1's lane ended; j1 stays done")
    assert released == ["pool-one"]

    def kept(drive: str, job_id: str, job: dict, root: Path) -> str:
        raise orca.OrcaError("orca-reclaim.py exited 1: uncommitted changes")

    monkeypatch.setattr(orca, "release", kept)
    with state.edit(remote) as data:
        data["jobs"]["j1"]["dispatch"] = "ctx_lane0002"
    assert run("job", "end", "j1") == (0, "nothing to reclaim\npool-one is kept: orca-reclaim.py exited 1: uncommitted changes\nj1's lane ended; j1 stays done")


def test_the_watch_frees_each_finished_lane_once_a_sprite_by_destroy_and_a_quiet_one_on_this_mac_by_its_dispatch_never_one_a_live_job_shares(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for goal in ("on this mac", "still at work", "its run lives on", "just finished", "stopped by hand"):
        run("job", "add", "builder", "--order", "o1", "--goal", goal, "--remote")
    quiet = state.stamp(T0 - watch.QUIET_LANE)
    ended = {"dispatch": DISPATCH, "workspace": "pool-one", "why": "ended by drive job end", "at": quiet, "ended": {"pid": 1, "at": quiet}}
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="done", workspace="pool-one", deaths=[ended])
        data["jobs"]["j6"].update(state="stopped", workspace="pool-two", run="run_two", dispatch="ctx_two")
        data["jobs"]["j2"].update(state="failed", route=state.LOCAL_ROUTE, run="run_mac", dispatch="ctx_mac", updated=quiet)
        data["jobs"]["j3"].update(state="running", route=state.LOCAL_ROUTE, run="run_live", dispatch="ctx_live", updated=quiet)
        data["jobs"]["j4"].update(state="stopped", route=state.LOCAL_ROUTE, run="run_live", dispatch="ctx_old", updated=quiet)
        data["jobs"]["j5"].update(state="done", route=state.LOCAL_ROUTE, run="run_new", dispatch="ctx_new", updated=state.stamp(T0))
    workspaces = tmp_path / "cc-remote-workspaces"
    workspaces.mkdir()
    (workspaces / "pool-one.json").write_text("{}")
    (workspaces / "pool-two.json").write_text("{}")
    monkeypatch.setattr(orca, "CC_REMOTE_WORKSPACES", workspaces)
    monkeypatch.setattr(state, "scratch", lambda: False)
    monkeypatch.setattr(orca, "checkout", lambda name, data: tmp_path)
    ends: list[str] = []
    monkeypatch.setattr(watch, "end_lane", lambda name, job_id: ends.append(job_id))
    started: list[tuple[str, ...]] = []
    finished = subprocess.CompletedProcess([], 0)
    finished.poll = lambda: 0
    monkeypatch.setattr(orca, "release_detached", lambda drive, job_id, job, root, log: started.append(("destroy", job["workspace"])) or finished)
    monkeypatch.setattr(orca, "reclaim_detached", lambda run_id, dispatch, root, log: started.append(("done", run_id, dispatch)) or finished)
    watcher = watch.Watcher(remote, offset=state.events_end(remote))
    watch.release_lanes(watcher, T0)
    watch.release_lanes(watcher, T0)
    assert (started, ends) == ([("destroy", "pool-one"), ("done", "run_mac", "ctx_mac")], ["j6"])


def test_end_of_a_finished_job_never_reads_its_sprite_and_keeps_the_parts_it_carried(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    parts = carrying(run, remote)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="stopped", workspace="pool-one")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: pytest.fail("a finished job's lane ends whatever its Sprite holds"))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "")
    assert run("job", "end", "j1") == (0, "j1's lane ended; j1 stays stopped")
    data = state.load(remote)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["carries"], data["orders"]["o2"]["jobs"]) == ("stopped", {"o2": parts}, ["j1"])


GONE_SPRITE = "Orca no longer holds its Sprite's environment"


def test_a_lane_drive_job_end_is_destroying_is_never_read_as_dead_or_ended_a_second_time(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ended_lanes: list[str]
) -> None:
    cloned(tmp_path, remote)
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", said="READY https://github.com/Forge-AI/monorepo/pull/55192", workspace="pool-4yf2z2cdrlsrofv5")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: SAVED)
    monkeypatch.setattr(orca, "worker", refused("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4"))
    watched = []

    def destroy(run_id: str, dispatch: str, root: Path) -> str:
        watching = watch.Watcher(remote, offset=state.events_end(remote))
        watched.append(staged(watch.agent_lines, watching, T0) + watch.death_lines(remote, {"j1": (DISPATCH, GONE_SPRITE)}, T0))
        return "destroyed pool-4yf2z2cdrlsrofv5 dispatch ctx_lane0001"

    monkeypatch.setattr(orca, "reclaim", destroy)
    assert run("job", "end", "j1")[0] == 0
    assert (watched, ended_lanes) == ([[]], [])
    [death] = state.load(remote)["jobs"]["j1"]["deaths"]
    assert (death["why"], death["ended"]["pid"], death["saved"]) == ("ended by drive job end", os.getpid(), SAVED)
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: pytest.fail("an end of an ended lane saves nothing"))
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: pytest.fail("an end of an ended lane reclaims nothing"))
    assert run("job", "end", "j1") == (0, f"j1's lane was already ended by the drive job end of {death['ended']['at']}, so nothing changed; j1 stays waiting")
    assert state.load(remote)["jobs"]["j1"]["deaths"] == [death]


def test_an_end_whose_save_is_refused_leaves_the_watchs_record_of_the_lanes_death_and_runs_again(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    died = {"dispatch": DISPATCH, "workspace": "pool-sleeping", "why": GONE_SPRITE, "at": state.stamp(T0)}
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", workspace="pool-sleeping", deaths=[died])
    monkeypatch.setattr(orca, "save", unsaveable(UNREAD))
    assert run("job", "end", "j1")[0] == 2
    assert state.load(remote)["jobs"]["j1"]["deaths"] == [died]
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "destroyed pool-sleeping dispatch ctx_lane0001")
    assert run("job", "end", "j1") == (0, "destroyed pool-sleeping dispatch ctx_lane0001\nj1's lane ended; j1 stays waiting")


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
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


J2363_SAVE = (
    "uncommitted changes in /home/sprite/monorepo (1 file), now on Forge-AI/monorepo as Uploading at LFS at objects: at 100% at (13/13),"
    " at 87 at MB at | at 0 at B/s, at done., saved/durable-agents-j2363/sandsql-iris-none-reads at 241f7a7611"
)
J2363_REFS = [
    {
        "ref": "refs/heads/saved/durable-agents-j2363/sandsql-iris-none-reads",
        "object": {"sha": "241f7a761105034c55d68ad9ab5903ddf3492bf3", "type": "commit"},
    }
]


def test_a_resume_names_the_saved_branches_github_holds_never_the_garbled_save_an_end_kept(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): ship it\nDrive: demo, order o1, job j1", None))
    kept = {
        "dispatch": DISPATCH,
        "workspace": "pool-fhbxfis6temcixty",
        "why": "ended by drive job end",
        "at": "2026-11-12T18:19:01-08:00",
        "ended": {"pid": ENDED_READER, "at": "2026-11-12T18:19:01-08:00"},
        "saved": J2363_SAVE,
    }
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), state="waiting", lane="durable-agents-j2363", workspace="pool-fhbxfis6temcixty", deaths=[kept])
    asked = []

    def matching_refs(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        asked.append(cmd[2].partition("/git/")[2])
        return json.dumps(J2363_REFS)

    monkeypatch.setattr(github, "saved_branches", REAL_SAVED_BRANCHES)
    monkeypatch.setattr(github, "read", matching_refs)
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "worker", refused("invalid_argument", "Unknown environment: f0b7755c-d270-48fa-88c3-8ad678a1c2e4"))
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: FRESH_RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        launched.update(brief=brief.read_text())
        return {field: f"{field}-2" for field in ("dispatch", "task", "terminal", "workspace", "environment", "lane")}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "resume", "j1", "the stale reads fix merged: carry it on")
    saved = "saved/durable-agents-j2363/sandsql-iris-none-reads at 241f7a7611"
    brief = orca.brief_of(launched["brief"])
    assert (code, out.endswith(f"and what pool-fhbxfis6temcixty held on no remote is saved on GitHub as {saved}")) == (0, True)
    assert f"what it held on no remote was saved for you on GitHub as {saved}. Fetch each saved branch and carry its work on." in brief
    assert ("Uploading" in out, "Uploading" in brief, asked) == (False, False, ["matching-refs/heads/saved/durable-agents-j2363/"])
    assert state.load(remote)["jobs"]["j1"]["deaths"] == [kept]


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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [
        "JOB j1 o1 Which fixture keeps the hold test deterministic?"
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == ("msg_question", "waiting", job["report"].splitlines()[0])
    assert job["report"].endswith("is red on it")


@pytest.mark.parametrize(
    ("question", "whole"),
    [
        ("Which fixture keeps the hold test deterministic?\nA: freeze state.now in conftest\nB: pass T0 through run_pass", True),
        ("Which fixture keeps the hold test deterministic?\nLOG https://buildkite.com/forge/test/builds/1 is red on it", True),
        ("Which fixture keeps the hold test deterministic?", False),
    ],
)
def test_a_lane_questions_job_line_posts_the_question_whole_as_its_records_body(question: str, whole: bool, remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", question)])
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    [line] = watch.run_pass(watching, T0)
    posted = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout, stdin=None: posted.append((argv[argv.index("--topic") + 2 :], stdin)) or "")
    assert watch.sent(watching, "root", [line], T0) == []
    assert posted == [([*(["--path", "-"] if whole else []), "--text", line], question if whole else None)]


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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == []
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"], job["prs"]) == (None, "waiting", f"PUSHED {url}", [url])
    required = {"context": "buildkite/test", "state": "SUCCESS", "targetUrl": "https://buildkite.com/forge/monorepo/builds/7"}
    pr_reads["Forge-AI/monorepo#7"] = {
        "state": "OPEN", "isDraft": False, "headRefOid": "abc1234", "baseRefName": "dev", "headRefName": "fix-x", "mergeCommit": None, "checks": [required], "reviewDecision": None,
        "approvals": [], "mergeable": "MERGEABLE", "title": "Fix: x", "author": "yasyf", "labels": [], "ejected_at": None, "native": [], "required_checks": ["buildkite/test"], "required_reviews": 1,
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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


@pytest.mark.parametrize("said", ["READ cci show 2065", "j1 READ cci show 2065\nfor the field facts"])
def test_the_watch_answers_a_read_question_itself_and_keeps_the_job_as_it_was(
    said: str, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", said, id="msg_read")])
    asked, replies = [], []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: asked.append((question, root, drive, job_id)) or "m2065 the field facts")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == []
    assert asked == [("READ cci show 2065", tmp_path, remote, "j1")]
    assert replies == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_read", "--run", RUN, "--body", "m2065 the field facts"),
        ("check", "--run", RUN),
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == (None, "running", None)


def test_a_log_question_a_lane_on_the_old_builder_rules_asks_gets_the_rule_back_with_no_job_line(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    question = "LOG https://buildkite.com/forge/test/builds/129091"
    deliver(monkeypatch, [message("question", question, id="msg_log")])
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == []
    assert [args[:5] for args in replies if args[0] == "reply"] == [("reply", "--id", "msg_log", "--run", RUN)]
    assert "tools/ci bk log <pipeline> <build> <job>" in next(args[-1] for args in replies if args[0] == "reply")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"], job["reads"]) == (None, "running", None, {})


def test_a_read_that_cannot_be_answered_tells_its_lane_why_and_says_so(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    question = "READ cci show 2065"
    deliver(monkeypatch, [message("question", question, id="msg_read")])

    def refuse(question: str, root: Path, drive: str, job_id: str) -> str:
        raise reads.ReadError("cci show 2065 exited 1: no record 2065")

    monkeypatch.setattr(reads, "answer", refuse)
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    why = f"{question}: cci show 2065 exited 1: no record 2065"
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [f"JOB j1 o1 READ unanswered, its lane was told why: {why}"]
    assert ("reply", "--id", "msg_read", "--run", RUN, "--body", why) in replies
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []


def test_a_read_answer_orca_refuses_is_retried_until_it_goes_through_and_said_from_the_third_refusal(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", "READ cci show 2065", id="msg_read")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "m2065")
    replies, refusals = [], iter(range(3))

    def fenced_three_times(*args: str) -> dict:
        if args[0] == "reply" and next(refusals, None) is not None:
            raise orca.OrcaError("orca orchestration reply: consumer_fenced: This caller is bound to Run run_other")
        replies.append(args)
        return {}

    monkeypatch.setattr(orca, "call", fenced_three_times)
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    passes = [watch.run_pass(watching, T0 + timedelta(seconds=s)) for s in (0, 5, 10)]
    assert passes == [
        [],
        [],
        ["WATCH the answer to j1's read failed 3 times in a row: orca orchestration reply: consumer_fenced: This caller is bound to Run run_other"],
    ]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {"msg_read": "READ cci show 2065"}
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []
    assert replies[-2:] == [("reply", "--id", "msg_read", "--run", RUN, "--body", "m2065"), ("check", "--run", RUN)]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}


def test_a_read_someone_already_answered_is_dropped_with_no_line(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", "READ ccn answer search x", id="relay_6779eb1e29a6")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "x")
    replies = []
    monkeypatch.setattr(orca, "call", answered_elsewhere(replies))
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == []
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []
    assert [args for args in replies if args[0] == "reply"] == [
        ("reply", "--id", "relay_6779eb1e29a6", "--run", RUN, "--body", "x")
    ]


def test_a_read_still_running_when_job_end_ended_its_lane_is_dropped_with_no_line(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    deliver(monkeypatch, [message("question", "READ cci show 2065", id="relay_4d6bfb09372e")])
    monkeypatch.setattr(watch, "spawn_reader", lambda name, job_id, message_id: os.getpid())
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: "m2065")
    replies = []
    closed = f"Dispatch {DISPATCH} finished on pool-one, which cannot take follow-ups for finished workers yet."
    monkeypatch.setattr(orca, "call", reply_refused(replies, "dispatch_inactive", closed))
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert staged(watch.lane_lines, watching, T0) == []
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="done", workspace="pool-one")
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "")
    assert run("job", "end", "j1") == (0, "j1's lane ended; j1 stays done")
    watch.answer_read(remote, "j1", "relay_4d6bfb09372e")
    assert [staged(watch.lane_lines, watching, T0 + timedelta(seconds=s)) for s in (5, 10, 70)] == [[], [], []]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}
    assert [args for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_4d6bfb09372e", "--run", RUN, "--body", "m2065")]


LONG_READ = 'DRIVE job replies j1\n{"files": {}}'
LONG_ANSWER = "drive exited 0\n" + 'a "quoted" line\n' * 15_000


def sprite_refuses(workspace: str, folder: str, name: str, text: bytes) -> str:
    raise orca.Unfit(f"writing {folder}/{name} onto {workspace} exited 255: Connection closed")


def test_an_answer_past_one_orca_relay_message_keeps_what_fits_over_a_line_naming_the_cut_and_the_question_that_gets_it_whole() -> None:
    cut = orca.relayable(LONG_ANSWER, LONG_READ)
    relayed = len(json.dumps({"questionId": "relay_4ee9b39cd0c6", "answerMessageId": "msg_" + "0" * 32, "body": cut}).encode())
    assert orca.RELAY_BYTES - 2 * orca.RELAY_ROOM < relayed <= orca.RELAY_BYTES
    head, _, closing = cut.rstrip("\n").rpartition("\n")
    assert LONG_ANSWER.startswith(head)
    assert closing == (
        f"[cut {len(LONG_ANSWER) - len(head):,} of {len(LONG_ANSWER):,} bytes, past the 64 KiB one Orca relay message carries:"
        " ask `drive job replies j1` again and its whole answer comes as a file on this Sprite]"
    )
    assert orca.relayable("drive exited 0\nshort", LONG_READ) == "drive exited 0\nshort"


def test_a_read_answer_kept_whole_past_one_orca_relay_message_goes_cut_and_leaves_the_queue(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"]["workspace"] = "pool-one"
    deliver(monkeypatch, [message("question", LONG_READ, id="relay_long")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: LONG_ANSWER)
    monkeypatch.setattr(orca, "put_on_sprite", sprite_refuses)
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == []
    assert [args for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_long", "--run", RUN, "--body", orca.relayable(LONG_ANSWER, LONG_READ))]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}


@pytest.mark.parametrize(
    ("command", "size"),
    [
        ("cc-slack thread --url https://demo.slack.com/archives/D0DEMO00001/p1794033848123456 --raw", 13_040),
        ("cc-slack history --channel D0DEMO00001 --limit 20 --raw", 109_486),
    ],
    ids=["a question card with its blocks", "a DM's newest 20 posts with their blocks"],
)
def test_a_slack_read_a_sprite_lane_asks_runs_this_macs_cc_slack_and_reaches_its_sprite_whole_past_the_read_budget(
    command: str, size: int, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed = '{"blocks": "' + "b" * (size - 14) + '"}'
    ran, placed, replies = [], [], []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: ran.append(argv) or f"{printed}\n")
    monkeypatch.setattr(orca, "put_on_sprite", lambda workspace, folder, name, text: placed.append((workspace, text)) or f"/home/sprite/{folder}/{name}")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="pool-one")
    deliver(monkeypatch, [message("question", f"j1 READ {command}", id="relay_slack")])
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    assert ran == [["cc-slack", *command.split()[1:]]]
    assert len(printed.encode()) == size > reads.BUDGET
    assert placed == [("pool-one", printed.encode())]
    kept = f"{orca.KEPT_ANSWER}/home/sprite/{orca.SPRITE_ANSWERS}/relay_slack.txt ({size} bytes, SHA-256 {hashlib.sha256(printed.encode()).hexdigest()}); read it in full\n"
    assert [args for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_slack", "--run", RUN, "--body", kept)]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}


def test_a_read_whose_answer_orca_kept_too_big_to_relay_gets_it_cut_by_message_and_says_so_once(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"]["workspace"] = "pool-one"
    deliver(monkeypatch, [message("question", LONG_READ, id="relay_long")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: LONG_ANSWER)
    monkeypatch.setattr(orca, "put_on_sprite", sprite_refuses)
    calls = []
    monkeypatch.setattr(orca, "call", answered_elsewhere(calls))
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [
        f"JOB j1 o1 READ answered by message, since Orca kept its {len(LONG_ANSWER):,}-byte answer as its question's and never relayed it: sent to dispatch {DISPATCH}"
    ]
    cut = orca.relayable(LONG_ANSWER, LONG_READ)
    assert [args for args in calls if args[0] in ("reply", "send")] == [
        ("reply", "--id", "relay_long", "--run", RUN, "--body", cut),
        ("send", "--to", f"dispatch:{DISPATCH}", "--run", RUN, "--subject", "The answer to your DRIVE job replies j1", "--body", cut),
    ]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []


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
        watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0)
    replies = []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: pytest.fail(f"the next watch ran {question} again"))
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0 + timedelta(minutes=5)) == [
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    for seconds in (0, 5):
        watch.run_pass(watching, T0 + timedelta(seconds=seconds))
        assert state.load(remote)["jobs"]["j1"]["read_answers"] == {"msg_glass": ["DROP TABLE", None]}
    assert watch.run_pass(watching, T0 + timedelta(seconds=10)) == []
    assert ran == [BREAK_GLASS_READ]
    assert ("reply", "--id", "msg_glass", "--run", RUN, "--body", "DROP TABLE") in replies
    job = state.load(remote)["jobs"]["j1"]
    assert (job["reads"], job["read_answers"]) == ({}, {})


@pytest.mark.parametrize("said", ["READ ccn answer show 5299250c9a85", "LOG https://buildkite.com/forge/test/builds/129091"])
def test_an_open_question_kept_before_the_watch_read_it_as_a_read_is_answered_and_its_lane_runs_again(
    said: str, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="relay_kept", state="waiting", said=said, report=f"{said}\n#53313's two reds are fixed at 29d4d04e")
    asked, replies = [], []
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: asked.append(question) or "FAIL hold_test.go:41")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    assert asked == [said]
    assert ("reply", "--id", "relay_kept", "--run", RUN, "--body", "FAIL hold_test.go:41") in replies
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["reads"]) == (None, "running", {})


def test_an_ask_naming_the_block_drive_job_ask_kept_and_posted_wakes_the_coordinator_with_its_file(
    remote: str, monkeypatch: pytest.MonkeyPatch, drawing: Drawing, asks_sent: list[dict]
) -> None:
    kept = cli.keep_ask(remote, "j1", ASK_IN_SHAPE, None)[0]
    deliver(monkeypatch, [message("question", f"ASK {kept}", id="msg_ask")])
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"the watch replied {args}"))
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0) == [f"JOB j1 o1 ASK {kept}"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], [(question["file"], question["waits"]) for question in job["questions"]]) == ("msg_ask", "waiting", [(str(kept), f"{remote}:j1-7")])
    assert [(sent["kind"], sent["to"], sent["attr"]["ask"]) for sent in asks_sent] == [("ask", [f"communicator@{remote}"], str(kept))]


@pytest.mark.parametrize("sent", [ASK_IN_SHAPE, "ASK /home/sprite/scratch/j1/ask.md"])
def test_an_ask_no_drive_job_ask_kept_goes_to_the_coordinator_saying_drive_job_ask_keeps_it_and_keeps_no_file(
    sent: str, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", sent, id="msg_ask")])
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"the watch replied {args}"))
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0) == [
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
    watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote)), T0)
    job = state.load(remote)["jobs"]["j1"]
    assert watch.unanswered("j1", job, T0 + timedelta(minutes=31)) == (
        f'j1\'s lane asked "ASK Where should the nightly store copy run?" 31 minutes ago, an ASK only his card answers: {watch.UNKEPT_ASK.format(job="j1")}'
    )
    kept = cli.keep_ask(remote, "j1", ASK_IN_SHAPE, None)[0]
    job = state.load(remote)["jobs"]["j1"] | {"report": f"ASK {kept}"}
    assert watch.UNKEPT_ASK.format(job="j1") not in (watch.unanswered("j1", job, T0 + timedelta(minutes=31)) or "")


def test_a_lane_question_nothing_answers_is_an_orphan_once_as_its_minutes_grow_whatever_older_card_its_job_has(
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 Which fixture keeps the hold test deterministic?"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=29)) == []
    said = (
        'ORPHAN o1 "ship the remote builder": j1\'s lane asked "Which fixture keeps the hold test deterministic?" {} minutes ago'
        ' and nothing answered it: drive job resume j1 "<the answer>" answers it'
    )
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == [said.format(30)]
    assert watch.run_pass(watching, T0 + timedelta(minutes=45)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == []
    cards((older_card, older), (newer_card, newer))
    with state.edit(remote) as data:
        data["jobs"]["j1"]["questions"].append(state.new_question(None, "Which fixture?", state.stamp(newer), card=newer_card))
    assert watch.run_pass(watching, T0 + timedelta(minutes=95)) == []
    assert state.load(remote)["orders"]["o1"]["parts"]["ship the remote builder"]["orphan_said"] is None


@pytest.mark.parametrize(
    ("blocks", "pointer", "parts"),
    [
        ([ASK_IN_SHAPE, SECOND_ASK], True, ["ship the remote builder"]),
        ([ASK_IN_SHAPE], True, ["ship the remote builder"]),
        ([ASK_IN_SHAPE], True, ["ship the remote builder", "the root fix"]),
        ([ASK_IN_SHAPE], False, ["ship the remote builder"]),
    ],
    ids=["j2329-two-paths", "j2310-one-path", "j2303-two-parts", "j2857-said-question"],
)
def test_a_lane_whose_kept_questions_are_each_open_on_his_card_is_no_orphan_however_long_before_its_lane_asked_they_were_kept(
    blocks: list[str], pointer: bool, parts: list[str], remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cards = [f"https://acme.slack.com/archives/D123/p179455026167295{index}" for index in range(len(blocks))]
    kept = []
    for block, at in zip(blocks, [T0 - timedelta(minutes=6), T0 - timedelta(minutes=2)][-len(blocks) :]):
        with monkeypatch.context() as clock:
            clock.setattr(state, "now", lambda at=at: at)
            kept.append(cli.keep_ask(remote, "j1", block, None)[0])
    with state.edit(remote) as data:
        for question, url in zip(data["jobs"]["j1"]["questions"], cards):
            question["card"] = url
        for part in parts[1:]:
            data["orders"]["o1"]["parts"][part] = state.new_part()
            data["jobs"]["j1"]["parts"].append(part)
    asked = f"ASK {' '.join(map(str, kept))}" if pointer else "Where should the nightly store copy run?"
    deliver(monkeypatch, [message("question", asked, id="relay_ask")])
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == [f"JOB j1 o1 {asked}"]
    assert watch.run_pass(watching, T0 + timedelta(minutes=30)) == []
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == []
    with state.edit(remote) as data:
        data["jobs"]["j1"]["questions"][0]["closed"] = state.new_close("answered", "On the primary", cards[0])
    assert watch.unanswered("j1", state.load(remote)["jobs"]["j1"], T0 + timedelta(minutes=61)).startswith("j1's lane asked")


def test_drive_job_ask_with_no_file_keeps_each_block_its_lanes_pointer_names_and_never_reads_the_pointer_as_a_block(
    run: Run, remote: str, capsys: pytest.CaptureFixture[str]
) -> None:
    kept = [cli.keep_ask(remote, "j1", block, None)[0] for block in (ASK_IN_SHAPE, SECOND_ASK)]
    with state.edit(remote) as data:
        data["jobs"]["j1"]["report"] = f"ASK {kept[0]} {kept[1]}"
        data["jobs"]["j1"]["questions"][0]["card"] = "https://acme.slack.com/archives/D123/p1794550261672959"
    assert run("job", "ask", "j1") == (0, f"{kept[0]}\n{kept[1]}")
    assert [question["file"] for question in state.load(remote)["jobs"]["j1"]["questions"]] == [str(kept[0]), str(kept[1])]
    with state.edit(remote) as data:
        data["jobs"]["j1"]["report"] = "ASK /home/sprite/scratch/j1/ask.md"
    assert cli.main(["job", "ask", "j1"]) == 2
    assert capsys.readouterr().err == "drive: no ASK block file at /home/sprite/scratch/j1/ask.md\n"


def test_every_worker_on_a_jobs_own_run_is_heard_one_it_never_recorded_included_and_one_it_replaced_only_kept(
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
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: pytest.fail(f"a replaced worker's read is never run: {question}"))
    calls = []
    monkeypatch.setattr(orca, "call", lambda *args: calls.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert staged(watch.lane_lines, watching, T0) == []
    assert [args for args in calls if args[0] == "reply"] == []
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["said"], job["report"], job["prs"]) == ("waiting", f"PUSHED {pr}", f"READY {pr}\nThis rewrote Show me", [pr])
    assert [reply["id"] for reply in state.replies(remote, "j1")] == ["msg_read", "msg_unrecorded", "msg_newest"]


def j2914_mail() -> list[dict]:
    """j2914's worker's mail from its first `drive job report j2914 -` to its worker_done, with the ids, times and order Orca kept, its job here j1."""
    sent = f"dispatch:{J2914_WORKER}"
    defect = f"{J2914_REPORT}DEFECT: a line it added after reading its report back\n"
    return [
        message("question", f"DRIVE job report j1 -\n{json.dumps({'files': {}, 'stdin': J2914_REPORT})}", id="relay_50e96f85384c", from_handle=sent, sequence=87362, created_at="2026-11-13T07:14:38Z"),
        message("question", f"DRIVE job report j1\n{json.dumps({'files': {}})}", id="relay_552a38613472", from_handle=sent, sequence=87389, created_at="2026-11-13T07:16:41Z"),
        message("question", f"DRIVE job report j1 -\n{json.dumps({'files': {}, 'stdin': defect})}", id="relay_228e13948c40", from_handle=sent, sequence=87419, created_at="2026-11-13T07:19:46Z"),
        message("worker_done", defect, subject=J2914_BUILT, id="relay_ede94f1bfec2", from_handle=sent, sequence=87432, created_at="2026-11-13T07:21:24Z"),
    ]


def j2914_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer j2914's reads as the drive does: `drive job report j1 -` keeps its stdin as the job's report and takes its BUILT line, a bare `drive job report j1` prints the report, and `drive pr add` prints the PR; the first line of each read run, in order."""
    ran = []

    def answer(question: str, root: Path, drive: str, job_id: str) -> str:
        line, _, sent = question.partition("\n")
        ran.append(line)
        if line == "DRIVE job report j1 -":
            whole = json.loads(sent)["stdin"]
            state.keep_report(drive, job_id, J2914_BUILT, whole)
            with state.edit(drive) as data:
                state.take_result(data, job_id, J2914_BUILT, whole)
            return reads.lane_exit(0, f"kept {job_id}'s report: {J2914_BUILT}")
        if line == "DRIVE job report j1":
            return reads.lane_exit(0, state.report_text(drive, job_id))
        return reads.lane_exit(0, "yasyf/demo#162")

    monkeypatch.setattr(reads, "answer", answer)
    return ran


def test_a_lane_whose_own_report_took_its_job_built_is_heard_until_its_worker_ends_its_turn(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(dispatch=J2914_WORKER, turn_open=True)
    mail = j2914_mail()
    calls = deliver(monkeypatch, *([sent] for sent in mail))
    ran = j2914_reads(monkeypatch)
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    woken = [staged(watch.lane_lines, watching, T0 + timedelta(seconds=5 * n)) for n in range(len(mail) + 1)]
    assert woken == [[f"JOB j1 o1 {J2914_BUILT}"], [], [f"JOB j1 o1 {J2914_BUILT}"], [], []]
    assert ran == ["DRIVE job report j1 -", "DRIVE job report j1", "DRIVE job report j1 -"]
    assert [args[2] for args in replies if args[0] == "reply"] == [sent["id"] for sent in mail[:3]]
    assert calls == [RUN] * len(mail)
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["turn_open"]) == ("built", False)


def test_a_built_job_relaunched_on_his_answer_runs_none_of_the_mail_its_finished_worker_left_unread_and_answers_its_next_worker(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(remote, "j1", "Order (his words): prove it\nDrive: demo, order o1, job j1", None))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(brief=str(first), dispatch=J2914_WORKER, turn_open=True, terminal="term_lane", route=state.LOCAL_ROUTE, workspace=None, environment=None, lane="demo-j1")
    kept, *unread = j2914_mail()
    added = message(
        "question",
        f"DRIVE pr add https://github.com/yasyf/demo/pull/162 --job j1\n{json.dumps({'files': {}})}",
        id="relay_66f02d393ede",
        from_handle=f"dispatch:{J2914_NEXT_WORKER}",
        sequence=87622,
        created_at="2026-11-13T07:49:40Z",
    )
    deliver(monkeypatch, [kept], [*unread, added])
    ran = j2914_reads(monkeypatch)
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert staged(watch.lane_lines, watching, T0) == [f"JOB j1 o1 {J2914_BUILT}"]
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    receipt = {"ok": True, "result": {"state": "ready", "runId": RUN, "taskId": "task_2", "dispatchId": J2914_NEXT_WORKER}}
    monkeypatch.setattr(orca, "bound_command", lambda drive, run_id, argv: subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt), stderr=""))
    code, out = run("job", "resume", "j1", "His answer: CI shoots both builds: dev and the change")
    assert (code, out.startswith(f"j1: the message went to the job's next worker, started in its terminal on this Mac as dispatch {J2914_NEXT_WORKER}")) == (0, True)
    assert staged(watch.lane_lines, watching, T0 + timedelta(minutes=27)) == []
    assert ran == ["DRIVE job report j1 -", "DRIVE pr add https://github.com/yasyf/demo/pull/162 --job j1"]
    assert [args[2] for args in replies if args[0] == "reply"] == [kept["id"], added["id"]]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["state"], job["dispatch"], job["turn_open"], [death["dispatch"] for death in job["deaths"]]) == ("running", J2914_NEXT_WORKER, True, [J2914_WORKER])
    history = state.replies(remote, "j1")
    assert [reply["id"] for reply in history if not reply["id"].startswith(state.REPORT_REPLY)] == [kept["id"], *(sent["id"] for sent in unread), added["id"]]
    assert [reply["body"] for reply in history if reply["id"].startswith(state.REPORT_REPLY)] == [J2914_REPORT]


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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 STOPPED without a report, last output: pushed the fix"]


def test_mail_is_read_only_while_a_remote_lane_is_live(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = deliver(monkeypatch)
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 READY first", "JOB j2 o1 READY second"]
    assert mailboxes == {}


def test_a_run_a_finished_job_shares_gives_the_live_job_only_its_own_workers_mail(run: Run, remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("job", "add", "builder", "--order", "o1", "--goal", "an earlier lane on the drive's old shared Run", "--remote")
    with state.edit(remote) as data:
        data["jobs"]["j2"].update(run=RUN, dispatch="ctx_lane0002", state="done")
    deliver(monkeypatch, [message("worker_done", "DONE earlier", id="msg_old", from_handle="dispatch:ctx_lane0002"), message("worker_done", "BLOCKED CI", id="msg_own")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == ["JOB j1 o1 BLOCKED CI"]
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
    top = watch.Watcher(remote, offset=state.events_end(remote))
    iris = watch.Watcher(remote, offset=state.events_end(remote), project="Iris")
    assert staged(watch.lane_lines, top, T0) == ["JOB j1 o1 BLOCKED remote builder"]
    assert staged(watch.lane_lines, iris, T0) == ["JOB j2 o2 BLOCKED run page"]
    assert (staged(watch.lane_lines, top, T0), staged(watch.lane_lines, iris, T0)) == ([], [])
    jobs = state.load(remote)["jobs"]
    assert (jobs["j1"]["mail_seen"], jobs["j2"]["mail_seen"]) == (second["sequence"], first["sequence"])
    assert [reply["id"] for job_id in ("j1", "j2") for reply in state.replies(remote, job_id)] == ["msg_top", "msg_iris"]


def test_a_lane_whose_job_carries_the_drives_order_wakes_only_the_watch_hearing_its_own_order_with_its_report_its_stop_and_its_death(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch, ended_lanes: list[str]
) -> None:
    carrying(run, remote)
    with state.edit(remote) as data:
        data["orders"]["o1"]["project"] = "Iris"
        data["jobs"]["j1"]["workspace"] = "sprite-old"
        cli.bind_subcoordinator(data, "Iris", {"session": "iris-1", "terminal": None, "cwd": "/work", "started": state.stamp(T0)})
    ready = "READY https://github.com/Forge-AI/monorepo/pull/8"
    stopped = "Worker term_1 ended its turn without sending worker_done.\nLast output:\nkept j1's report\nFull output: x"
    deliver(
        monkeypatch,
        [message("question", f"DRIVE job report j1 -\n{json.dumps({'stdin': ready})}", id="relay_report")],
        [message("worker_done", ready), message("escalation", stopped, subject="Worker stopped without reporting")],
    )

    def report(question: str, root: Path, drive: str, job_id: str) -> str:
        state.keep_report(drive, job_id, ready, ready)
        with state.edit(drive) as data:
            state.take_result(data, job_id, ready, ready)
        return reads.lane_exit(0, str(state.replies_path(drive, job_id)))

    monkeypatch.setattr(reads, "answer", report)
    monkeypatch.setattr(orca, "call", lambda *args: {})
    top = watch.Watcher(remote, offset=state.events_end(remote))
    iris = watch.Watcher(remote, offset=state.events_end(remote), project="Iris")
    assert (staged(watch.lane_lines, top, T0), staged(watch.lane_lines, iris, T0)) == ([], [f"JOB j1 o1 {ready}"])
    assert (staged(watch.lane_lines, top, T0), staged(watch.lane_lines, iris, T0)) == ([], ["JOB j1 o1 STOPPED without a report, last output: kept j1's report"])
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    assert staged(watch.agent_lines, top, T0) == []
    assert staged(watch.agent_lines, iris, T0)[0].startswith(f'LANE j1 o1 died on Sprite sprite-old: its worker exited, while it waited on "{ready}"')
    assert ended_lanes == ["j1"]


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
    top = watch.Watcher(remote, offset=state.events_end(remote))
    iris = watch.Watcher(remote, offset=state.events_end(remote), project="Iris")
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
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == [
        f"JOB j1 o1 PUSHED {url}; the drive tracks no {url}: drive pr add <url> --job j1 registers each"
    ]
    assert state.load(remote)["prs"] == {}


@pytest.mark.parametrize(("line", "woken"), [("DONE merged and installed", ["JOB j1 o1 DONE merged and installed"]), ("PUSHED https://github.com/Forge-AI/monorepo/pull/8", [])])
def test_a_lanes_drive_job_report_wakes_the_coordinator_with_its_line_as_its_answer_goes_whatever_state_it_set(
    line: str, woken: list[str], remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", f"DRIVE job report j1 -\n{json.dumps({'stdin': line})}", id="relay_report")])

    def report(question: str, root: Path, drive: str, job_id: str) -> str:
        state.keep_report(drive, job_id, line, line)
        with state.edit(drive) as data:
            state.take_result(data, job_id, line, line)
        return reads.lane_exit(0, str(state.replies_path(drive, job_id)))

    replies = []
    monkeypatch.setattr(reads, "answer", report)
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == woken
    assert [args[:3] for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_report")]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}


def test_a_lane_that_reads_its_own_report_wakes_nobody_and_its_relaunched_job_stays_running(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting = "WAITING https://github.com/Forge-AI/monorepo/pull/8 lands first"
    state.keep_report(remote, "j1", waiting, f"{waiting}\nWAITS: the PR below\n")
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(said=waiting, state="running")
    deliver(monkeypatch, [message("question", f"DRIVE job report j1\n{json.dumps({'files': {}})}", id="relay_read")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: reads.lane_exit(0, state.report_text(drive, job_id)))
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    assert [args[:3] for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_read")]
    assert state.load(remote)["jobs"]["j1"]["state"] == "running"


def test_a_lanes_drive_job_report_that_left_its_job_no_line_wakes_nobody_and_its_answer_goes(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", f"DRIVE job report j1 -\n{json.dumps({'stdin': 'x'})}", id="relay_report")])
    monkeypatch.setattr(reads, "answer", lambda question, root, drive, job_id: reads.lane_exit(0, str(state.replies_path(drive, job_id))))
    replies = []
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert state.load(remote)["jobs"]["j1"]["said"] is None
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    assert [args[:3] for args in replies if args[0] == "reply"] == [("reply", "--id", "relay_report")]


def test_a_report_drive_job_report_took_is_not_taken_again_when_its_lane_sends_it(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/Forge-AI/monorepo/pull/8"
    report = f"PUSHED {url}\nPR: {url}\nCHANGE: x\n"
    with state.edit(remote) as data:
        state.keep_report(remote, "j1", f"PUSHED {url}", report)
        state.take_result(data, "j1", f"PUSHED {url}", report)
    taken = state.load(remote)["jobs"]["j1"]
    deliver(monkeypatch, [message("worker_done", report, id="msg_report")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    job, prs = state.load(remote)["jobs"]["j1"], state.load(remote)["prs"]
    assert ({key: job[key] for key in ("said", "state", "waits", "returned")}, prs) == ({key: taken[key] for key in ("said", "state", "waits", "returned")}, {})
    assert [reply["id"].startswith(state.REPORT_REPLY) for reply in state.replies(remote, "j1")] == [True, False]


def test_the_worker_done_ending_a_kept_reports_turn_is_never_taken_again_whatever_its_body(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    pushed = "PUSHED https://github.com/Forge-AI/monorepo/pull/55192 https://github.com/Forge-AI/monorepo/pull/55596"
    report = f"{pushed}\nPR: https://github.com/Forge-AI/monorepo/pull/55192\nCHANGE: #55192 is replayed onto the rebased base\n"
    with state.edit(remote) as data:
        state.keep_report(remote, "j1", pushed, report)
        state.take_result(data, "j1", pushed, report)
        state.take_result(data, "j1", "READY" + pushed.removeprefix("PUSHED"), report)
    settled = state.load(remote)["jobs"]["j1"]
    summary = "PR #55192 is replayed onto the rebased base and both PRs are registered."
    deliver(monkeypatch, [message("worker_done", summary, subject=pushed, id="relay_6c98dc2ddc4d")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    job = state.load(remote)["jobs"]["j1"]
    assert {key: job[key] for key in ("said", "state", "waits", "returned")} == {key: settled[key] for key in ("said", "state", "waits", "returned")}
    assert state.report_text(remote, "j1") == report


@pytest.mark.parametrize("ended", [False, True])
def test_the_exit_of_a_lane_that_kept_its_report_is_not_said_stopped(run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ended: bool) -> None:
    cloned(tmp_path, remote)
    ready = "READY https://github.com/Forge-AI/monorepo/pull/9"
    with state.edit(remote) as data:
        state.keep_report(remote, "j1", ready, f"{ready}\nPR: https://github.com/Forge-AI/monorepo/pull/9\n", state.parse_time("2026-11-13T06:33:50Z"))
        state.take_result(data, "j1", ready)
        data["jobs"]["j1"]["workspace"] = "pool-one"
    if ended:
        monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
        monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "stopped ctx_lane0001 stopped")
        assert run("job", "end", "j1")[0] == 0
    stopped = "Worker term_1 exited (Terminal closed by operator request) without sending worker_done.\nLast output:\nkept j1's report: READY"
    deliver(monkeypatch, [message("escalation", stopped, subject="Worker exited without reporting", id="msg_stop_926de80909585e54654a273f")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    assert (state.load(remote)["jobs"]["j1"]["said"], state.report_text(remote, "j1").splitlines()[0]) == (ready, ready)


def test_the_exit_of_a_lane_drive_job_end_ended_while_it_waited_on_its_question_is_not_said_stopped(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned(tmp_path, remote)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(state="waiting", said="ASK which npm name?", ask="msg_q1", workspace="pool-one")
    monkeypatch.setattr(orca, "save", lambda drive, job_id, job, root: None)
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: "stopped ctx_lane0001 stopped")
    assert run("job", "end", "j1")[0] == 0
    stopped = "Worker term_1 exited (Terminal closed by operator request) without sending worker_done.\nLast output:\nASK which npm name?"
    deliver(monkeypatch, [message("escalation", stopped, subject="Worker exited without reporting")])
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    assert (state.load(remote)["jobs"]["j1"]["state"], state.load(remote)["jobs"]["j1"]["said"]) == ("waiting", "ASK which npm name?")


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
    assert staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0) == []
    data = state.load(remote)
    pr, job = data["prs"]["yasyf/cc-skills#7"], data["jobs"]["j1"]
    assert (landers, pr["job"], pr["wants_tap"], pr["lander"]["head"]) == ([("yasyf/cc-skills#7",)], "j1", False, "abc")
    assert (job["said"], job["prs"], job["waits"]) == (f"PUSHED {url}", [url], ["yasyf/cc-skills#7"])


def test_a_lanes_ready_on_a_yasyf_pr_with_no_proof_file_is_refused_and_starts_no_lander(
    remote: str, monkeypatch: pytest.MonkeyPatch, landers: list[tuple[str, ...]]
) -> None:
    url = "https://github.com/yasyf/cc-skills/pull/7"
    deliver(monkeypatch, [message("worker_done", f"READY {url}\nPR: {url}")])
    [line] = staged(watch.lane_lines, watch.Watcher(remote, offset=state.events_end(remote)), T0)
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
    watching = watch.Watcher(remote, offset=state.events_end(remote))
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
    assert binds == [RUN, "run_other"]
    assert (argv.count("--retry-request"), shown) == (1, [argv[argv.index("--retry-request") + 1]] * 2)
    assert not given or shown[0] == "req_launcher"


def test_every_lane_call_speaks_as_the_drives_own_terminal_whatever_terminal_ran_it_and_binds_only_the_lane_run(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def orca_call(*args: str) -> dict:
        calls.append((os.environ["ORCA_TERMINAL_HANDLE"], *args[:3]))
        if args[0] == "run-create":
            return {"run": {"id": FRESH_RUN}}
        if args[0] == "reply":
            raise orca.OrcaError("orca orchestration reply: consumer_fenced: This caller is bound to Run run_other")
        return {}

    monkeypatch.setattr(orca, "call", orca_call)
    monkeypatch.setattr(orca.subprocess, "Popen", lambda argv, **kwargs: Started(argv))
    assert orca.new_run(remote, "a lane") == FRESH_RUN
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_coordinator")
    orca.bound_command(remote, RUN, ["orca", "orchestration", "worker-start", "--run", RUN])
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_coordinator")
    with pytest.raises(orca.OrcaError):
        orca.reply(remote, RUN, "msg_1", "an answer")
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_coordinator")
    orca.send(remote, {"dispatch": "ctx_1", "run": RUN}, "From the coordinator", "a message")
    assert calls == [
        (DRIVE_TERMINAL, "run-create", "--objective", "a lane"),
        (DRIVE_TERMINAL, "run-use", "--id", RUN),
        (DRIVE_TERMINAL, "run-use", "--id", RUN),
        (DRIVE_TERMINAL, "reply", "--id", "msg_1"),
        (DRIVE_TERMINAL, "send", "--to", "dispatch:ctx_1"),
    ]


@pytest.mark.parametrize(
    ("kept", "orphaned", "spoken"),
    [("term_kept", False, "term_kept"), ("term_kept", True, "term_opened"), ("term_kept", None, "term_opened"), (None, None, "term_opened")],
)
def test_a_drive_speaks_as_the_terminal_it_keeps_while_orca_shows_its_pane_else_one_it_opens_in_the_coordinators_checkout_and_keeps(
    remote: str, monkeypatch: pytest.MonkeyPatch, kept: str | None, orphaned: bool | None, spoken: str
) -> None:
    with state.edit(remote) as data:
        data["terminal"] = kept
        data["roles"]["coordinator"]["cwd"] = "/work"
    opened = []

    def orca_request(*args: str, seconds: float = orca.CALL_SECONDS) -> dict:
        if args[:2] == ("terminal", "create"):
            opened.append(args[2:])
            return {"terminal": {"handle": "term_opened"}}
        if orphaned is None:
            raise orca.OrcaError("orca terminal show: terminal_not_found: no such terminal")
        return {"terminal": {"orphaned": orphaned}}

    monkeypatch.setattr(orca, "request", orca_request)
    assert (REAL_DRIVE_TERMINAL(remote), state.load(remote)["terminal"]) == (spoken, spoken)
    assert opened == ([] if spoken == kept else [("--worktree", "path:/work", "--title", f"{remote}-lanes")])


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
    assert binds == [RUN]


NO_RUN = "orca orchestration run-use: run_not_found: no such Run"


@pytest.mark.parametrize(
    ("refused", "starts", "binds"),
    [
        ("lane binding", 0, [RUN]),
        ("lane binding after a fence", 1, [RUN, RUN]),
    ],
)
def test_bound_exits_75_when_it_refuses_before_a_worker_start_could_start_anything(
    remote: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refused: str, starts: int, binds: list[str]
) -> None:
    bound = []

    def orca_call(*args: str) -> dict:
        bound.append(args[2])
        if bound == binds:
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
    watching = watch.Watcher(rooted, offset=state.events_end(rooted))
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
    """The attempt the launcher keeps for a brief whose worker started: its brief, and worker-start exited 0 with a ready receipt."""
    kept = state.drive_dir(drive) / "jobs" / f"{attempt}.worker"
    kept.mkdir(parents=True)
    kept.with_suffix(".md").write_text("Order (his words): ship it\n")
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: pytest.fail("a worker already started gets no second Run"))
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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: pytest.fail("a worker already started gets no second Run"))
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
    assert list(place.local_builders(state.load(demo), state.now())) == ["j1"]
    monkeypatch.setattr(orca, "worker", lambda dispatch: SPRITE_WORKER)
    watch.run_pass(watch.Watcher(demo, offset=state.events_end(demo)), T0)
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
    launched = orca.launch("demo", "demo-j1", brief, tmp_path, RUN, state.LOCAL_ROUTE, None, None)
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
        orca.launch("demo", "demo-j1", brief, tmp_path, RUN, None, None, None)
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


def test_a_sprite_says_whether_anything_listens_on_the_port_its_cc_remote_task_runs_orcas_runtime_on_and_nothing_when_it_does_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, dispatch, port = J982_10
    monkeypatch.setattr(orca, "CC_REMOTE_TASKS", tmp_path)
    (tmp_path / f"{workspace}.json").write_text(json.dumps({"port": port, "forward": {"host": workspace, "config": "/ssh/j982-10.ssh"}}))
    answers = [(0, b""), (0, f"LISTEN 0 4096 127.0.0.1:{port} 0.0.0.0:*\n".encode()), (255, b""), None]
    ran = []

    def ssh(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        ran.append((argv[:3], argv[-2:]))
        if not (answer := answers[len(ran) - 1]):
            raise subprocess.TimeoutExpired(argv, orca.CALL_SECONDS)
        return subprocess.CompletedProcess(argv, answer[0], stdout=answer[1], stderr=b"")

    monkeypatch.setattr(orca.subprocess, "run", ssh)
    assert [RUNTIME_LISTENING(workspace) for _ in answers] == [False, True, None, None]
    assert ran == [(["ssh", "-F", "/ssh/j982-10.ssh"], [workspace, f"ss -Hltn 'sport = :{port}'"])] * 4
    assert RUNTIME_LISTENING("pool-unrecorded") is None


def test_putting_a_file_on_a_sprite_sends_it_through_ssh_stdin_and_returns_the_path_only_when_the_sprite_kept_those_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orca, "CC_REMOTE_TASKS", tmp_path)
    (tmp_path / "pool-one.json").write_text(json.dumps({"forward": {"host": "pool-one", "config": "/ssh/pool-one.ssh"}}))
    text = b"Remote lane: drive demo, job j1.\n" * 4096
    path = "/home/sprite/.local/state/long-running/answers/msg_1.txt"
    kept = [hashlib.sha256(text).hexdigest(), hashlib.sha256(b"torn").hexdigest()]
    ran = []

    def ssh(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        ran.append((argv[:3], argv[-2], kwargs["input"]))
        return subprocess.CompletedProcess(argv, 0, stdout=f"{kept[len(ran) - 1]}  {path}\n".encode(), stderr=b"")

    monkeypatch.setattr(orca.subprocess, "run", ssh)
    assert orca.put_on_sprite("pool-one", orca.SPRITE_ANSWERS, "msg_1.txt", text) == path
    assert ran == [(["ssh", "-F", "/ssh/pool-one.ssh"], "pool-one", text)]
    with pytest.raises(orca.Unfit, match="^writing .local/state/long-running/answers/msg_1.txt onto pool-one exited 0: "):
        orca.put_on_sprite("pool-one", orca.SPRITE_ANSWERS, "msg_1.txt", text)


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
    monkeypatch.setattr(orca, "new_run", lambda drive, objective: RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None, workspace: str | None, session: str | None) -> dict:
        watching = watch.Watcher(demo, offset=state.events_end(demo))
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
    watching = watch.Watcher(demo, offset=state.events_end(demo))
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
    watching = watch.Watcher(demo, offset=state.events_end(demo))
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
    watching = watch.Watcher(rooted, offset=state.events_end(rooted))
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on this Mac: its worker exited, while it worked on "root-tooling: dispatch ctx_ab821100f80b". The watch '
        "ran drive job resume j1, which relaunches it in a fresh worktree on this Mac with a handoff, its output in the drive's relaunches.log"
    ]
