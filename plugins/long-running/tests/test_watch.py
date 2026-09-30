from __future__ import annotations

import json
import subprocess

import ledger
import pytest
from conftest import LEDGER, FakeShell

REPO = "Forge-AI/monorepo"
PR = "27949"
HEAD = "eb4241ac46674fbddb035a3b369440c4c7ecb41b"
SQUASH = "815d915c0000000000000000000000000000abcd"
LANE = "phase0-deploy"


def event(kind: str, pr: str = PR, detail: str = "", head: str = HEAD) -> str:
    payload = {"at": "2026-09-30T05:34:49Z", "pr": int(pr), "event": kind, "head": head}
    if detail:
        payload["detail"] = detail
    return json.dumps(payload)


def watch(shell: FakeShell, tmp_path, *extra: str) -> int:
    return ledger.main(
        ["watch", "--repo", REPO, "--ledger", LEDGER, "--checkout", str(tmp_path), "--state", str(tmp_path / "watch.json"), "--once", *extra],
        shell,
    )


def row_shell(**fields: str) -> FakeShell:
    return FakeShell(rows=[{"key": PR, "fields": {"head": HEAD, "lane": LANE, "state": "open", **fields}}])


def p0s(shell: FakeShell) -> list[dict]:
    return [row["fields"] for row in shell.store["rows"] if row["key"].startswith("msg/") and row["fields"]["kind"] == "p0"]


def test_an_ejection_the_pass_it_happens_is_a_p0_on_the_inbox_and_a_line(capsys, tmp_path):
    shell = row_shell(label_head=HEAD, labelled_at="2026-09-30T05:28:00Z")
    shell.ccx_out = event("ejected", detail="it had merge conflicts") + "\n" + event("conflicting") + "\n"

    assert watch(shell, tmp_path) == 0

    out = capsys.readouterr().out.splitlines()
    assert out == [f"P0 #{PR} ejected (it had merge conflicts) {LANE}", f"P0 #{PR} conflicting {LANE}"]
    ejected, conflicting = p0s(shell)
    assert ejected["pr"] == PR and ejected["head"] == HEAD and ejected["lane"] == LANE and ejected["state"] == "pending"
    assert "ejected (it had merge conflicts)" in ejected["text"]
    assert conflicting["event"] == "conflicting"
    row = shell.fields(PR)
    assert row["ejected_at"] == "2026-09-30T05:34:49Z"
    assert row["watch_event"] == "conflicting"
    assert row["watch_queued"] == ""


def test_an_ejection_is_a_p0_even_on_a_row_the_desk_never_labelled(capsys, tmp_path):
    shell = row_shell()
    shell.ccx_out = event("queued") + "\n"
    watch(shell, tmp_path)
    assert shell.fields(PR)["watch_queued"] == "true"
    assert not p0s(shell)

    shell.ccx_out = event("ejected") + "\n"
    watch(shell, tmp_path)

    assert len(p0s(shell)) == 1
    assert f"P0 #{PR} ejected {LANE}" in capsys.readouterr().out


def test_red_on_a_queued_or_priority_row_is_a_p0_and_elsewhere_only_recorded(capsys, tmp_path):
    shell = row_shell()
    shell.ccx_out = event("red", detail="buildkite/tests") + "\n"

    watch(shell, tmp_path)
    assert not p0s(shell)
    assert shell.fields(PR)["watch_event"] == "red buildkite/tests"
    assert capsys.readouterr().out == ""

    watch(shell, tmp_path, "--priority", PR)
    assert len(p0s(shell)) == 1
    assert capsys.readouterr().out == f"P0 #{PR} red buildkite/tests {LANE}\n"


def test_each_pass_rereads_the_rows_so_new_prs_join_and_settled_ones_leave(tmp_path):
    shell = row_shell()
    shell.stores[LEDGER]["rows"].append({"key": "27001", "fields": {"state": "landed"}})
    watch(shell, tmp_path)
    shell.stores[LEDGER]["rows"].append({"key": "28008", "fields": {"head": HEAD, "lane": "phase0-rebase"}})
    watch(shell, tmp_path)

    first, second = shell.ccx_calls
    assert first[:5] == ["ccx", "vcs", "pr", "watch", PR] and "27001" not in first
    assert second[4:6] == [PR, "28008"]
    assert first[first.index("--state") + 1] == str(tmp_path / "watch.json.pending")
    assert {"--once", "--json"} <= set(first)


def arm(tmp_path, *prs: str) -> None:
    (tmp_path / "watch.json").write_text(json.dumps({"prs": {pr: {"state": "OPEN"} for pr in prs}}))


def test_a_landing_settles_the_row_in_the_same_pass(tmp_path):
    arm(tmp_path, PR)
    shell = row_shell()
    shell.pulls[PR] = {"number": int(PR), "state": "closed", "head": {"sha": HEAD, "ref": "yasyf/v3-phase0/deploy"}, "base": {"ref": "yasyf/v3-phase0/base"}}
    shell.pull_heads[PR] = HEAD
    shell.pr_files[PR] = ["infra/ci/src/pipelines/deploy/index.ts"]
    shell.base_squash = f"{SQUASH} 2026-09-30T05:50:00+00:00"
    shell.ccx_out = event("landed", detail=SQUASH[:9]) + "\n"

    assert watch(shell, tmp_path) == 0

    assert shell.fields(PR)["state"] == "landed"
    assert shell.fields(PR)["landed_sha"] == SQUASH
    assert not p0s(shell)


def test_a_failed_ccx_poll_is_reported_and_writes_nothing(capsys, tmp_path):
    class Failing(FakeShell):
        def run(self, argv, stdin=None):
            if argv[0] == "ccx":
                raise subprocess.CalledProcessError(1, argv, stderr="pr watch: 10 polls in a row failed")
            return super().run(argv, stdin)

    shell = Failing(rows=[{"key": PR, "fields": {"head": HEAD, "lane": LANE}}])

    assert watch(shell, tmp_path) == 1
    assert "10 polls in a row failed" in capsys.readouterr().err
    assert not p0s(shell)


def test_a_rate_limit_line_passes_through(capsys, tmp_path):
    shell = row_shell()
    shell.ccx_out = json.dumps({"at": "2026-09-30T06:00:00Z", "event": "rate-limited", "detail": "until 2026-09-30T07:00:00Z"}) + "\n"

    watch(shell, tmp_path)

    assert capsys.readouterr().out == "rate-limited until 2026-09-30T07:00:00Z\n"


def test_a_ccn_read_retries_while_another_desk_writes(monkeypatch):
    calls: list[list[str]] = []
    slept: list[float] = []

    def flaky(argv, **_):
        calls.append(argv)
        if len(calls) < 3:
            raise subprocess.CalledProcessError(1, argv, stderr="ledger busy")
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(ledger.subprocess, "run", flaky)
    monkeypatch.setattr(ledger.time, "sleep", slept.append)

    assert ledger.Shell().run(["ccn", "ledger", "show", LEDGER, "--json"]) == "{}"
    assert len(calls) == 3
    assert slept == [0.5, 1.0]


def test_a_ccn_read_that_keeps_failing_raises_and_a_write_is_never_retried(monkeypatch):
    calls: list[list[str]] = []

    def failing(argv, **_):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv, stderr="ledger busy")

    monkeypatch.setattr(ledger.subprocess, "run", failing)
    monkeypatch.setattr(ledger.time, "sleep", lambda _: None)

    with pytest.raises(subprocess.CalledProcessError):
        ledger.Shell().run(["ccn", "ledger", "show", LEDGER, "--json"])
    assert len(calls) == ledger.CCN_READ_ATTEMPTS

    calls.clear()
    with pytest.raises(subprocess.CalledProcessError):
        ledger.Shell().run(["ccn", "ledger", "row", "set", LEDGER, "--key", PR, "--field", "a=b"])
    assert len(calls) == 1


def test_the_snapshot_advances_only_after_the_ledger_took_every_transition(tmp_path):
    shell = row_shell(label_head=HEAD, labelled_at="2026-09-30T05:28:00Z")
    shell.ccx_out = event("ejected", detail="it had merge conflicts") + "\n"
    state = tmp_path / "watch.json"
    shell.fail_ccn_writes = True

    assert watch(shell, tmp_path) == 1
    assert not state.exists()

    shell.fail_ccn_writes = False
    assert watch(shell, tmp_path) == 0
    assert json.loads(state.read_text())["call"] == 2
    assert len(p0s(shell)) == 1


def test_an_acked_red_does_not_swallow_a_later_ejection_on_the_same_head(capsys, tmp_path):
    shell = row_shell()
    shell.ccx_out = event("red", detail="buildkite/tests") + "\n"
    watch(shell, tmp_path, "--priority", PR)
    for row in shell.store["rows"]:
        if row["key"].startswith("msg/"):
            row["fields"]["state"] = "acked"

    shell.ccx_out = event("ejected") + "\n"
    watch(shell, tmp_path)

    assert [m["event"] for m in p0s(shell)] == ["red buildkite/tests", "ejected"]


def test_a_stray_line_from_ccx_is_reported_and_the_events_still_land(capsys, tmp_path):
    shell = row_shell()
    shell.ccx_out = "warning: something\n" + event("ejected") + "\n"

    assert watch(shell, tmp_path) == 0

    captured = capsys.readouterr()
    assert "ccx: warning: something" in captured.err
    assert f"P0 #{PR} ejected {LANE}" in captured.out


def test_arming_drops_a_landing_or_close_that_predates_the_snapshot_and_keeps_standing_conditions(capsys, tmp_path):
    shell = row_shell(label_head=HEAD, labelled_at="2026-09-30T05:28:00Z")
    shell.stores[LEDGER]["rows"].append({"key": "28008", "fields": {"head": HEAD, "lane": LANE, "state": "open"}})
    shell.ccx_out = event("landed", detail=SQUASH[:9]) + "\n" + event("closed-without-squash", pr="28008") + "\n" + event("conflicting") + "\n"

    assert watch(shell, tmp_path) == 0

    assert capsys.readouterr().out == f"P0 #{PR} conflicting {LANE}\n"
    assert shell.fields(PR)["watch_event"] == "conflicting"
    assert shell.fields(PR)["state"] == "open"
    assert "watch_event" not in shell.fields("28008")
    assert shell.fields("28008")["state"] == "open"
    assert set(json.loads((tmp_path / "watch.json").read_text())["prs"]) == {PR, "28008"}


def test_a_shard_watches_only_its_lanes_and_keeps_its_own_snapshot(tmp_path):
    shell = row_shell()
    shell.stores[LEDGER]["rows"].append({"key": "28008", "fields": {"head": HEAD, "lane": "phase0-rebase", "state": "open"}})

    watch(shell, tmp_path, "--shard", LANE)

    assert shell.ccx_calls[0][4:6] == [PR, "--repo"]
    home = ledger.Path.home() / ".cache" / "ccn-ledger"
    assert ledger.watch_state(LEDGER, None) == home / f"{LEDGER}.watch.json"
    sharded = ledger.watch_state(LEDGER, frozenset({"b", "a"}))
    assert sharded == ledger.watch_state(LEDGER, frozenset({"a", "b"})) != ledger.watch_state(LEDGER, frozenset({"a"}))
    assert sharded.parent == home and sharded.name.startswith(f"{LEDGER}.") and sharded.name.endswith(".watch.json")
    assert len(ledger.watch_state(LEDGER, frozenset(f"lane-{n:03}" for n in range(200))).name) < 255


@pytest.mark.parametrize("snapshot", [{}, {"prs": None}])
def test_a_snapshot_without_prs_arms_like_an_absent_one(capsys, tmp_path, snapshot):
    (tmp_path / "watch.json").write_text(json.dumps(snapshot))
    shell = row_shell()
    shell.ccx_out = event("landed", detail=SQUASH[:9]) + "\n"

    assert watch(shell, tmp_path) == 0

    assert shell.fields(PR)["state"] == "open"
