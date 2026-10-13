from __future__ import annotations

import json
from pathlib import Path

import holds
import pytest


SWITCH_HOLD = "#36525 lands only after the Switch batch (#35413 deletes the SandSQL sandbox exclusion) and without its infra/rows/datadog/sandsql.ts hunk\n"


def run(path: Path, *argv: str) -> int:
    return holds.main(["--file", str(path), *argv])


def test_add_writes_the_two_line_shapes_the_runner_reads(tmp_path):
    path = tmp_path / "holds.md"

    assert run(path, "add", "--pr", "30082", "--reason", "owner review of the replica cutover") == 0
    assert run(path, "add", "--lane", "L23-dashboard-wasm-follower", "--reason", "  waits for   the follower memory fix ") == 0

    assert path.read_text() == "#30082 owner review of the replica cutover\nlane:L23-dashboard-wasm-follower waits for the follower memory fix\n"


@pytest.mark.parametrize("reason", ["ours to land after the replica PR", "ready to merge", "release it", "lifted by the owner", "LAND IT NOW"])
def test_a_pr_line_whose_reason_says_it_should_land_is_refused(tmp_path, capsys, reason):
    path = tmp_path / "holds.md"

    assert run(path, "add", "--pr", "30082", "--reason", reason) == 1

    assert not path.exists()
    assert "holds.py rm --pr 30082" in capsys.readouterr().err


@pytest.mark.parametrize("reason", ["wait for parent 30080 to land", "waits on #30080", "do not land it before parity passes", "hold until the release train clears"])
def test_a_reason_naming_a_dependency_or_a_prohibition_is_a_hold(tmp_path, reason):
    assert run(tmp_path / "holds.md", "add", "--pr", "30082", "--reason", reason) == 0


def test_rm_refuses_to_turn_a_shared_line_into_a_release_line(tmp_path):
    path = tmp_path / "holds.md"
    path.write_text("lane:L28 release #30083 after owner review\n")

    assert run(path, "rm", "--lane", "L28") == 1
    assert path.read_text() == "lane:L28 release #30083 after owner review\n"


@pytest.mark.parametrize("reason", ["same as lane:L28", ""])
def test_a_reason_that_would_hold_something_else_or_nothing_is_refused(tmp_path, reason):
    assert run(tmp_path / "holds.md", "add", "--pr", "30082", "--reason", reason) == 1


@pytest.mark.parametrize(("flag", "value"), [("--pr", "30082a"), ("--lane", "two lanes")])
def test_a_malformed_target_is_refused(tmp_path, flag, value):
    assert run(tmp_path / "holds.md", "add", flag, value, "--reason", "owner review") == 1


def test_a_second_hold_on_the_same_pr_is_refused(tmp_path):
    path = tmp_path / "holds.md"
    path.write_text("#30083 owner review\n")

    assert run(path, "add", "--pr", "#30083", "--reason", "another reason") == 1


def test_a_line_holds_only_its_leading_pr(tmp_path, capsys):
    path = tmp_path / "holds.md"
    path.write_text(SWITCH_HOLD)

    assert [entry.prs for entry in holds.read(path)] == [["36525"]]
    assert run(path, "list") == 0
    assert capsys.readouterr().out == f"{SWITCH_HOLD.rstrip()}\tholds #36525\n"
    assert run(path, "rm", "--pr", "35413") == 1
    assert run(path, "add", "--pr", "35413", "--reason", "owner review") == 0
    assert run(path, "rm", "--pr", "36525") == 0
    assert path.read_text() == "#35413 owner review\n"


def test_rm_drops_a_lone_entry_and_trims_a_shared_line(tmp_path):
    path = tmp_path / "holds.md"
    path.write_text("hold:all owner review\n#30082 lands after #30083\nlane:L28 lane:L29 waits\nrelease #30082 reviewed\n")

    assert run(path, "rm", "--pr", "30082") == 0
    assert run(path, "rm", "--lane", "L28") == 0

    assert path.read_text() == "hold:all owner review\nlane:L29 waits\nrelease #30082 reviewed\n"
    assert run(path, "rm", "--lane", "L28") == 1


def test_list_names_what_each_line_holds_and_reads_a_runner_config(tmp_path, capsys):
    path = tmp_path / "holds.md"
    path.write_text("hold:all owner review\n#30082 owner review\nlane:L28 waits\nrelease #30083 reviewed\n")
    config = tmp_path / "landing.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"holds": str(path)}}))

    assert holds.main(["--config", str(config), "list"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "hold:all owner review\tholds every open row",
        "#30082 owner review\tholds #30082",
        "lane:L28 waits\tholds lane:L28",
        "release #30083 reviewed\treleases #30083 reviewed",
    ]
