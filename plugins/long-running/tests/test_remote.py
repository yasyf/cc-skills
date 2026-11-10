import itertools
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from drive import orca, place, reads, render, state, watch

Run = Callable[..., tuple[int, str]]
T0 = state.parse_time("2026-11-09T07:30:00-08:00")
RUN = "run_drive0001"
DISPATCH = "ctx_lane0001"
SEQUENCE = itertools.count(1)


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
    }
    return {**base, **overrides}


@pytest.fixture
def remote(run: Run, demo: str) -> str:
    run("order", "add", "ship the remote builder")
    run("job", "add", "builder", "--order", "o1", "--goal", "remote builders", "--remote")
    with state.edit(demo) as data:
        data["jobs"]["j1"].update(run=RUN, dispatch=DISPATCH, ask=None)
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
    run("order", "add", "many")
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
    run("order", "add", "two")
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
    run("order", "add", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "ship it", "--remote")
    objectives = []
    monkeypatch.setattr(orca, "new_run", lambda objective: objectives.append(objective) or RUN)
    launched = {}

    def fake_launch(lane: str, brief: Path, checkout: Path, run_id: str) -> dict:
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


def test_launch_refuses_a_job_on_this_mac_and_a_checkout_without_orca_values(run: Run, demo: str) -> None:
    run("order", "add", "ship it")
    run("job", "add", "builder", "--order", "o1", "--goal", "here", "--agent", "a")
    run("job", "add", "builder", "--order", "o1", "--goal", "there", "--remote")
    assert run("job", "launch", "j1", "brief.md")[0] == 2
    assert run("job", "launch", "j2", "brief.md")[0] == 2
    assert "dispatch" not in state.load(demo)["jobs"]["j2"]


def test_resume_answers_the_open_question_then_sends_to_the_dispatch(
    run: Run, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(orca, "call", lambda *args: calls.append(args) or {})
    with state.edit(remote) as data:
        data["jobs"]["j1"].update(ask="msg_q1", state="waiting")
    assert run("job", "resume", "j1", "TAP Approve")[0] == 0
    assert run("job", "resume", "j1", "PR checks failed")[0] == 0
    assert calls == [
        ("run-use", "--id", RUN),
        ("reply", "--id", "msg_q1", "--run", RUN, "--body", "TAP Approve"),
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
    run("order", "add", "ship it")
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


def test_the_watch_answers_a_read_question_itself_and_keeps_the_job_as_it_was(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://buildkite.com/forge/test/builds/121531"
    deliver(monkeypatch, [message("question", f"LOG {url}", id="msg_log")])
    asked, replies = [], []
    monkeypatch.setattr(reads, "answer", lambda question, root: asked.append((question, root)) or "ERROR: journal write")
    monkeypatch.setattr(orca, "call", lambda *args: replies.append(args) or {})
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == []
    assert asked == [(f"LOG {url}", tmp_path)]
    assert replies == [("run-use", "--id", RUN), ("reply", "--id", "msg_log", "--run", RUN, "--body", "ERROR: journal write")]
    job = state.load(remote)["jobs"]["j1"]
    assert (job["ask"], job["state"], job["said"]) == (None, "running", None)


def test_a_read_answer_orca_refuses_is_one_watch_line(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    deliver(monkeypatch, [message("question", "LOG https://buildkite.com/forge/test/builds/1")])
    monkeypatch.setattr(reads, "answer", lambda question, root: "log")

    def refuse(*args: str) -> dict:
        raise orca.OrcaError("orca orchestration reply: not_found: no message")

    monkeypatch.setattr(orca, "call", refuse)
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    assert watch.run_pass(watching, T0) == [
        "WATCH read answer to j1 failed: orca orchestration reply: not_found: no message; its lane asks again when its wait ends"
    ]


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


def test_the_inbox_turns_each_message_into_one_event_and_skips_what_check_already_took(
    remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = message("question", "READY taken by check", id="msg_old", read=1)
    first = message("status", "building", id="msg_1")
    second = message("question", "READY https://github.com/Forge-AI/monorepo/pull/7", id="msg_2")
    deliver(monkeypatch, [taken, first], [taken, first, second], [taken, first, second])
    watching = watch.Watcher(remote, offset=state.events_end(remote), ticked_at=T0)
    passes = [watch.run_pass(watching, T0 + timedelta(minutes=m)) for m in (0, 1, 2)]
    assert passes == [[], ["JOB j1 o1 READY https://github.com/Forge-AI/monorepo/pull/7"], []]
    assert state.load(remote)["lane_mail"] == {RUN: second["sequence"]}
