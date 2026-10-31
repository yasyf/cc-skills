from __future__ import annotations

import json
from pathlib import Path

import endstate
import pytest

DESIGN_MAC = {
    "primaries": "1",
    "replicas": "1",
    "serve-cpu-percent": "0.4",
    "serve-rss-mb": "180",
    "primary-rss-mb": "60",
    "litestream-rss-mb": "30",
    "read-path-local": "primary",
    "read-path-shared": "reader",
    "read-path-cloud": "reader",
    "failed-opens-offline": "0",
    "failed-opens-online": "0",
    "delete-calls": "0",
}
DRIVE_MAC = DESIGN_MAC | {"replicas": "2", "serve-rss-mb": "410", "read-path-local": "replica", "failed-opens-offline": "2"}
DESIGN_POD = {
    "work-discovery-calls": "0",
    "lock-waits": "0",
    "writers-per-agent": "1",
    "delete-calls": "0",
    "permission-rows": "4 env",
    "queue-contents": "empty",
}


def reading(build: str, mac: dict, pod: dict) -> dict:
    return {"mac": "ym-mbp", "build": build, "rows": mac, "pods": {"plat-usw2-prod": pod}}


@pytest.mark.parametrize(
    ("prefix", "read", "differs"),
    [
        ("r1a", reading("drive", DRIVE_MAC, DESIGN_POD), []),
        ("r1a", reading("drive", DRIVE_MAC, DESIGN_POD | {"work-discovery-calls": "12"}), ["the pod on plat-usw2-prod reads work-discovery-calls 12, and the design is 0"]),
        ("r1a", reading("drive", DRIVE_MAC, DESIGN_POD | {"lock-waits": "3"}), ["the pod on plat-usw2-prod reads lock-waits 3, and the design is 0"]),
        ("r1a", reading("r1b", DESIGN_MAC, DESIGN_POD), ["after r1a the Mac runs the drive build, and ym-mbp runs R1b"]),
        ("r1b", reading("r1b", DESIGN_MAC, DESIGN_POD), []),
        ("r1b", reading("r1b", DESIGN_MAC | {"primaries": "2"}, DESIGN_POD), ["ym-mbp reads primaries 2, and the design is 1"]),
        ("r1b", reading("r1b", DESIGN_MAC | {"serve-rss-mb": "260"}, DESIGN_POD), ["ym-mbp reads serve-rss-mb 260, and the design is at most 200"]),
        ("r1b", reading("r1b", DESIGN_MAC | {"failed-opens-offline": "1"}, DESIGN_POD), ["ym-mbp reads failed-opens-offline 1, and the design is 0"]),
        ("r1b", reading("drive", DRIVE_MAC, DESIGN_POD), ["after r1b the reading is of a pinned internal Mac on R1b, and ym-mbp runs the drive build"]),
        ("promoted", reading("r1b", DESIGN_MAC | {"read-path-shared": "serve"}, DESIGN_POD), ["ym-mbp reads read-path-shared serve, and the design is reader"]),
        ("promoted", reading("drive", DRIVE_MAC, DESIGN_POD), []),
        ("promoted", reading("drive", DRIVE_MAC, DESIGN_POD | {"writers-per-agent": "2"}), ["the pod on plat-usw2-prod reads writers-per-agent 2, and the design is 1"]),
    ],
)
def test_each_row_off_its_prefixes_end_state_is_named_and_a_matching_reading_passes(prefix: str, read: dict, differs: list[str]) -> None:
    assert endstate.differences(prefix, read) == differs


def test_a_reading_missing_a_row_or_a_cluster_is_refused_before_it_is_graded() -> None:
    missing = {name: value for name, value in DESIGN_MAC.items() if name != "primaries"}

    assert endstate.gaps("r1b", reading("r1b", missing, DESIGN_POD), ["plat-usw2-prod", "sofi-use1-prod"]) == [
        "the Mac reads no primaries",
        "no pod read on sofi-use1-prod",
    ]
    assert endstate.gaps("r3", reading("r1b", DESIGN_MAC | {"followers": "0"}, DESIGN_POD), []) == [
        "prefix 'r3' is none of r1a, r1b, promoted",
        "the Mac has no row followers",
    ]


def test_grade_exits_non_zero_naming_drift_and_zero_on_a_matching_reading(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    drifted = tmp_path / "drifted.json"
    drifted.write_text(json.dumps(reading("r1b", DESIGN_MAC | {"primaries": "2"}, DESIGN_POD)))
    matched = tmp_path / "matched.json"
    matched.write_text(json.dumps(reading("r1b", DESIGN_MAC, DESIGN_POD)))

    assert endstate.main(["grade", "--prefix", "r1b", "--reading", str(drifted), "--cluster", "plat-usw2-prod"]) == 1
    assert "ym-mbp reads primaries 2, and the design is 1" in capsys.readouterr().out
    assert endstate.main(["grade", "--prefix", "r1b", "--reading", str(matched), "--cluster", "plat-usw2-prod"]) == 0


def check(minutes: int, failed: str = "") -> dict:
    return {
        "component": "sand",
        "since": "2026-10-30T18:00:00.123456789Z",
        "until": f"2026-10-30T18:{minutes:02d}:00.5Z",
        "readings": [{"series": {"name": "turns", "query": "q"}, "peak": 4}, {"series": {"name": "open errors", "query": "e"}, "failed": failed}],
    }


def test_a_watch_under_thirty_minutes_since_the_last_cut_refuses() -> None:
    assert endstate.watch_refusals(check(20)) == ["the watch read 20 of 30 minutes since the last cut; watch again from 2026-10-30T18:30:00.123456+00:00"]


def test_a_watch_whose_series_refuses_refuses_and_a_clean_thirty_minutes_passes() -> None:
    assert endstate.watch_refusals(check(45, "failing: peak 3 above 0")) == ["open errors: failing: peak 3 above 0"]
    assert endstate.watch_refusals(check(31)) == []
