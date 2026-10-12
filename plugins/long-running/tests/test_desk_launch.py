"""desk-launch.py against a fake cci drive whose orca runner settles each posted launch."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-launch.py"
spec = importlib.util.spec_from_file_location("desk_launch", SCRIPT)
desk_launch = importlib.util.module_from_spec(spec)
sys.modules["desk_launch"] = desk_launch
spec.loader.exec_module(desk_launch)


def flag(argv: tuple[str, ...], name: str) -> str:
    return argv[argv.index(name) + 1]


class Drive:
    def __init__(self):
        self.records = [
            {"seq": 100, "lane": "root", "kind": "go", "text": "R2025 (11:50 PM) orca-desk: launch old-fix NOW incident xhigh brief=/b.md"},
            {"seq": 101, "lane": "root", "kind": "go", "text": "orca-desk: unhold sandsql-5177"},
        ]
        self.verdicts: dict[str, str] = {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, drive: str, *argv: str) -> str:
        self.calls.append((drive, *argv))
        if argv[0] == "grep":
            return "".join(f"{json.dumps(record)}\n" for record in reversed(self.records) if "orca-desk: " in record["text"])
        if argv[0] == "post":
            record = {"seq": self.records[-1]["seq"] + 1, "lane": flag(argv, "--lane"), "kind": flag(argv, "--kind"), "to": [flag(argv, "--to")], "text": flag(argv, "--text")}
            self.records.append(record)
            key = record["text"].split()[0]
            if key in self.verdicts:
                lane = record["text"].split("launch ")[1].split()[0]
                self.records.append({"seq": record["seq"] + 1, "lane": "desk-runner", "kind": "report", "text": self.verdicts[key].format(lane=lane)})
            return json.dumps(record)
        assert argv[0] == "tail"
        since = int(argv[argv.index("--since") + 1])
        return "".join(f"{json.dumps(record)}\n" for record in self.records if record["seq"] > since and record["lane"] == argv[argv.index("--lane") + 1])


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch) -> Drive:
    fake = Drive()
    monkeypatch.setattr(desk_launch, "cci", fake)
    monkeypatch.setattr(desk_launch.time, "sleep", lambda seconds: None)
    return fake


def briefs(tmp_path: Path) -> tuple[Path, Path]:
    fix, evidence = tmp_path / "fix-brief.md", tmp_path / "evidence-brief.md"
    fix.write_text("fix")
    evidence.write_text("evidence")
    return fix, evidence


def test_an_incident_pair_posts_addressed_launch_lines_under_the_next_keys_and_prints_each_dispatch(drive, tmp_path, capsys):
    fix, evidence = briefs(tmp_path)
    drive.verdicts = {
        "R2026": "LAUNCHED R2026 {lane}: dispatch ctx_fix terminal term_fix",
        "R2027": "LAUNCHED R2027 {lane}: dispatch ctx_ev terminal term_ev",
    }
    assert desk_launch.main(["incident", "inc-app-origin", str(fix), str(evidence)]) == 0
    posts = [call for call in drive.calls if call[1] == "post"]
    assert [flag(call, "--to") for call in posts] == ["orca-desk", "orca-desk"]
    lines = [flag(call, "--text") for call in posts]
    assert lines[0].startswith("R2026 (") and lines[0].endswith(f") orca-desk: launch inc-app-origin-fix NOW incident xhigh brief={fix}")
    assert lines[1].startswith("R2027 (") and lines[1].endswith(f") orca-desk: launch inc-app-origin-evidence NOW incident xhigh brief={evidence}")
    out = capsys.readouterr().out.splitlines()
    assert "inc-app-origin-fix R2026 LAUNCHED R2026 inc-app-origin-fix: dispatch ctx_fix terminal term_fix" in out
    assert "inc-app-origin-evidence R2027 LAUNCHED R2027 inc-app-origin-evidence: dispatch ctx_ev terminal term_ev" in out


def test_a_launch_the_runner_refuses_exits_one_with_its_line(drive, tmp_path, capsys):
    fix, _ = briefs(tmp_path)
    drive.verdicts = {"R2026": "LAUNCH-FAILED desk-lane-{lane}/R2026 {lane}: orca-launch.sh exited 1"}
    assert desk_launch.main(["incident", "inc-x", str(fix)]) == 1
    assert "inc-x-fix R2026 LAUNCH-FAILED desk-lane-inc-x-fix/R2026 inc-x-fix: orca-launch.sh exited 1" in capsys.readouterr().out


def test_an_unsettled_launch_exits_two_at_the_timeout(drive, tmp_path, capsys):
    _, evidence = briefs(tmp_path)
    assert desk_launch.main(["--timeout-seconds", "0", "lane", "slow-restore", "sol", "xhigh", str(evidence)]) == 2
    [post] = [call for call in drive.calls if call[1] == "post"]
    assert flag(post, "--text").endswith(f"orca-desk: launch slow-restore sol xhigh brief={evidence}")
    assert "slow-restore R2026 unsettled after 0s" in capsys.readouterr().out


def test_a_missing_brief_posts_nothing(drive, tmp_path):
    with pytest.raises(SystemExit):
        desk_launch.main(["incident", "inc-x", str(tmp_path / "missing.md")])
    assert [call for call in drive.calls if call[1] == "post"] == []
