from __future__ import annotations

from datetime import datetime, timedelta, timezone

import ledger
import pytest
from conftest import LEDGER, FakeShell

REPO = "Forge-AI/monorepo"
HOT = ["infra/ci/src/pipelines/release/**", "infra/ci/src/verbs/release*.ts", "infra/ci/test/release-*.test.ts", "infra/engine.ts"]
INDEX = "infra/ci/src/pipelines/release/index.ts"
VERB = "infra/ci/src/verbs/release.ts"
CLOSURE = "infra/ci/test/release-closure.test.ts"
COLD = "api/src/entities/Team.ts"


def stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(pr: str, lane: str = "lane-a", **fields) -> dict:
    base = {
        "state": "open",
        "head": f"{pr}".ljust(40, "a"),
        "base": "dev",
        "branch": f"{lane}/{pr}",
        "lane": lane,
        "reported_head": f"{pr}".ljust(40, "a"),
        "test_state": "success",
        "mergeable_state": "clean",
    }
    return {"key": pr, "fields": base | fields}


def run(shell, *argv) -> int:
    return ledger.main(list(argv), shell)


def train(shell, capsys, *extra) -> list[str]:
    capsys.readouterr()
    assert run(shell, "train", "--repo", REPO, "--ledger", LEDGER, "--paths", *HOT, *extra) == 0
    return capsys.readouterr().out.splitlines()


def approve(shell, *prs: str) -> None:
    for pr in prs:
        shell.reviews[pr] = [{"state": "APPROVED", "commit_id": "x", "user": {"login": "forge-pr-reviewer[bot]"}}]


def test_train_orders_green_before_dirty_then_fewest_overlaps_then_oldest(capsys):
    shell = FakeShell(
        rows=[
            row("27500", mergeable_state="dirty"),
            row("27510"),
            row("27520"),
            row("27530"),
        ]
    )
    shell.pr_files |= {"27500": [INDEX], "27510": [INDEX, VERB], "27520": [VERB], "27530": [CLOSURE]}
    approve(shell, "27500")

    lines = train(shell, capsys)

    assert lines == [
        f"car 1 #27530 {'27530'.ljust(40, 'a')[:9]} green overlaps 0 lane-a lane-a/27530",
        f"car 2 #27520 {'27520'.ljust(40, 'a')[:9]} green overlaps 1 lane-a lane-a/27520",
        f"car 3 #27510 {'27510'.ljust(40, 'a')[:9]} green overlaps 2 lane-a lane-a/27510",
        f"car 4 #27500 {'27500'.ljust(40, 'a')[:9]} dirty overlaps 1 lane-a lane-a/27500",
        "ccx vcs stack rebase --linearize lane-a/27530,lane-a/27520,lane-a/27510,lane-a/27500",
    ]


def test_train_leaves_out_rows_off_the_hot_set_held_queued_or_untracked(capsys):
    shell = FakeShell(
        rows=[
            row("27600"),
            row("27601"),
            row("27602", hold_reason="render not approved", hold_since="x", hold_until=stamp(timedelta(hours=2))),
            row("27603", labels="merge"),
            row("27604", reported_head="", registered=""),
            row("27605", state="landed"),
        ]
    )
    shell.pr_files |= {pr: [INDEX] for pr in ("27601", "27602", "27603", "27604", "27605")} | {"27600": [COLD]}

    lines = train(shell, capsys)

    assert [line.split()[2] for line in lines if line.startswith("car ")] == ["#27601"]
    read = {endpoint.split("/")[4] for endpoint in shell.endpoints() if "/files?" in endpoint}
    assert read == {"27600", "27601"}


def test_train_reads_every_page_of_a_prs_files(capsys):
    shell = FakeShell(rows=[row("27650")])
    shell.pr_files["27650"] = [f"docs/{index}.md" for index in range(100)] + [INDEX]

    lines = train(shell, capsys)

    assert lines[0].startswith("car 1 #27650 ")


def test_train_names_hot_rows_that_are_not_ready_and_never_seats_them(capsys):
    shell = FakeShell(rows=[row("27700", test_state="failure"), row("27701", mergeable_state="dirty"), row("27702", mergeable_state="blocked")])
    shell.pr_files |= {pr: [VERB] for pr in ("27700", "27701", "27702")}

    lines = train(shell, capsys)

    assert lines == [
        f"no ready row touches {' '.join(HOT)}",
        "not ready: #27700 failure/clean | #27701 success/dirty | #27702 success/blocked",
    ]


def test_train_caps_the_cars_and_names_the_next_train(capsys):
    shell = FakeShell(rows=[row(str(pr)) for pr in range(27800, 27808)])
    shell.pr_files |= {str(pr): [f"infra/ci/src/pipelines/release/f{pr}.ts"] for pr in range(27800, 27808)}

    lines = train(shell, capsys)

    assert [line.split()[2] for line in lines if line.startswith("car ")] == [f"#{pr}" for pr in range(27800, 27806)]
    assert "next train: #27806 #27807" in lines

    lines = train(shell, capsys, "--cars", "2")
    assert "next train: #27802 #27803 #27804 #27805 #27806 #27807" in lines


def test_train_reads_approvals_only_for_dirty_rows(capsys):
    shell = FakeShell(rows=[row("27900"), row("27901", mergeable_state="dirty")])
    shell.pr_files |= {"27900": [INDEX], "27901": [INDEX]}
    approve(shell, "27901")

    train(shell, capsys)

    assert {endpoint.split("/")[4] for endpoint in shell.endpoints() if "/reviews" in endpoint} == {"27901"}


def test_train_never_writes(capsys):
    shell = FakeShell(rows=[row("27900")])
    shell.pr_files["27900"] = [INDEX]

    train(shell, capsys)

    assert not [argv for argv in shell.calls if argv[:4] == ["ccn", "ledger", "row", "set"] or "--method" in argv]


def test_train_requires_paths():
    with pytest.raises(SystemExit):
        run(FakeShell(), "train", "--repo", REPO, "--ledger", LEDGER)
