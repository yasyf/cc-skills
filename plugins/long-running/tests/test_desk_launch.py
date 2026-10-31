"""desk-launch.py against a fake cci drive whose orca runner settles each posted launch."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-launch.py"
spec = importlib.util.spec_from_file_location("desk_launch", SCRIPT)
desk_launch = importlib.util.module_from_spec(spec)
sys.modules["desk_launch"] = desk_launch
spec.loader.exec_module(desk_launch)


def flag(argv: tuple[str, ...], name: str) -> str:
    return argv[argv.index(name) + 1]


def alive(at: datetime) -> dict:
    attrs = {"pid": "4242", "version": "0.7.500", "config": "/prog/runner.json", "terminal": "term_root", "restart": "nohup desk-runner.py run --config /prog/runner.json --desk orca &"}
    return {"seq": 99, "lane": "desk-runner", "kind": "state", "topic": "orca", "to": ["drive-watchdog@prog"], "at": at.isoformat(), "text": "orca alive 11:20 PM pass 3", "fields": {"attrs": attrs}}


class Drive:
    def __init__(self):
        self.records = [
            alive(datetime.now(timezone.utc)),
            {"seq": 100, "lane": "root", "kind": "go", "to": ["orca-desk"], "text": "R2025 (11:50 PM) orca-desk: launch old-fix NOW incident xhigh brief=/b.md"},
            {"seq": 101, "lane": "root", "kind": "go", "to": ["orca-desk"], "text": "orca-desk: unhold sandsql-5177"},
        ]
        self.verdicts: dict[str, str] = {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, drive: str, *argv: str) -> str:
        self.calls.append((drive, *argv))
        if argv[0] == "grep":
            match = (lambda record: flag(argv, "--to") in record.get("to", [])) if "--to" in argv else (lambda record: record["lane"] == flag(argv, "--lane"))
            return "".join(f"{json.dumps(record)}\n" for record in reversed(self.records) if match(record) and re.search(argv[1], record["text"]))
        if argv[0] == "post":
            record = {"seq": self.records[-1]["seq"] + 1, "lane": flag(argv, "--lane"), "kind": flag(argv, "--kind"), "to": [flag(argv, "--to")], "text": flag(argv, "--text")}
            self.records.append(record)
            key = record["text"].split()[0]
            if key in self.verdicts:
                lane = record["text"].split("launch ")[1].split()[0]
                self.records.append({"seq": record["seq"] + 1, "lane": "desk-runner", "kind": "report", "text": self.verdicts[key].format(lane=lane)})
            return json.dumps(record)
        assert argv[0] == "tail"
        since = int(flag(argv, "--since-seq"))
        fields = [arg[2:].split("=", 1) for arg in argv if arg.startswith(("--kind=", "--topic="))]
        found = [
            record
            for record in self.records
            if record["seq"] > since
            and record["lane"] == flag(argv, "--lane")
            and ("--to" not in argv or flag(argv, "--to") in record.get("to", []))
            and all(record.get(name) == value for name, value in fields)
        ]
        if "--limit" in argv:
            found = found[-int(flag(argv, "--limit")) :]
        return "".join(f"{json.dumps(record)}\n" for record in found)


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "prog"
    (directory / "inbox").mkdir(parents=True)
    monkeypatch.setenv(desk_launch.registry.SESSION_ENV, "s1")
    monkeypatch.setattr(desk_launch.registry, "drives", lambda: [{"drive": "prog", "sessions": ["s1"], "state_dir": str(directory)}])
    return directory


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch, state: Path) -> Drive:
    fake = Drive()
    monkeypatch.setattr(desk_launch, "cci", fake)
    monkeypatch.setattr(desk_launch.time, "sleep", lambda seconds: None)
    return fake


def posted(drive: Drive) -> list[str]:
    return [flag(call, "--text") for call in drive.calls if call[1] == "post"]


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
    assert lines[0].startswith("R2026 (") and lines[0].endswith(f") orca-desk: launch inc-app-origin-fix NOW incident xhigh role=fix brief={fix}")
    assert lines[1].startswith("R2027 (") and lines[1].endswith(f") orca-desk: launch inc-app-origin-evidence NOW incident xhigh role=evidence brief={evidence}")
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
    assert desk_launch.main(["lane", "slow-restore", "sol", "xhigh", str(evidence), "--timeout-seconds", "0"]) == 2
    [post] = [call for call in drive.calls if call[1] == "post"]
    assert flag(post, "--text").endswith(f"orca-desk: launch slow-restore sol xhigh brief={evidence}")
    assert "slow-restore R2026 unsettled after 0s" in capsys.readouterr().out


def test_a_missing_brief_posts_nothing(drive, tmp_path):
    with pytest.raises(SystemExit):
        desk_launch.main(["incident", "inc-x", str(tmp_path / "missing.md")])
    assert [call for call in drive.calls if call[1] == "post"] == []


LOAD_HIGH = "LOAD-HIGH load:2026-10-14T03:0 runner: load 42 above 32 cores: Orca launches are refused while it lasts"
ON_LOAD = "LAUNCH-FAILED desk-lane-inc-x-fix/R474 inc-x-fix: R474 refused on load 42 above 32 cores; nothing was launched, and the root launches it in-process or once the load is moved"


def runner(drive: Drive, *texts: str) -> None:
    for text in texts:
        drive.records.append({"seq": drive.records[-1]["seq"] + 1, "lane": "desk-runner", "kind": "defect", "text": text})


@pytest.mark.parametrize("lane", ["inc-x", "inc-x-fix"])
def test_a_load_refusal_of_the_fix_lane_clears_an_in_process_launch(drive, capsys, lane):
    runner(drive, LOAD_HIGH, ON_LOAD)
    assert desk_launch.main(["refused", lane, "--drive", "d"]) == 0
    assert capsys.readouterr().out == f"{ON_LOAD}\n"
    [grep] = [call for call in drive.calls if call[1] == "grep"]
    assert flag(grep, "--lane") == "desk-runner" and flag(grep, "--since") == "30m" and grep[0] == "d"


def test_load_high_clears_a_lane_the_runner_has_no_verdict_for(drive, capsys):
    runner(drive, LOAD_HIGH, "LAUNCHED R12 other-fix: dispatch ctx_o terminal term_o")
    assert desk_launch.main(["refused", "inc-x-fix", "--drive", "d"]) == 0
    assert capsys.readouterr().out == f"{LOAD_HIGH}\n"


@pytest.mark.parametrize(
    "texts",
    [
        (),
        (ON_LOAD, "LAUNCHED R475 inc-x-fix: dispatch ctx_fix terminal term_fix"),
        (LOAD_HIGH, "LAUNCH-FAILED desk-lane-inc-x-fix/R475 inc-x-fix: orca-launch.sh exited 1"),
    ],
)
def test_no_load_refusal_as_the_lanes_newest_verdict_exits_one(drive, capsys, texts):
    runner(drive, *texts)
    assert desk_launch.main(["refused", "inc-x", "--drive", "d"]) == 1
    assert capsys.readouterr().out == ""


def test_a_session_reads_its_drive_from_the_registry(drive, monkeypatch):
    monkeypatch.setattr(desk_launch.registry, "find", lambda name, session: {"state_dir": "/s/prog"} if session == "s1" else None)
    runner(drive, ON_LOAD)
    assert desk_launch.main(["refused", "inc-x", "--session", "s1"]) == 0
    assert [call[0] for call in drive.calls if call[1] == "grep"] == ["prog"]
    with pytest.raises(SystemExit, match="no drive in the registry claims session s2"):
        desk_launch.main(["refused", "inc-x", "--session", "s2"])


def test_the_next_key_reads_only_cci_and_never_the_desk_inbox_file(drive, state, tmp_path):
    inbox = state / "inbox" / "orca-desk.md"
    (archive := inbox.with_name("orca-desk.md.archive")).mkdir()
    (archive / "2026-10-16.md").write_text("- R3001 (9:00 PM) orca-desk: relay to a: go\n")
    inbox.write_text("R2999 (12:38 AM) orca-desk: launch by-hand NOW opus xhigh brief=/b.md\norca-desk: alert m1 https://x :: fired\n")
    _, evidence = briefs(tmp_path)
    desk_launch.main(["lane", "next-lane", "opus", "xhigh", str(evidence), "--timeout-seconds", "0"])
    assert posted(drive)[0].startswith("R2026 (")


@pytest.mark.parametrize(
    ("argv", "line"),
    [
        (["relay", "lane-a,lane-b", "restack onto dev"], "orca-desk: relay to lane-a, lane-b: restack onto dev"),
        (["hold", "sandsql-5177", "root", "sand releases until #12 lands"], "orca-desk: hold sandsql-5177 owner=root :: sand releases until #12 lands"),
        (["unhold", "sandsql-5177"], "orca-desk: unhold sandsql-5177"),
        (["withdraw", "R2025"], "orca-desk: withdraw R2025"),
    ],
)
def test_each_keyed_line_posts_under_the_next_key_and_prints_it_without_waiting(drive, capsys, argv, line):
    assert desk_launch.main(argv) == 0
    [text] = posted(drive)
    assert re.fullmatch(rf"R2026 \(\d{{1,2}}:\d\d [AP]M\) {re.escape(line)}", text)
    assert [call[1] for call in drive.calls] == ["tail", "grep", "post"]
    assert capsys.readouterr().out == f"posted R2026 as cci #102: {text}\n"


def test_a_drive_the_registry_lacks_posts_nothing(drive, monkeypatch):
    monkeypatch.setattr(desk_launch.registry, "drives", lambda: [])
    with pytest.raises(SystemExit, match="no drive in the registry is bound to session s1"):
        desk_launch.main(["unhold", "sandsql-5177"])
    assert posted(drive) == []


@pytest.mark.parametrize(
    ("argv", "instead"),
    [
        (["incident", "inc-x", "{fix}", "{evidence}"], "spawn an Agent named `inc-x-fix` and one named `inc-x-evidence` instead"),
        (["incident", "inc-x", "{fix}"], "spawn an Agent named `inc-x-fix` instead"),
        (["lane", "solo", "opus", "xhigh", "{fix}"], "spawn an Agent named `solo` instead"),
        (["relay", "lane-a", "go"], "nothing was posted"),
    ],
)
@pytest.mark.parametrize("last", [None, timedelta(minutes=6)], ids=["never", "stale"])
def test_a_drive_whose_orca_runner_posted_no_recent_alive_record_refuses_in_one_line_naming_the_agent_fallback(drive, state, tmp_path, argv, instead, last):
    fix, evidence = briefs(tmp_path)
    drive.records = [record for record in drive.records if record["lane"] != "desk-runner"]
    if last:
        drive.records.insert(0, alive(datetime.now(timezone.utc) - last))
    with pytest.raises(SystemExit) as refused:
        desk_launch.main([arg.format(fix=fix, evidence=evidence) for arg in argv])
    assert str(refused.value).startswith("refused: no orca desk runner has posted an orca alive record on cci drive prog in the last five minutes (")
    assert ("(never)" in str(refused.value)) is (last is None)
    assert str(refused.value).endswith(f"nothing reads orca-desk lines; {instead}")
    assert "\n" not in str(refused.value)
    assert [call[1] for call in drive.calls] == ["tail"]


def test_an_orca_runner_on_another_machine_takes_launches_with_no_heartbeat_file_on_this_one(drive, state, tmp_path, capsys):
    """Oct 29: the orca and landing runners must move off this Mac, so a heartbeat file only this Mac reads cannot gate a launch (cci #199401)."""
    _, evidence = briefs(tmp_path)
    assert not list((state / "inbox").glob("*.heartbeat.json"))
    drive.verdicts = {"R2026": "LAUNCHED R2026 {lane}: dispatch ctx_r terminal term_r"}
    assert desk_launch.main(["lane", "remote-lane", "opus", "xhigh", str(evidence)]) == 0
    assert "remote-lane R2026 LAUNCHED R2026 remote-lane: dispatch ctx_r terminal term_r" in capsys.readouterr().out
