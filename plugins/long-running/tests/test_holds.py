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


@pytest.mark.parametrize(
    ("line", "argv"),
    [
        ("lane:L28 release lane:L29 after owner review", ("--lane", "L28")),
        ("#30082 #30083 lane:L28 owner review", ("--pr", "30082")),
        ("hold:all lane:L28", ("--lane", "L28")),
    ],
)
def test_rm_refuses_to_leave_a_release_line_a_new_hold_or_a_line_no_form_matches(tmp_path, line, argv):
    path = tmp_path / "holds.md"
    path.write_text(f"{line}\n")

    assert run(path, "rm", *argv) == 1
    assert path.read_text() == f"{line}\n"


@pytest.mark.parametrize(("line", "argv"), [("lane:L28 #30083 waits for review", ("--lane", "L28")), ("#30082 release #30083 after owner review", ("--pr", "30082"))])
def test_rm_drops_a_line_whose_only_subject_it_removes(tmp_path, line, argv):
    path = tmp_path / "holds.md"
    path.write_text(f"{line}\n")

    assert run(path, "rm", *argv) == 0
    assert path.read_text() == ""


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


@pytest.mark.parametrize(
    "line",
    ["L34-publish HOLD (L34 re-land): no enqueue until root posts 'L32 pass done'", "HOLD L34-publish (L34 re-land): no enqueue until root posts 'L32 pass done'"],
)
def test_a_lane_hold_line_reads_as_its_lane_and_rm_drops_it(tmp_path, capsys, line):
    path = tmp_path / "holds.md"
    path.write_text(f"{line}\n")

    assert [(entry.lanes, entry.prs, entry.unknown) for entry in holds.read(path)] == [(["L34-publish"], [], False)]
    assert run(path, "list") == 0
    assert capsys.readouterr().out == f"{line}\tholds lane:L34-publish\n"
    assert run(path, "add", "--lane", "L34-publish", "--reason", "owner review") == 1
    assert run(path, "rm", "--lane", "L34-publish") == 0
    assert path.read_text() == ""


@pytest.mark.parametrize(
    "line",
    ["#30082 owner review", "lane:L28 waits", "L28 HOLD waits", "HOLD L28 waits", "hold:all owner review", "release #30082 reviewed", "release lane:L28", "", "   "],
)
def test_every_form_and_a_blank_line_is_known(line):
    assert not holds.Entry(line).unknown


@pytest.mark.parametrize("line", ["36935 waits for the L32 pass", " #30082 owner review", "L28: waits", "hold L28 waits", "L28 on HOLD", "L28 HOLD: waits", "hold:all", "HOLD"])
def test_a_line_no_form_matches_is_unknown(line):
    assert holds.Entry(line).unknown


def test_list_says_a_line_no_form_matches_stops_the_runner(tmp_path, capsys):
    path = tmp_path / "holds.md"
    path.write_text("\n36935 waits for the L32 pass\n")

    assert [entry.unknown for entry in holds.read(path)] == [False, True]
    assert run(path, "list") == 0
    assert capsys.readouterr().out == "36935 waits for the L32 pass\tmatches no form; the landing runner enqueues nothing until it is fixed\n"


def test_rowless_names_each_lane_line_whose_lane_no_row_carries_in_any_state():
    rows = {"36935": {"lane": "ship-l34-reland", "state": "open"}, "36397": {"lane": "L34-publish", "state": "landed"}}
    entries = [
        holds.Entry("L34-publish HOLD waits"),
        holds.Entry("HOLD L35-sizing waits"),
        holds.Entry("lane:ship-l34-reland lane:L36-unopened waits"),
        holds.Entry("#36934 owner review"),
        holds.Entry("release lane:L37-unopened"),
        holds.Entry("36935 waits for the L32 pass"),
    ]

    assert holds.rowless(entries, rows) == {"HOLD L35-sizing waits": ["L35-sizing"], "lane:ship-l34-reland lane:L36-unopened waits": ["L36-unopened"]}


def test_held_resolves_lane_lines_to_open_rows_and_a_rowless_lane_to_nothing():
    rows = {
        "36934": {"lane": "L35-sizing", "state": "open"},
        "36935": {"lane": "L34-publish", "state": "open"},
        "36397": {"lane": "L34-publish", "state": "landed"},
    }

    assert holds.held([holds.Entry("L34-publish HOLD waits")], rows) == {"36935"}
    assert holds.held([holds.Entry("HOLD L35-sizing waits"), holds.Entry("lane:L36-unopened waits"), holds.Entry("#30082 owner review")], rows) == {"36934", "30082"}
    assert holds.held([holds.Entry("hold:all owner review"), holds.Entry("release lane:L34-publish"), holds.Entry("release #30082 reviewed")], rows) == {"36934"}


def test_every_line_form_written_through_the_cli_parses_back_to_what_it_holds(tmp_path):
    path = tmp_path / "holds.md"
    rows = {
        "30082": {"lane": "L28", "state": "open"},
        "30083": {"lane": "L29", "state": "open"},
        "30084": {"lane": "L30", "state": "open"},
    }

    assert run(path, "add", "--all", "--reason", "every PR waits for the owner") == 0
    assert run(path, "release", "--pr", "30082", "--reason", "owner reviewed it") == 0
    assert run(path, "release", "--lane", "L29", "--reason", "owner reviewed the lane") == 0

    assert path.read_text() == "hold:all every PR waits for the owner\nrelease #30082 owner reviewed it\nrelease lane:L29 owner reviewed the lane\n"
    entries = holds.read(path)
    assert [entry.holds_all for entry in entries] == [True, False, False] and [entry.release for entry in entries] == [False, True, True]
    assert holds.held(entries, rows) == {"30084"} and holds.released(entries, rows) == {"30082", "30083"}


def test_rm_takes_back_a_hold_all_or_a_release_line(tmp_path):
    path = tmp_path / "holds.md"
    path.write_text("hold:all every PR waits\nrelease #30082 owner reviewed it\n#30085 owner review\n")

    assert run(path, "rm", "--pr", "30082", "--release") == 0
    assert run(path, "rm", "--all") == 0

    assert path.read_text() == "#30085 owner review\n"


@pytest.mark.parametrize(
    ("argv", "line"),
    [
        (("add", "--all", "--reason", "owner review"), "hold:all already holding"),
        (("release", "--pr", "30082", "--reason", "again"), "release #30082 first reason"),
        (("release", "--lane", "L29", "--reason", "and lane:L30"), None),
    ],
)
def test_a_second_hold_all_a_second_release_or_a_reason_naming_another_subject_is_refused(tmp_path, argv, line):
    path = tmp_path / "holds.md"
    if line:
        path.write_text(f"{line}\n")

    assert run(path, *argv) == 1

    assert (path.read_text() if path.exists() else None) == (f"{line}\n" if line else None)


def test_lift_releases_like_release_with_a_reason_that_names_prs_or_with_none(tmp_path):
    path = tmp_path / "holds.md"
    path.write_text("hold:all every PR waits\n")

    assert run(path, "lift", "--pr", "30082", "--reason", "owner tapped #30082 with #30090 at 2:16 AM") == 0
    assert run(path, "release", "--lane", "L29") == 0

    assert path.read_text() == "hold:all every PR waits\nrelease #30082 owner tapped #30082 with #30090 at 2:16 AM\nrelease lane:L29\n"
    rows = {"30082": {"lane": "L1"}, "30090": {"lane": "L2"}, "30091": {"lane": "L29"}}
    assert holds.released(holds.read(path), rows) == {"30082", "30091"}
