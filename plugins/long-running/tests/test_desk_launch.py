"""desk-launch.py against a fake cci drive whose orca runner settles each posted launch."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import threading
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
        self.node = ""
        self.launched = False
        self.allocating = threading.Lock()

    def __call__(self, drive: str, *argv: str) -> str:
        self.calls.append((drive, *argv))
        if argv[0] == "post":
            with self.allocating:
                seq = self.records[-1]["seq"] + 1
                record = {"seq": seq, "num": f"#{seq}{self.node}", "lane": flag(argv, "--lane"), "kind": flag(argv, "--kind"), "to": [flag(argv, "--to")], "text": flag(argv, "--text")}
                self.records.append(record)
                key = f"R{record['num'][1:]}"
                lane = record["text"].split("launch ")[1].split()[0] if " launch " in f" {record['text']}" else ""
                verdict = self.verdicts.get(key) or (f"LAUNCHED {key} {{lane}}: dispatch ctx_{lane} terminal term_{lane}" if self.launched and lane else "")
                if verdict:
                    self.records.append({"seq": seq + 1, "lane": "desk-runner", "kind": "report", "text": verdict.format(lane=lane)})
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
    monkeypatch.setattr(desk_launch.registry, "drives", lambda: [{"drive": "prog", "sessions": ["s1"], "state_dir": str(directory), "checkout": "/prog/checkout"}])
    return directory


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch, state: Path) -> Drive:
    fake = Drive()
    monkeypatch.setattr(desk_launch, "cci", fake)
    monkeypatch.setattr(desk_launch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(desk_launch.launch_checks, "carry_fault", lambda brief, repo: "")
    return fake


def posted(drive: Drive) -> list[str]:
    return [flag(call, "--text") for call in drive.calls if call[1] == "post"]


BRIEF_LINES = "Scale: no N\n\nRegister slice: lane=x step=1\n"


def briefs(tmp_path: Path) -> tuple[Path, Path]:
    fix, evidence = tmp_path / "fix-brief.md", tmp_path / "evidence-brief.md"
    fix.write_text(f"fix\n{BRIEF_LINES}")
    evidence.write_text(f"evidence\n{BRIEF_LINES}")
    return fix, evidence


def test_two_posters_at_once_with_no_shared_lock_never_share_a_key(drive, tmp_path, monkeypatch, capsys):
    """Nov 2 8:22-8:57 PM: meta-desk-1 and root each allocated R85115, R85116, R85117, R85119 and R85120 from the last 20 records, and the runner dropped one launch of each pair."""
    fix, _ = briefs(tmp_path)
    drive.launched = True
    together = threading.Barrier(2, timeout=1)
    exits: dict[str, int] = {}

    def racing(name: str, *argv: str) -> str:
        if argv[0] == "post":
            with contextlib.suppress(threading.BrokenBarrierError):
                together.wait()
        return drive(name, *argv)

    monkeypatch.setattr(desk_launch, "cci", racing)
    launches = [threading.Thread(target=lambda lane=lane: exits.update({lane: desk_launch.main(["lane", lane, "sol", "xhigh", str(fix)])})) for lane in ("lane-a", "lane-b")]
    for launch in launches:
        launch.start()
    for launch in launches:
        launch.join()

    assert exits == {"lane-a": 0, "lane-b": 0}
    keys = {line.split()[1]: line.split()[0] for line in capsys.readouterr().out.splitlines() if " LAUNCHED " in line}
    assert len(keys) == 2 and sorted(keys.values()) == ["lane-a", "lane-b"]


@pytest.mark.parametrize("name", ["inc-app-origin", "inc-app-origin-fix"])
def test_an_incident_pair_posts_addressed_launch_lines_keyed_by_their_records_and_prints_each_dispatch(drive, tmp_path, capsys, name):
    """Root #279033: `incident takeover-500-fix` launched takeover-500-fix-fix, so the fix lane's name matched no order or brief."""
    fix, evidence = briefs(tmp_path)
    drive.verdicts = {
        "R102": "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix",
        "R104": "LAUNCHED R104 {lane}: dispatch ctx_ev terminal term_ev",
    }
    assert desk_launch.main(["incident", name, str(fix), str(evidence)]) == 0
    posts = [call for call in drive.calls if call[1] == "post"]
    assert [flag(call, "--to") for call in posts] == ["orca-desk", "orca-desk"] and all("--stamp" in call for call in posts)
    assert [flag(call, "--text") for call in posts] == [
        f"orca-desk: launch inc-app-origin-fix NOW incident xhigh role=fix brief={fix}",
        f"orca-desk: launch inc-app-origin-evidence NOW incident xhigh role=evidence brief={evidence}",
    ]
    out = capsys.readouterr().out.splitlines()
    assert "inc-app-origin-fix R102 LAUNCHED R102 inc-app-origin-fix: dispatch ctx_fix terminal term_fix" in out
    assert "inc-app-origin-evidence R104 LAUNCHED R104 inc-app-origin-evidence: dispatch ctx_ev terminal term_ev" in out


def test_a_launch_the_runner_refuses_exits_one_with_its_line(drive, tmp_path, capsys):
    fix, _ = briefs(tmp_path)
    drive.verdicts = {"R102": "LAUNCH-FAILED desk-lane-{lane}/R102 {lane}: orca-launch.sh exited 1"}
    assert desk_launch.main(["incident", "inc-x", str(fix)]) == 1
    assert "inc-x-fix R102 LAUNCH-FAILED desk-lane-inc-x-fix/R102 inc-x-fix: orca-launch.sh exited 1" in capsys.readouterr().out


def test_an_unsettled_launch_exits_two_at_the_timeout(drive, tmp_path, capsys):
    _, evidence = briefs(tmp_path)
    assert desk_launch.main(["lane", "slow-restore", "sol", "xhigh", str(evidence), "--timeout-seconds", "0"]) == 2
    [post] = [call for call in drive.calls if call[1] == "post"]
    assert flag(post, "--text").endswith(f"orca-desk: launch slow-restore sol xhigh brief={evidence}")
    assert "slow-restore R102 unsettled after 0s" in capsys.readouterr().out


def test_a_missing_brief_posts_nothing(drive, tmp_path):
    with pytest.raises(SystemExit):
        desk_launch.main(["incident", "inc-x", str(tmp_path / "missing.md")])
    assert [call for call in drive.calls if call[1] == "post"] == []


TEMPLATE = """# <lane> (incident fix lane)

Root: `<lane>` is the whole lane name. Save it as `<state>/<lane>/brief.md`.

Alert: <monitor or Slack link, times PT, what fails where>. Owner words, verbatim: <...>.

0. SCOPE FIRST: post `SCOPE <lane>: active <yes|no>` as `<lane>`.
"""


def test_an_alert_fills_the_incident_template_into_the_lanes_own_brief(drive, state, tmp_path):
    """Root #299799, Nov 1 4:48 PM: root sed the incident template by hand and run-queue-fix launched on a brief still reading <lane> for 20 seconds."""
    template = tmp_path / "incident-template-fix.md"
    template.write_text(TEMPLATE)
    drive.verdicts = {"R102": "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"}
    alert = "monitor 339599887 Run queue backlog not draining, 4:46 PM PT. Owner words, verbatim: fix asap"
    assert desk_launch.main(["incident", "run-queue", str(template), "--alert", alert]) == 0
    brief = state / "run-queue-fix" / "brief.md"
    assert brief.read_text() == f"# run-queue-fix (incident fix lane)\n\nAlert: {alert}\n\n0. SCOPE FIRST: post `SCOPE run-queue-fix: active <yes|no>` as `run-queue-fix`.\n"
    [line] = posted(drive)
    assert line.endswith(f"orca-desk: launch run-queue-fix NOW incident xhigh role=fix brief={brief}")


def test_an_alert_holding_its_own_angle_brackets_still_fills_and_posts(drive, state, tmp_path):
    """Root #305172: an --alert quoting `<RUN>` and `<X>` read as an unfilled template field, so the incident launched nothing."""
    template = tmp_path / "incident-template-fix.md"
    template.write_text(TEMPLATE)
    drive.verdicts = {"R102": "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"}
    alert = "monitor 339599887 fires on <RUN> for team <X>, 8:40 PM PT"
    assert desk_launch.main(["incident", "run-queue", str(template), "--alert", alert]) == 0
    assert f"Alert: {alert}\n" in (state / "run-queue-fix" / "brief.md").read_text()


@pytest.mark.parametrize("text", [
    "# <lane>\n\nAlert: monitor 1, 4:46 PM\n",
    "# run-queue-fix\n\nAlert: <monitor or Slack link>\n",
    "# run-queue-fix\n\nAlert: monitor 1. Owner words, verbatim: <...>.\n",
    "# run-queue-fix\n\nAlert: monitor 1\nWork in <state>/run-queue-fix.\n",
])
def test_an_incident_brief_still_holding_a_root_placeholder_posts_nothing(drive, tmp_path, text):
    brief = tmp_path / "run-queue-fix" / "brief.md"
    brief.parent.mkdir()
    brief.write_text(text)
    with pytest.raises(SystemExit):
        desk_launch.main(["incident", "run-queue", str(brief)])
    assert posted(drive) == []


def test_a_lanes_own_placeholders_pass_once_root_filled_its_own(drive, tmp_path):
    brief = tmp_path / "run-queue-fix" / "brief.md"
    brief.parent.mkdir()
    brief.write_text("# run-queue-fix\n\nAlert: monitor 1, 4:46 PM\n\nPost `CANDIDATE run-queue-fix: <files> <sha>`.\n")
    drive.verdicts = {"R102": "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"}
    assert desk_launch.main(["incident", "run-queue", str(brief)]) == 0


def test_a_brief_missing_both_lines_is_refused_before_any_post_naming_both(drive, tmp_path):
    """Nov 2: R84952 to R84958 and R84964 each lost a five-minute runner round trip to one missing brief line at a time."""
    brief = tmp_path / "plain-brief.md"
    brief.write_text("Do the thing.\n")
    with pytest.raises(SystemExit) as refused:
        desk_launch.main(["lane", "new-lane", "opus", "xhigh", str(brief)])
    assert "`Register slice: lane=<name> step=<n>`" in str(refused.value) and "`Scale:`" in str(refused.value)
    assert drive.calls == []


def test_a_brief_naming_what_no_sprite_can_receive_is_refused_before_any_post_naming_it(drive, tmp_path, monkeypatch):
    """Coordinator 10:43 PM Nov 2 (cci 352724): briefs naming Mac paths landed on Sprites that could not read them."""
    fix, _ = briefs(tmp_path)
    carried: list[tuple[Path, str]] = []
    monkeypatch.setattr(desk_launch.launch_checks, "carry_fault", lambda brief, repo: carried.append((brief, repo)) or f"brief {brief} names what no Sprite can receive: /Users/me/gone.md does not exist on this Mac")
    with pytest.raises(SystemExit) as refused:
        desk_launch.main(["lane", "new-lane", "opus", "xhigh", str(fix)])
    assert "/Users/me/gone.md does not exist on this Mac" in str(refused.value)
    assert carried == [(fix.resolve(), "/prog/checkout")]
    assert drive.calls == []


def test_a_launch_pinned_to_the_mac_carries_nothing(drive, tmp_path, monkeypatch):
    fix, _ = briefs(tmp_path)
    local = tmp_path / "local-brief.md"
    local.write_text(f"ccx: role=build place=local\n{BRIEF_LINES}")
    monkeypatch.setattr(desk_launch.launch_checks, "carry_fault", lambda brief, repo: pytest.fail(f"carried {brief} for a launch that stays on the Mac"))
    drive.launched = True
    assert desk_launch.main(["lane", "mac-lane", "opus", "xhigh", str(local)]) == 0
    assert desk_launch.main(["lane", "fable-lane", "fable", "xhigh", str(fix)]) == 0


def test_every_fault_of_a_launch_is_named_at_once_before_any_post(drive, tmp_path):
    brief = tmp_path / "other-lane" / "brief.md"
    brief.parent.mkdir()
    brief.write_text("Do the thing.\n")
    with pytest.raises(SystemExit) as refused:
        desk_launch.main(["lane", "new-lane", "gemini", "hard", str(brief)])
    for fault in ("gemini is not a model", "hard is not an effort", "is lane other-lane's", "`Register slice:", "`Scale:`"):
        assert fault in str(refused.value)
    assert drive.calls == []


def test_an_incident_brief_owes_neither_line(drive, tmp_path):
    brief = tmp_path / "plain-brief.md"
    brief.write_text("Fix the outage.\n")
    drive.verdicts = {"R102": "LAUNCHED R102 {lane}: dispatch ctx_fix terminal term_fix"}
    assert desk_launch.main(["incident", "inc-x", str(brief)]) == 0


def test_a_brief_carrying_both_lines_posts(drive, tmp_path):
    fix, _ = briefs(tmp_path)
    drive.verdicts = {"R102": "LAUNCHED R102 {lane}: dispatch ctx_l terminal term_l"}
    assert desk_launch.main(["lane", "new-lane", "opus", "xhigh", str(fix)]) == 0
    assert len(posted(drive)) == 1


def test_a_launch_posted_on_another_node_waits_on_the_key_every_node_names_its_record_by(drive, tmp_path, capsys):
    _, evidence = briefs(tmp_path)
    drive.node = "@8d2775d4"
    drive.verdicts = {"R102@8d2775d4": "LAUNCHED desk-lane-{lane}/R102@8d2775d4 {lane}: dispatch ctx_r terminal term_r"}
    assert desk_launch.main(["lane", "next-lane", "opus", "xhigh", str(evidence)]) == 0
    assert "next-lane R102@8d2775d4 LAUNCHED desk-lane-next-lane/R102@8d2775d4 next-lane: dispatch ctx_r terminal term_r" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "line"),
    [
        (["relay", "lane-a,lane-b", "restack onto dev"], "orca-desk: relay to lane-a, lane-b: restack onto dev"),
        (["hold", "sandsql-5177", "root", "sand releases until #12 lands"], "orca-desk: hold sandsql-5177 owner=root :: sand releases until #12 lands"),
        (["unhold", "sandsql-5177"], "orca-desk: unhold sandsql-5177"),
        (["withdraw", "R2025"], "orca-desk: withdraw R2025"),
    ],
)
def test_each_line_posts_with_no_key_of_its_own_under_ccis_stamp_and_prints_its_record_without_waiting(drive, capsys, argv, line):
    assert desk_launch.main(argv) == 0
    assert posted(drive) == [line]
    assert [call[1] for call in drive.calls] == ["tail", "post"] and "--stamp" in drive.calls[-1]
    assert capsys.readouterr().out == f"posted cci #102: {line}\n"


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
    drive.verdicts = {"R102": "LAUNCHED R102 {lane}: dispatch ctx_r terminal term_r"}
    assert desk_launch.main(["lane", "remote-lane", "opus", "xhigh", str(evidence)]) == 0
    assert "remote-lane R102 LAUNCHED R102 remote-lane: dispatch ctx_r terminal term_r" in capsys.readouterr().out
