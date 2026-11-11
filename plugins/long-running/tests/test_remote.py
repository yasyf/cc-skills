import itertools
import json
import subprocess
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from drive import cli, github, orca, place, reads, render, state, watch

Run = Callable[..., tuple[int, str]]
T0 = state.parse_time("2026-11-09T07:30:00-08:00")
RUN = "run_drive0001"
DRIVE_RUN = "run_coordinator1"
DISPATCH = "ctx_lane0001"
SEQUENCE = itertools.count(1)
FINISHED_WORKER = {
    "worker": {"state": "succeeded", "startOptions": {"worktree": "id:repo-1::/home/sprite/monorepo"}},
    "projection": {"liveness": {"verdict": "live"}, "outcome": "succeeded", "attention": {"categories": ["root_completion"]}},
    "terminal": {"branch": "refs/heads/ci-mac-box-prove"},
}
ROOT_DISPATCH = "ctx_ab821100f80b"
ROOT_WORKER = {
    "worker": {"state": "ready", "startOptions": {"worktree": "path:/work/root-tooling"}},
    "dispatch": {"runId": RUN, "taskId": "task_root"},
    "projection": {"liveness": {"verdict": "live"}, "outcome": "in_progress", "attention": {"categories": []}},
    "terminal": {"handle": "term_root", "lastOutputAt": None},
}
REAL_LOCAL_GAP = watch.local_gap
UNSENT = (
    "no worker started on Sprite pool-one: orca orchestration worker-start failed twice before Orca recorded it, with"
    " runtime_error: Timed out waiting for the remote Orca runtime; its terminal term_lane waits idle, and launching this"
    " brief again retries worker-start in it"
)


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
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the remote builder")
    run("job", "add", "builder", "--order", "o1", "--goal", "remote builders", "--remote")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(run=RUN, dispatch=DISPATCH, ask=None, report=None)
        data["run"] = DRIVE_RUN
    return demo


@pytest.fixture
def rooted(run: Run, demo: str) -> str:
    """A root started by hand with the orca skill's local route and recorded with drive job set --agent <its dispatch>, as drives saved it before local lanes."""
    run("order", "add", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(agent=ROOT_DISPATCH, where="local", said="root-tooling: dispatch ctx_ab821100f80b")
        data["run"] = DRIVE_RUN
    return demo


def deliver(monkeypatch: pytest.MonkeyPatch, *batches: list[dict]) -> list[str]:
    queue, calls = list(batches), []

    def fake(run: str) -> list[dict]:
        calls.append(run)
        return queue.pop(0) if queue else []

    monkeypatch.setattr(orca, "mail", fake)
    return calls


@pytest.mark.parametrize(
    ("share", "local_builders", "where"),
    [(0.81, 0, "remote"), (0.5, 3, "remote"), (0.2, 8, "remote"), (0.2, 7, "local")],
)
def test_verdict_sends_a_builder_remote_over_the_limit_or_the_cap(
    share: float, local_builders: int, where: str
) -> None:
    assert place.verdict(share, 0.5, local_builders)[0] == where


def test_place_counts_only_running_builders_on_this_mac(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.2)
    monkeypatch.setenv(place.LIMIT_ENV, "0.5")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "many")
    for _ in range(7):
        run("job", "add", "builder", "--order", "o1", "--goal", "here", "--agent", "a")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    run("job", "add", "reader", "--order", "o1", "--goal", "read", "--agent", "r")
    run("job", "add", "builder", "--order", "o1", "--goal", "parked", "--agent", "p")
    run("job", "set", "j10", "--state", "waiting")
    code, out = run("place", "--json")
    assert (code, json.loads(out)["where"], json.loads(out)["local_builders"]) == (0, "local", 7)
    run("job", "add", "builder", "--order", "o1", "--goal", "eighth", "--agent", "e")
    assert run("place")[1] == "remote: 8 builders already run on this Mac, its cap of 8"


def test_place_skips_a_running_builder_whose_agent_is_gone(
    run: Run, demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(place, "busy", lambda: 0.2)
    monkeypatch.setenv(place.LIMIT_ENV, "0.5")
    monkeypatch.setattr(
        watch, "local_gap", lambda job_id, job, data, moment: f"{job_id}'s agent ended with an earlier session" if job_id == "j1" else None
    )
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "two")
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


def test_launch_composes_the_vm_brief_and_records_the_lane(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".orca.json").write_text("{}")
    (tmp_path / "brief.md").write_text("Order (his words): ship it\nDrive: demo, order o1, job j1\n")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    objectives = []
    monkeypatch.setattr(orca, "new_run", lambda objective: objectives.append(objective) or RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), checkout=checkout, run=run_id)
        fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
        return {field: f"{field}-1" for field in fields} | {"dispatch": DISPATCH}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "launch", "j1", "brief.md")
    assert (code, out) == (0, f"j1 started on Sprite workspace-1 as dispatch {DISPATCH}")
    assert (launched["lane"], launched["checkout"].resolve(), launched["run"]) == ("demo-j1", tmp_path.resolve(), RUN)
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


def test_launch_refuses_a_job_on_this_mac_and_a_checkout_without_orca_values(
    run: Run, demo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "here", "--agent", "a")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    assert cli.main(["job", "launch", "j1", "brief.md"]) == 2
    assert capsys.readouterr().err == "drive: j1 runs on this Mac as agent a; resume it with SendMessage\n"
    assert run("job", "launch", "j2", "brief.md")[0] == 2
    assert "dispatch" not in state.load(demo)["jobs"]["j2"]


def test_launch_starts_a_job_added_with_no_agent_as_a_remote_lane(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".orca.json").write_text("{}")
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
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
        orca.launch("demo", "demo-j1", brief, tmp_path, RUN, None)
    assert str(raised.value) == said.format(attempt=attempt)


def test_resume_answers_the_open_question_then_sends_to_the_dispatch(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(orca, "call", lambda *args: calls.append(args) or {})
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a live lane is never relaunched"))
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="msg_q1", state="waiting")
    assert run("job", "resume", "j1", "TAP Approve")[0] == 0
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


def dead(verdict: str = "exited") -> dict:
    return {"projection": {"liveness": {"verdict": verdict}, "outcome": "in_progress", "attention": {"categories": []}}}


def test_resume_to_a_dead_worker_relaunches_the_job_with_the_message_in_its_handoff(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".orca.json").write_text("{}")
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
    sent, launched = [], {}
    monkeypatch.setattr(orca, "call", lambda *args: sent.append(args) or {})
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None) -> dict:
        launched.update(lane=lane, brief=brief.read_text(), run=run_id)
        fields = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
        return {field: f"{field}-2" for field in fields}

    monkeypatch.setattr(orca, "launch", fake_launch)
    code, out = run("job", "resume", "j1", "Use @poeticapps/poetic")
    assert (code, sent) == (0, [])
    assert out == (
        "j1: its lane on Sprite sprite-old was dead (its worker exited), so the message was not sent to it; relaunched the "
        "job on Sprite workspace-2 as dispatch dispatch-2 with a handoff carrying its brief, its branch, its last line, its "
        "report, its PRs and the message; work not pushed from sprite-old is not carried over"
    )
    assert (launched["lane"], launched["run"]) == ("demo-j1-2", RUN)
    brief = orca.brief_of(launched["brief"])
    assert brief.startswith("Order (his words): ship it\nDrive: demo, order o1, job j1\n\n## Taking over from a lane that died")
    assert "Commit and push before any long build." in brief
    assert "Its last line: ASK which npm name?\nIts PRs: https://github.com/Forge-AI/monorepo/pull/7\n" in brief
    assert brief.endswith("which you act on now:\n\nUse @poeticapps/poetic")
    job = state.load(remote)["jobs"]["j1"]
    assert (job["dispatch"], job["workspace"], job["state"], job["ask"], job["brief"]) == (
        "dispatch-2",
        "workspace-2",
        "running",
        None,
        str(first),
    )
    assert [(death["dispatch"], death["workspace"], death["why"]) for death in job["deaths"]] == [
        (DISPATCH, "sprite-old", "its worker exited")
    ]


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

    def worker_start(drive: str, run_id: str, argv: list[str]) -> subprocess.CompletedProcess:
        started.update(run=run_id, argv=argv, spec=Path(argv[argv.index("--spec-file") + 1]).read_text())
        receipt = {"ok": True, "result": {"state": "ready", "runId": run_id, "taskId": "task_2", "dispatchId": "ctx_lane0002"}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt), stderr="")

    monkeypatch.setattr(orca, "bound_command", worker_start)
    code, out = run("job", "resume", "j1", "The review: one medium finding, fix it")
    assert (code, out) == (
        0,
        "j1: its lane on Sprite pool-one had finished (its worker succeeded, and Orca sends nothing to a finished worker on "
        "a Sprite), so the message was not sent to it; started the job's next worker in its terminal on the same Sprite as "
        "dispatch ctx_lane0002 with a handoff carrying its brief, its branch, its last line, its report, its PRs and the message",
    )
    argv = started["argv"]
    options = {argv[index]: argv[index + 1] for index in range(3, len(argv) - 2, 2)}
    assert (started["run"], argv[:3], argv[-2:]) == (RUN, ["orca", "orchestration", "worker-start"], ["--full", "--json"])
    assert {key: value for key, value in options.items() if key != "--spec-file"} == {
        "--run": RUN,
        "--on": "pool-one",
        "--worktree": "id:repo-1::/home/sprite/monorepo",
        "--terminal": "term_lane",
        "--task-title": "demo-j1-2",
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


def test_the_watch_says_a_dead_lane_once_whether_it_ran_or_waited(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(workspace="sprite-old", state="waiting", said="ASK which npm name?", ask="msg_q1")
    shown = []
    monkeypatch.setattr(orca, "worker", lambda dispatch: shown.append(dispatch) or dead())
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on Sprite sprite-old: its worker exited, while it waited on "ASK which npm name?". A message sent '
        'to it reaches no one; drive job resume j1 "<its next step, or his answer>" relaunches it on a fresh Sprite with a handoff'
    ]
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == []
    assert shown == [DISPATCH]
    assert [death["dispatch"] for death in state.load(remote)["jobs"]["j1"]["deaths"]] == [DISPATCH]


def test_the_watch_says_a_lane_orca_dropped_died(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    with state.edit(remote) as data:
        data["jobs"]["j1"]["workspace"] = "sprite-old"

    def gone(dispatch: str) -> dict:
        raise orca.OrcaError("orca orchestration worker-show: dispatch_not_found: no such dispatch")

    monkeypatch.setattr(orca, "worker", gone)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0)[0].startswith("LANE j1 o1 died on Sprite sprite-old: Orca no longer holds its worker, while it worked on")


def branches(monkeypatch: pytest.MonkeyPatch, prs: dict[str, dict]) -> list[list[tuple[str, str]]]:
    asked = []
    monkeypatch.setattr(github, "checkout_repo", lambda checkout: "Forge-AI/monorepo")
    monkeypatch.setattr(github, "open_on", lambda heads: asked.append(heads) or prs)
    monkeypatch.setattr(github, "fetch", lambda keys: {})
    return asked


def test_a_finished_lane_whose_job_still_runs_is_said_every_thirty_minutes_until_its_result_is_recorded(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch)
    monkeypatch.setattr(orca, "worker", lambda dispatch: FINISHED_WORKER)
    asked = branches(monkeypatch, {"Forge-AI/monorepo#53447": {"branch": "ci-mac-box-prove", "checks": "pass", "review": "approved"}})
    with state.edit(remote) as data:
        data["orders"]["o1"]["parts"]["ship the remote builder"]["posted"] = "https://acme.slack.com/archives/D123/p1794200000000100"
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
        data["jobs"]["j1"].update(state="waiting", said="WAITING CI", updated=state.stamp(T0))
    assert watch.run_pass(watching, T0 + timedelta(minutes=60)) == []
    assert state.load(remote)["orders"]["o1"]["parts"]["ship the remote builder"]["orphan_said"] is None


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
    body = "Built and opened PR #53447: the serve marks queued card answers.\nLeft: build #122630 is still running"
    deliver(monkeypatch, [message("worker_done", body, subject="WAITING CI on PR #53447 (build #122630)")])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 WAITING CI on PR #53447 (build #122630)"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["report"], job["state"], job["said"]) == (body, "waiting", "WAITING CI on PR #53447 (build #122630)")


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
    (tmp_path / ".orca.json").write_text("{}")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    assert run("job", "resume", "j1", "go")[0] == 2
    assert run("job", "end", "j1")[0] == 2


def test_end_reclaims_the_sprite_and_marks_the_job_done(
    run: Run, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".orca.json").write_text("{}")
    reclaimed = []
    monkeypatch.setattr(orca, "reclaim", lambda run_id, dispatch, root: reclaimed.append((run_id, dispatch)) or "")
    assert run("job", "end", "j1") == (0, "j1 ended")
    assert reclaimed == [(RUN, DISPATCH)]
    assert state.load(remote)["jobs"]["j1"]["state"] == "done"


def test_lane_mail_becomes_job_events_and_records_the_question(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(
        monkeypatch,
        [
            message("heartbeat", "alive"),
            message("status", "building the watch"),
            message("status", "no body", body=None),
            message("question", "READY https://github.com/Forge-AI/monorepo/pull/7\nPR: Fix: x\nCHANGE: y"),
            message("worker_done", "DONE elsewhere", from_handle="dispatch:ctx_other"),
        ],
    )
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 READY https://github.com/Forge-AI/monorepo/pull/7"]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == ("msg_question", "waiting", job["report"].splitlines()[0])
    assert job["report"].endswith("CHANGE: y")


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
    monkeypatch.setattr(reads, "answer", lambda question, root: asked.append((question, root)) or "ERROR: journal write")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    assert asked == [(f"LOG {url}", tmp_path)]
    assert replies == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_log", "--run", RUN, "--body", "ERROR: journal write"),
        ("run-use", "--id", DRIVE_RUN),
    ]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == (None, "running", None)


def test_a_read_that_cannot_be_answered_tells_its_lane_why_and_says_so(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    question = "LOG https://buildkite.com/forge/test/builds/1"
    deliver(monkeypatch, [message("question", question, id="msg_log")])

    def refuse(question: str, root: Path) -> str:
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
    monkeypatch.setattr(reads, "answer", lambda question, root: "log")
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


def test_a_read_someone_already_answered_is_dropped_and_said_once(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", "READ ccn answer search x", id="relay_6779eb1e29a6")])
    monkeypatch.setattr(reads, "answer", lambda question, root: "x")
    replies = []

    def answered(*args: str) -> dict:
        replies.append(args)
        if args[0] == "reply":
            done = subprocess.CompletedProcess(
                ["orca"],
                0,
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "answer_conflict",
                            "message": "Question relay_6779eb1e29a6 already has a different answer.",
                        },
                    }
                ),
                "",
            )
            return orca.result_of("orchestration reply", done)
        return {}

    monkeypatch.setattr(orca, "call", answered)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        "WATCH j1's read relay_6779eb1e29a6 was answered outside the watch, so the watch stops answering it"
    ]
    assert state.load(remote)["jobs"]["j1"]["reads"] == {}
    assert watch.run_pass(watching, T0 + timedelta(seconds=70)) == []
    assert [args for args in replies if args[0] == "reply"] == [
        ("reply", "--id", "relay_6779eb1e29a6", "--run", RUN, "--body", "x")
    ]


def test_a_read_the_watch_stopped_before_answering_is_answered_by_the_next_watch(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliver(monkeypatch, [message("question", 'READ ccn answer search "dom extraction"', id="relay_read")])

    def stopped(question: str, root: Path) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(reads, "answer", stopped)
    with pytest.raises(KeyboardInterrupt):
        watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    replies = []
    monkeypatch.setattr(reads, "answer", lambda question, root: f"{question}: empty, the read printed nothing")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0 + timedelta(minutes=5)) == []
    assert replies == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "relay_read", "--run", RUN, "--body", 'READ ccn answer search "dom extraction": empty, the read printed nothing'),
        ("run-use", "--id", DRIVE_RUN),
    ]


def test_mail_from_a_worker_the_job_replaced_is_only_kept(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(dispatch="ctx_lane0002", deaths=[{"dispatch": DISPATCH, "workspace": "pool-one", "why": "x", "at": "y"}])
    old = f"dispatch:{DISPATCH}"
    deliver(
        monkeypatch,
        [message("question", "READ ccn answer search x", id="msg_read", from_handle=old), message("worker_done", "DONE old", id="msg_done", from_handle=old)],
    )
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing answers a replaced worker: {args}"))
    assert watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0) == []
    job = state.load(remote)["jobs"]["j1"]
    assert (job.get("reads"), job["report"]) == (None, None)
    assert [reply["id"] for reply in state.replies(remote, "j1")] == ["msg_read", "msg_done"]


def test_every_result_and_question_a_lane_sends_stays_readable_after_the_next_one_replaces_its_report(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    alive = message("heartbeat", "alive")
    detail = message("worker_done", "FINDINGS: the proof identity is a full employee session", id="msg_1", created_at="2026-11-10T04:40:00Z")
    short = message("question", "WAITING his pick (details in my last reply)", id="msg_2", created_at="2026-11-10T04:41:00Z")
    deliver(monkeypatch, [alive, detail], [short])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    watch.run_pass(watching, T0)
    assert watch.run_pass(watching, T0 + timedelta(minutes=1)) == ["JOB j1 o1 WAITING his pick (details in my last reply)"]
    assert state.load(remote)["jobs"]["j1"]["report"] == "WAITING his pick (details in my last reply)"
    code, out = run("job", "replies", "j1")
    assert (code, out) == (
        0,
        "2026-11-10T04:40:00Z worker_done: FINDINGS: the proof identity is a full employee session\n\n"
        "2026-11-10T04:41:00Z question: WAITING his pick (details in my last reply)",
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

    def broken(run: str) -> list[dict]:
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
    monkeypatch.setattr(orca, "mail", lambda run_id: mailboxes.pop(run_id))
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == ["JOB j1 o1 READY first", "JOB j2 o1 READY second"]
    assert mailboxes == {}


def test_the_watch_registers_each_pr_a_lane_names_on_a_ready_or_pr_line(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "PR: Fix: x https://github.com/Forge-AI/monorepo/pull/8\nrebased on https://github.com/Forge-AI/monorepo/pull/9"
    deliver(monkeypatch, [message("status", body)])
    watch.run_pass(watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0), T0)
    data = state.load(remote)
    assert list(data["prs"]) == ["Forge-AI/monorepo#8"]
    assert (data["prs"]["Forge-AI/monorepo#8"]["job"], data["prs"]["Forge-AI/monorepo#8"]["wants_tap"]) == ("j1", True)
    assert data["jobs"]["j1"]["prs"] == ["https://github.com/Forge-AI/monorepo/pull/8"]


def test_the_inbox_turns_each_message_into_one_event_even_one_a_consuming_check_took_first(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = message("worker_done", "WAITING CI on #7", id="msg_old", read=1)
    first = message("status", "building", id="msg_1")
    second = message("question", "READY https://github.com/Forge-AI/monorepo/pull/7", id="msg_2")
    deliver(monkeypatch, [taken, first], [taken, first, second], [taken, first, second])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    passes = [watch.run_pass(watching, T0 + timedelta(minutes=m)) for m in (0, 1, 2)]
    assert passes == [["JOB j1 o1 WAITING CI on #7"], ["JOB j1 o1 READY https://github.com/Forge-AI/monorepo/pull/7"], []]
    assert state.load(remote)["lane_mail"] == {RUN: second["sequence"]}


def test_a_reply_during_a_launch_waits_until_its_worker_start_is_done(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    binds = []
    monkeypatch.setattr(orca, "call", lambda *args: binds.append(args[2]) if args[0] == "run-use" else {})
    replying = threading.Thread(target=orca.reply, args=(remote, "run_other", "msg_1", "an answer"))

    def worker_start(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        replying.start()
        replying.join(timeout=0.5)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"bound": binds[-1], "reply_waiting": replying.is_alive()}), stderr="")

    monkeypatch.setattr(orca.subprocess, "run", worker_start)
    done = orca.bound_command(remote, RUN, ["orca", "orchestration", "worker-start", "--run", RUN])
    replying.join()
    assert json.loads(done.stdout) == {"bound": RUN, "reply_waiting": True}
    assert binds == [RUN, DRIVE_RUN, "run_other", DRIVE_RUN]


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
    monkeypatch.setattr(orca.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="{}", stderr=""))
    orca.bound_command(remote, RUN, ["orca", "orchestration", "worker-start", "--run", RUN])
    with pytest.raises(orca.OrcaError):
        orca.reply(remote, RUN, "msg_1", "an answer")
    assert calls == [
        ("run-use", "--id", RUN),
        ("run-create", "--objective", f"drive {remote}: the coordinator's own Run, which no lane mails"),
        ("run-use", "--id", DRIVE_RUN),
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_1"),
        ("run-use", "--id", DRIVE_RUN),
    ]
    assert state.load(remote)["run"] == DRIVE_RUN


def test_a_root_recorded_by_its_orca_dispatch_is_read_through_worker_show_and_its_run_mail_and_is_no_orphan(
    rooted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch, "local_gap", REAL_LOCAL_GAP)
    shown = []
    monkeypatch.setattr(orca, "worker", lambda dispatch: shown.append(dispatch) or ROOT_WORKER)
    calls = deliver(monkeypatch, [message("worker_done", "WAITING CI on PR #9", from_handle=f"dispatch:{ROOT_DISPATCH}")])
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
    run("order", "add", "the tooling root")
    run("job", "add", "builder", "--order", "o1", "--goal", "root tooling")
    run("job", "set", "j1", "--agent", ROOT_DISPATCH)
    saved = json.loads(state.state_path(demo).read_text())["jobs"]["j1"]
    assert {field: saved[field] for field in ("agent", "dispatch", "where", "route", "workspace")} == {
        "agent": None,
        "dispatch": ROOT_DISPATCH,
        "where": "remote",
        "route": "local",
        "workspace": None,
    }


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
    launched = orca.launch("demo", "demo-j1", brief, tmp_path, RUN, state.LOCAL_ROUTE)
    assert launched == {**receipt, "lane": "demo-j1", "workspace": None, "environment": None}
    assert (ran["argv"], ran["cwd"]) == (["orca-worker-launch.sh", "demo-j1", orca.MODEL, orca.EFFORT, str(brief)], tmp_path)
    assert {key: ran["env"][key] for key in ("WORKER_LOCAL", "WORKER_REPO", "WORKER_RUN", "WORKER_ENV")} == {
        "WORKER_LOCAL": "1",
        "WORKER_REPO": "repo-local",
        "WORKER_RUN": RUN,
        "WORKER_ENV": orca.LANE_ENV,
    }
    assert asked == [("repo", "show", "--repo", "path:/code/monorepo")]


def test_launch_local_records_a_lane_on_this_mac_that_reads_as_starting_while_it_launches(
    run: Run, demo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".orca.json").write_text("{}")
    (tmp_path / "brief.md").write_text("Drive: demo, order o1, job j1\n")
    run("order", "add", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it")
    monkeypatch.setattr(orca, "new_run", lambda objective: RUN)
    launched = {}

    def fake_launch(drive: str, lane: str, brief: Path, checkout: Path, run_id: str, route: str | None) -> dict:
        watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
        watch.agent_lines(watching, state.now())
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
    run("order", "add", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["launching"] = state.stamp(T0 - ago)
    watching = watch.Watcher(demo, offset=state.events_end(demo), ticked_at=T0)
    watch.agent_lines(watching, T0)
    assert watching.agent_gaps == {"j1": gap}


def test_resume_to_a_local_lane_that_finished_starts_the_next_worker_in_its_worktree_and_terminal_on_this_mac(
    run: Run, rooted: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.md"
    first.write_text(orca.vm_brief(rooted, "j1", "Order (his words): the tooling root", state.LOCAL_ROUTE))
    with state.edit(rooted) as data:
        data["jobs"]["j1"].update(run=RUN, task="task_root", terminal="term_root", lane="demo-j1", brief=str(first), ask=None, report=None)
    monkeypatch.setattr(orca, "worker", lambda dispatch: {**ROOT_WORKER, "worker": {**ROOT_WORKER["worker"], "state": "succeeded"}})
    monkeypatch.setattr(orca, "launch", lambda *args: pytest.fail("a finished worker whose terminal Orca holds is never relaunched elsewhere"))
    monkeypatch.setattr(orca, "call", lambda *args: pytest.fail(f"nothing is sent to a finished worker: {args}"))
    started = {}

    def worker_start(drive: str, run_id: str, argv: list[str]) -> subprocess.CompletedProcess:
        started.update(argv=argv, spec=Path(argv[argv.index("--spec-file") + 1]).read_text())
        receipt = {"ok": True, "result": {"state": "ready", "runId": run_id, "taskId": "task_2", "dispatchId": "ctx_lane0002"}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt), stderr="")

    monkeypatch.setattr(orca, "bound_command", worker_start)
    assert run("job", "resume", "j1", "The review: fix the one finding") == (
        0,
        "j1: its lane on this Mac had finished (its worker succeeded, and Orca sends nothing to a finished worker on this "
        "Mac), so the message was not sent to it; started the job's next worker in its terminal on this Mac as dispatch "
        "ctx_lane0002 with a handoff carrying its brief, its branch, its last line, its report, its PRs and the message",
    )
    argv = started["argv"]
    options = {argv[index]: argv[index + 1] for index in range(3, len(argv) - 2, 2)}
    assert {key: value for key, value in options.items() if key != "--spec-file"} == {
        "--run": RUN,
        "--worktree": "path:/work/root-tooling",
        "--terminal": "term_root",
        "--task-title": "demo-j1-2",
        "--timeout-ms": "600000",
    }
    assert "You continue the job in its terminal and checkout on this Mac, as it left them." in orca.brief_of(started["spec"])
    job = state.load(rooted)["jobs"]["j1"]
    assert (job["dispatch"], job["route"], job["terminal"], job["workspace"], job["state"]) == ("ctx_lane0002", "local", "term_root", None, "running")
    assert [(ended["dispatch"], ended["workspace"]) for ended in job["deaths"]] == [(ROOT_DISPATCH, None)]


def test_the_watch_says_a_dead_local_lane_died_on_this_mac(rooted: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch)
    with state.edit(rooted) as data:
        data["jobs"]["j1"].update(run=RUN, task="task_root", terminal="term_root")
    monkeypatch.setattr(orca, "worker", lambda dispatch: dead())
    watching = watch.Watcher(rooted, offset=state.events_end(rooted), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        'LANE j1 o1 died on this Mac: its worker exited, while it worked on "root-tooling: dispatch ctx_ab821100f80b". A '
        'message sent to it reaches no one; drive job resume j1 "<its next step, or his answer>" relaunches it in a fresh '
        "worktree on this Mac with a handoff"
    ]
