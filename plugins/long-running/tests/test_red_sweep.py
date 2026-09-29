from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/red-sweep.sh"
BUILD_URL = "https://buildkite.com/forge/test/builds/{}"
TRUNK_BUILDS = "/pipelines/test/builds?branch=dev&state%5B%5D=passed&state%5B%5D=failed&per_page=1"

GH = """#!/usr/bin/env python3
import os, re, subprocess, sys
state = os.environ["FAKE_STATE"]
endpoint, jq = sys.argv[2], sys.argv[sys.argv.index("--jq") + 1]
path = os.path.join(state, re.sub("[/?&=:%]", "_", endpoint) + ".json")
if not os.path.exists(path):
    sys.exit(1)
sys.stdout.write(subprocess.run(["jq", "-r", jq], stdin=open(path), capture_output=True, text=True, check=True).stdout)
"""

BK = """#!/usr/bin/env python3
import os, re, sys
state = os.environ["FAKE_STATE"]
with open(os.path.join(state, "bk-calls"), "a") as calls:
    calls.write(sys.argv[2] + "\\n")
path = os.path.join(state, re.sub("[/?&=:%]", "_", sys.argv[2]) + ".json")
if not os.path.exists(path):
    sys.exit(1)
sys.stdout.write(open(path).read())
"""

SLEEP = """#!/bin/sh
echo x >> "$FAKE_STATE/sweeps"
[ ! -f "$FAKE_STATE/between" ] || { sh "$FAKE_STATE/between"; rm "$FAKE_STATE/between"; }
[ "$(wc -l < "$FAKE_STATE/sweeps")" -lt "${FAKE_SWEEPS:-1}" ] || kill -TERM "$PPID"
"""


def name(endpoint: str) -> str:
    return re.sub("[/?&=:%]", "_", endpoint) + ".json"


def job(step: str, state: str = "failed", soft: bool = False) -> dict:
    return {"type": "script", "step_key": step, "name": step.title(), "state": state, "soft_failed": soft}


class Forge:
    def __init__(self, root: Path):
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for tool, body in {"gh": GH, "bk": BK, "sleep": SLEEP}.items():
            (bin_dir / tool).write_text(body)
            (bin_dir / tool).chmod(0o755)
        self.list = root / "list"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "RED_SWEEP_REPO": "o/r",
            "RED_SWEEP_TRUNK": "dev",
            "RED_SWEEP_STATE": str(root / "sweep" / "seen.txt"),
            "RED_SWEEP_AGE_MINUTES": "0",
            "RED_SWEEP_INTERVAL": "7",
        }

    def write(self, endpoint: str, payload: object) -> None:
        (self.state / name(endpoint)).write_text(json.dumps(payload))

    def pull(self, n: int, sha: str, build: int, jobs: list[dict]) -> None:
        self.write(f"repos/o/r/pulls/{n}", {"head": {"sha": sha}, "mergeable_state": "clean", "updated_at": "2026-09-01T00:00:00Z", "title": f"pr {n}"})
        self.write(f"repos/o/r/commits/{sha}/status", {"state": "failure", "statuses": [{"state": "failure", "target_url": BUILD_URL.format(build)}]})
        self.write(f"/pipelines/test/builds/{build}", {"jobs": jobs})
        with self.list.open("a") as handle:
            handle.write(f"{n}\n")

    def trunk(self, jobs: list[dict]) -> None:
        self.write(TRUNK_BUILDS, [{"jobs": jobs}])

    def run(self, sweeps: int = 1) -> list[str]:
        env = dict(self.env, FAKE_SWEEPS=str(sweeps))
        result = subprocess.run(["sh", str(SCRIPT), str(self.list)], env=env, capture_output=True, text=True, timeout=60)
        return result.stdout.splitlines()

    @property
    def bk_calls(self) -> list[str]:
        path = self.state / "bk-calls"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def forge(tmp_path):
    return Forge(tmp_path)


def test_a_step_red_on_the_trunk_too_prints_dev_red_instead_of_stalled_red(forge):
    forge.pull(101, "a" * 40, 9001, [job("infra-plan-observability"), job("biome", "passed")])
    forge.trunk([job("infra-plan-observability"), job("tsc", "passed")])

    assert forge.run() == ["DEV-RED #101 aaaaaaaa infra-plan-observability"]


def test_a_step_only_the_pr_fails_is_still_its_own_red(forge):
    forge.pull(102, "b" * 40, 9002, [job("infra-plan-observability"), job("tsc")])
    forge.trunk([job("infra-plan-observability")])

    [line] = forge.run()
    assert line.startswith("STALLED-RED #102 bbbbbbbb failure/clean head ")


def test_soft_failures_on_the_trunk_do_not_excuse_the_pr(forge):
    forge.pull(103, "c" * 40, 9003, [job("e2e")])
    forge.trunk([job("e2e", soft=True)])

    [line] = forge.run()
    assert line.startswith("STALLED-RED #103 cccccccc failure/clean head ")


def test_dev_red_prints_once_per_head_and_stalled_red_follows_once_the_trunk_passes(forge):
    forge.pull(104, "d" * 40, 9004, [job("infra-plan-observability")])
    forge.trunk([job("infra-plan-observability")])
    (forge.state / "between").write_text(f"echo '{json.dumps([{'jobs': [job('infra-plan-observability', 'passed')]}])}' > '{forge.state / name(TRUNK_BUILDS)}'\n")

    lines = forge.run(sweeps=3)

    assert lines[0] == "DEV-RED #104 dddddddd infra-plan-observability"
    assert lines[1].startswith("STALLED-RED #104 dddddddd failure/clean head ")
    assert len(lines) == 2


def test_the_trunk_build_is_read_once_per_sweep(forge):
    forge.pull(105, "e" * 40, 9005, [job("infra-plan-observability")])
    forge.pull(106, "f" * 40, 9006, [job("infra-plan-observability")])
    forge.trunk([job("infra-plan-observability")])

    assert forge.run() == ["DEV-RED #105 eeeeeeee infra-plan-observability", "DEV-RED #106 ffffffff infra-plan-observability"]
    assert forge.bk_calls.count(TRUNK_BUILDS) == 1


def test_a_red_outside_buildkite_is_stalled_red(forge):
    forge.pull(107, "0" * 40, 9007, [job("tsc")])
    forge.write(f"repos/o/r/commits/{'0' * 40}/status", {"state": "failure", "statuses": [{"state": "failure", "target_url": "https://github.com/o/r/actions/runs/1"}]})

    [line] = forge.run()
    assert line.startswith("STALLED-RED #107 00000000 failure/clean head ")
    assert forge.bk_calls == []


def test_a_second_failing_build_the_trunk_passes_keeps_the_pr_its_own_red(forge):
    forge.pull(108, "1" * 40, 9008, [job("infra-plan-observability")])
    forge.write(
        f"repos/o/r/commits/{'1' * 40}/status",
        {
            "state": "failure",
            "statuses": [
                {"state": "failure", "target_url": BUILD_URL.format(9008)},
                {"state": "failure", "target_url": "https://buildkite.com/forge/deploy/builds/77"},
                {"state": "failure", "target_url": BUILD_URL.format(9008) + "#job"},
            ],
        },
    )
    forge.write("/pipelines/deploy/builds/77", {"jobs": [job("helm-diff")]})
    forge.write("/pipelines/deploy/builds?branch=dev&state%5B%5D=passed&state%5B%5D=failed&per_page=1", [{"jobs": [job("helm-diff", "passed")]}])
    forge.trunk([job("infra-plan-observability")])

    [line] = forge.run()
    assert line.startswith("STALLED-RED #108 11111111 failure/clean head ")


def test_every_failing_build_red_on_its_own_trunk_prints_every_step(forge):
    forge.pull(109, "2" * 40, 9009, [job("infra-plan-observability")])
    forge.write(
        f"repos/o/r/commits/{'2' * 40}/status",
        {"state": "failure", "statuses": [{"state": "failure", "target_url": BUILD_URL.format(9009)}, {"state": "failure", "target_url": "https://buildkite.com/forge/deploy/builds/78"}]},
    )
    forge.write("/pipelines/deploy/builds/78", {"jobs": [job("helm-diff")]})
    forge.write("/pipelines/deploy/builds?branch=dev&state%5B%5D=passed&state%5B%5D=failed&per_page=1", [{"jobs": [job("helm-diff")]}])
    forge.trunk([job("infra-plan-observability")])

    assert forge.run() == ["DEV-RED #109 22222222 helm-diff,infra-plan-observability"]
