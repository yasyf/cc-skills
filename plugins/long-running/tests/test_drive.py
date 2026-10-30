from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import drive
import pytest
from conftest import DRIVE, LEDGER, FakeShell

ROOT_SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
RESUMED_SESSION = "a5c86fb7-27c5-4619-b702-8ffeaf7ab7e0"
WORKER_SESSION = "0dd0bead-0000-4000-8000-000000000000"
HEAD = "1caa098d30a8"
NEW_HEAD = "af664caed71b"


def git_repo(path: Path, origin: str) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(drive.DRIVE_ENV, raising=False)
    monkeypatch.setenv(drive.SESSION_ENV, ROOT_SESSION)
    checkout = git_repo(tmp_path / "monorepo", "git@github.com:Forge-AI/monorepo.git")
    monkeypatch.chdir(checkout)
    return checkout


def started(repo: Path, *argv: str) -> dict:
    assert drive.main(["start", "--ledger", LEDGER, *argv]) == 0
    return json.loads((drive.drives_dir() / "900424b6.json").read_text())


def record(shell: FakeShell, cwd: Path, *argv: str) -> int:
    return drive.main(["record", "--cwd", str(cwd), *argv], shell)


def test_explicit_drive_reads_only_its_canonical_registry_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = drive.drives_dir()
    root.mkdir(parents=True)
    selected = {"drive": "z-selected", "sessions": [ROOT_SESSION]}
    (root / "z-selected.json").write_text(json.dumps(selected))
    for index in range(100):
        (root / f"unrelated-{index}.json").write_text("not JSON")
    assert drive.find("z-selected", None) == selected
    assert drive.find("missing", ROOT_SESSION) is None


@pytest.mark.parametrize("name", ["../selected", "nested/selected", "nul\x00id"])
def test_explicit_drive_does_not_expand_registry_authority(tmp_path, monkeypatch, name):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert drive.find(name, ROOT_SESSION) is None


def test_start_registers_the_drive_by_its_root_session(repo, capsys):
    entry = started(repo, "--orca-run", "run_7715a23a5657")

    assert entry | {"started_at": "", "updated_at": ""} == {
        "drive": "900424b6",
        "ledger": LEDGER,
        "repo": "Forge-AI/monorepo",
        "git_common_dir": str((repo / ".git").resolve()),
        "checkout": str(Path.cwd()),
        "sessions": [ROOT_SESSION],
        "orca_run": "run_7715a23a5657",
        "state_dir": str(Path.home() / ".claude" / "scratch" / "900424b6"),
        "program": "900424b6",
        "cci_drive": None,
        "register_lane": None,
        "root_terminal": None,
        "started_at": "",
        "updated_at": "",
    }
    assert capsys.readouterr().out.startswith(f"drive 900424b6 on ledger {LEDGER} at ")


def test_start_inside_an_orca_terminal_records_it_as_the_coordinators_and_a_start_outside_keeps_it(repo, monkeypatch):
    monkeypatch.setenv(drive.TERMINAL_ENV, "term_root")
    assert started(repo)["root_terminal"] == "term_root"
    monkeypatch.delenv(drive.TERMINAL_ENV)
    monkeypatch.setenv(drive.SESSION_ENV, RESUMED_SESSION)

    assert started(repo, "--drive", "900424b6")["root_terminal"] == "term_root"


def test_desk_records_standing_desks_and_keeps_one_supervisor(repo, capsys):
    started(repo)
    assert drive.main(["desk", "--name", "landing", "--terminal", "term_landing", "--supervisor"]) == 0
    assert drive.main(["desk", "--name", "codex-supervisor", "--terminal", "term_codex", "--environment", "pool-a", "--supervisor"]) == 0

    entry = json.loads((drive.drives_dir() / "900424b6.json").read_text())

    assert entry["desks"] == {
        "landing": {"terminal": "term_landing", "environment": None, "supervisor": False},
        "codex-supervisor": {"terminal": "term_codex", "environment": "pool-a", "supervisor": True},
    }
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "drive 900424b6 desks: codex-supervisor=term_codex (supervisor), landing=term_landing",
        "codex-supervisor gets one typed line at its idle prompt; while it holds a wait it reads busy and hears only through: cci watch --drive 900424b6 --to codex-supervisor --for 0",
    ]


def test_desk_remove_forgets_a_desk_and_refuses_one_it_never_held(repo, capsys):
    started(repo)
    drive.main(["desk", "--name", "landing", "--terminal", "term_landing"])

    assert drive.main(["desk", "--name", "landing", "--remove"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "drive 900424b6 desks: none"
    with pytest.raises(SystemExit, match="drive 900424b6 has no desk landing"):
        drive.main(["desk", "--name", "landing", "--remove"])


def test_a_handoff_joins_the_drive_and_keeps_its_orca_run(repo, monkeypatch):
    started(repo, "--orca-run", "run_7715a23a5657")
    monkeypatch.setenv(drive.SESSION_ENV, RESUMED_SESSION)

    entry = started(repo, "--drive", "900424b6")

    assert entry["sessions"] == [ROOT_SESSION, RESUMED_SESSION]
    assert entry["orca_run"] == "run_7715a23a5657"
    assert drive.main(["current"]) == 0


def test_a_handoff_keeps_the_state_dir_the_drive_started_with(repo, monkeypatch, tmp_path):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))
    monkeypatch.setenv(drive.SESSION_ENV, RESUMED_SESSION)

    assert started(repo, "--drive", "900424b6")["state_dir"] == str(tmp_path / "release-v3")


def test_start_writes_the_dashboard_context_from_the_registry(repo, tmp_path):
    started(repo, "--state-dir", str(tmp_path / "release-v3"), "--cci-drive", "release-v3-cci", "--orca-run", "run_1")

    context = json.loads((tmp_path / "release-v3" / "dashboard" / "context.json").read_text())

    assert context | {"started_at": ""} == {
        "id": "900424b6",
        "title": "release-v3",
        "program": "release-v3",
        "repo": "Forge-AI/monorepo",
        "checkout": str(Path.cwd()),
        "ledger": LEDGER,
        "sessions": [ROOT_SESSION],
        "cci_drive": "release-v3-cci",
        "orca_run": "run_1",
        "state_dir": str(tmp_path / "release-v3"),
        "started_at": "",
        "packs": {"lr": str(drive.PACK)},
    }
    assert (drive.PACK / "components.py").exists()


def test_start_records_the_register_lane_and_a_handoff_keeps_it(repo, monkeypatch):
    assert started(repo, "--register-lane", "register-desk")["register_lane"] == "register-desk"
    monkeypatch.setenv(drive.SESSION_ENV, RESUMED_SESSION)

    assert started(repo, "--drive", "900424b6")["register_lane"] == "register-desk"


def test_start_records_the_program_workers_resolve_and_a_handoff_keeps_it(repo, tmp_path, monkeypatch):
    entry = started(repo, "--state-dir", str(tmp_path / "release-v3"), "--program", "plan-slug")
    monkeypatch.setenv(drive.SESSION_ENV, RESUMED_SESSION)

    assert entry["program"] == "plan-slug"
    assert started(repo, "--drive", "900424b6")["program"] == "plan-slug"


def test_context_rewrites_the_dashboard_context_with_the_state_dir_as_the_cci_drive(repo, tmp_path):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))
    path = tmp_path / "release-v3" / "dashboard" / "context.json"
    path.unlink()

    assert drive.main(["context", "--drive", "900424b6"]) == 0
    assert json.loads(path.read_text())["cci_drive"] == "release-v3"


def test_context_keeps_the_task_facts_a_layout_added_and_rewrites_the_registry_ones(repo, tmp_path):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))
    path = tmp_path / "release-v3" / "dashboard" / "context.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"owner_login": "yasyf", "cci_drive": "stale"}))

    assert drive.main(["context", "--drive", "900424b6"]) == 0
    context = json.loads(path.read_text())
    assert (context["owner_login"], context["cci_drive"]) == ("yasyf", "release-v3")


def test_a_session_runs_one_drive_at_a_time(repo):
    started(repo)

    with pytest.raises(SystemExit, match="already runs drive 900424b6"):
        drive.main(["start", "--ledger", LEDGER, "--drive", "second"])


def test_current_and_end_resolve_the_session_drive(repo, monkeypatch, capsys):
    started(repo)
    capsys.readouterr()

    assert drive.main(["current"]) == 0
    assert capsys.readouterr().out == "900424b6\n"
    assert drive.main(["end"]) == 0
    assert drive.main(["current"]) == 1
    assert not list(drive.drives_dir().glob("*.json"))


def test_registrations_live_under_the_claude_config_dir(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    started(repo)
    assert (tmp_path / "config" / "long-running" / "drives" / "900424b6.json").exists()


def test_a_sessionless_process_resolves_the_drive_of_its_orca_run(repo, monkeypatch, capsys):
    started(repo, "--orca-run", "run_7715a23a5657")
    capsys.readouterr()
    monkeypatch.delenv(drive.SESSION_ENV)

    assert drive.main(["current"]) == 1

    monkeypatch.setenv(drive.ORCA_RUN_ENV, "run_other")
    assert drive.main(["current"]) == 1

    monkeypatch.setenv(drive.ORCA_RUN_ENV, "run_7715a23a5657")
    assert drive.main(["current"]) == 0
    assert capsys.readouterr().out == "900424b6\n"


def test_orca_run_prints_the_run_of_the_session_drive(repo, monkeypatch, capsys):
    started(repo)
    capsys.readouterr()
    assert drive.main(["orca-run"]) == 1

    started(repo, "--orca-run", "run_7715a23a5657")
    capsys.readouterr()
    assert drive.main(["orca-run"]) == 0
    assert capsys.readouterr().out == "run_7715a23a5657\n"

    monkeypatch.delenv(drive.SESSION_ENV)
    assert drive.main(["orca-run"]) == 1

    monkeypatch.setenv(drive.DRIVE_ENV, "900424b6")
    assert drive.main(["orca-run"]) == 0
    assert capsys.readouterr().out == "run_7715a23a5657\n"


def test_a_lane_in_the_root_session_records_its_pr_with_its_head(repo, capsys):
    started(repo)
    shell = FakeShell()

    assert record(shell, repo, "--session", ROOT_SESSION, "--lane", "deploy-experience", "--pr", f"Forge-AI/monorepo#28534={HEAD}") == 0

    assert shell.fields("28534") == {"lane": "deploy-experience", "registered": "deploy-experience", "registered_head": HEAD}
    assert capsys.readouterr().out.splitlines()[-1] == "registered deploy-experience #28534"


def test_recording_the_same_head_twice_writes_once_and_a_repush_moves_only_the_head(repo):
    started(repo)
    shell = FakeShell()
    argv = ["--session", ROOT_SESSION, "--lane", "deploy-experience"]

    record(shell, repo, *argv, "--pr", f"28534={HEAD}")
    record(shell, repo, *argv, "--pr", f"28534={HEAD}")
    record(shell, repo, "--session", ROOT_SESSION, "--lane", "ship-pr", "--pr", f"28534={NEW_HEAD}")

    writes = [call for call in shell.calls if call[1:4] == ["ledger", "row", "set"]]
    assert len(writes) == 2
    assert shell.fields("28534") == {"lane": "deploy-experience", "registered": "deploy-experience", "registered_head": NEW_HEAD}


def test_an_orca_worker_records_through_its_drive_env(repo):
    started(repo)
    worktree = repo.parent / "worker"
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True)
    shell = FakeShell()

    assert record(shell, worktree, "--session", WORKER_SESSION, "--drive", "900424b6", "--lane", "orca-desk", "--pr", "28557") == 0

    assert shell.fields("28557") == {"lane": "orca-desk", "registered": "orca-desk"}


def test_a_codex_worker_records_its_pr_and_posts_it_opened_under_its_lane(repo):
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()

    assert record(shell, repo, "--session", WORKER_SESSION, "--drive", "900424b6", "--lane", "inc-unrun-fix", "--opened", "--pr", f"40359={HEAD}") == 0

    assert shell.fields("40359") == {"lane": "inc-unrun-fix", "registered": "inc-unrun-fix", "registered_head": HEAD}
    assert [(post["lane"], post["kind"], post["text"]) for post in shell.cci] == [("inc-unrun-fix", "opened", "opened #40359")]


@pytest.mark.parametrize(
    ("session", "where", "pr"),
    [
        (WORKER_SESSION, "repo", "28534"),
        (ROOT_SESSION, "elsewhere", "28534"),
        (ROOT_SESSION, "repo", "yasyf/cc-skills#123"),
    ],
    ids=["session-in-no-drive", "command-in-another-repo", "pr-on-another-repo"],
)
def test_nothing_outside_the_drive_reaches_its_ledger(repo, tmp_path, session, where, pr):
    started(repo)
    cwd = repo if where == "repo" else git_repo(tmp_path / "cc-skills", "https://github.com/yasyf/cc-skills")
    shell = FakeShell()

    assert record(shell, cwd, "--session", session, "--lane", "lane", "--pr", pr) == 0

    assert shell.calls == []


def test_a_session_in_no_drive_says_so_on_stderr(repo, capsys):
    started(repo)
    capsys.readouterr()

    assert record(FakeShell(), repo, "--session", WORKER_SESSION, "--lane", "merge-walker-r2", "--pr", "29693") == 0

    assert capsys.readouterr().err.strip() == (
        f"no drive claims session {WORKER_SESSION} (lane merge-walker-r2, {drive.DRIVE_ENV}=unset); PR #29693 not recorded"
    )


def test_a_failed_write_names_the_ledger_and_the_command_to_run(repo, capsys):
    started(repo)
    shell = FakeShell()
    shell.fail_ccn_writes = True

    assert record(shell, repo, "--session", ROOT_SESSION, "--lane", "lane", "--pr", f"28534={HEAD}") == 1

    assert capsys.readouterr().err.strip() == (
        f"PR #28534 was not recorded in ledger {LEDGER}: ledger busy — run "
        f"`ledger.py register --ledger {LEDGER} --lane lane --pr 28534 --head {HEAD}` from {repo}"
    )


def closed_pull(shell: FakeShell, pr: str) -> None:
    shell.pulls[pr] = {"number": int(pr), "state": "closed", "head": {"sha": HEAD, "ref": "lane/x"}, "base": {"ref": "dev"}, "created_at": "2026-10-06T00:00:00Z"}
    shell.pull_heads[pr] = HEAD
    shell.pr_files[pr] = ["go/ci/release.go"]
    shell.delivered[HEAD] = ("f2f8393f0000", "2026-10-07T03:53:00+00:00")


def test_a_landing_settles_the_rows_the_ledger_tracks_and_adds_none(repo, capsys):
    started(repo)
    shell = FakeShell(rows=[{"key": "31390", "fields": {"lane": "app-build-speed", "registered": "app-build-speed"}}])
    closed_pull(shell, "31390")
    closed_pull(shell, "296")

    assert drive.main(["landed", "--session", ROOT_SESSION, "--pr", "31390", "--pr", "296"], shell) == 0

    assert shell.fields("31390")["state"] == "landed"
    assert shell.fields("31390")["landed_sha"] == "f2f8393f0000"
    assert [row["key"] for row in shell.store["rows"]] == ["31390"]
    assert "landed #31390" in capsys.readouterr().out


def test_a_landing_outside_any_drive_settles_nothing(repo):
    started(repo)
    shell = FakeShell()

    assert drive.main(["landed", "--session", WORKER_SESSION, "--pr", "31390"], shell) == 0

    assert shell.calls == []


def test_list_json_reads_back_every_drive(repo, capsys):
    started(repo)
    capsys.readouterr()

    assert drive.main(["list", "--json"]) == 0

    assert [entry["drive"] for entry in json.loads(capsys.readouterr().out)] == ["900424b6"]


def test_record_refuses_a_malformed_pr(repo):
    with pytest.raises(SystemExit):
        drive.main(["record", "--session", ROOT_SESSION, "--lane", "x", "--cwd", str(repo), "--pr", "#12"])


@pytest.mark.parametrize(
    "argv",
    [
        ["thread", "--lane", "owner", "--channel", "C0AAAAAAAA1", "--thread-ts", "1790901035.467689", "--posted-ts", "1790901997.422529"],
        ["watch", "--lane", "owner", "--channel", "C0AAAAAAAA1", "--message", "1790901997.422529"],
    ],
)
def test_no_drive_verb_enrols_a_slack_thread_or_link_for_watching(repo, tmp_path, argv):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))

    with pytest.raises(SystemExit):
        drive.main([*argv, "--session", ROOT_SESSION])

    assert not (tmp_path / "release-v3" / "slack").exists()


def test_release_records_the_builds_owner_on_the_drive_its_cci_drive_names(repo, capsys):
    started(repo, "--cci-drive", "release-v3")
    build = "https://buildkite.com/forge/release/builds/8748"

    assert drive.main(["release", "--cci-drive", "release-v3", "--owner", "release-walker-4@release-v3", "--build", build, "--release", "releases/2026-10-22/3"]) == 0
    assert drive.main(["release", "--cci-drive", "other-drive", "--owner", "walker@other-drive", "--build", "1"]) == 1

    releases = json.loads((drive.drives_dir() / "900424b6.json").read_text())["releases"]
    assert releases == {build: {"owner": "release-walker-4@release-v3", "release": "releases/2026-10-22/3", "at": releases[build]["at"]}}
    assert "no registered drive posts to cci drive other-drive; build 1 not recorded" in capsys.readouterr().err


def heartbeat(shell: FakeShell, desk: str, pid: int, version: str, ago: timedelta = timedelta()) -> str:
    restart = f"nohup desk-runner.py run --config /drive/runner.json --desk {desk} > /drive/{desk}-runner.log 2>&1 < /dev/null &"
    at = (datetime.now(timezone.utc) - ago).isoformat()
    attrs = {"pid": str(pid), "version": version, "config": "/drive/runner.json", "terminal": "term_root", "restart": restart}
    shell.cci.append({"seq": len(shell.cci) + 1, "drive": DRIVE, "lane": "desk-runner", "kind": "state", "topic": desk, "to": [f"drive-watchdog@{DRIVE}"], "at": at, "text": f"{desk} alive 9:26 PM pass 3", "fields": {"attrs": attrs}})
    return at


def test_digest_names_each_desk_runner_behind_the_installed_plugin_with_the_handoff_that_rotates_it(repo, capsys):
    """#535 shipped as 0.7.186 at 5:07 PM on Oct 20 and the live runner stayed on 0.7.185 until 9:26 PM (#107539)."""
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    plugins = drive.drives_dir().parents[1] / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"long-running@skills": [{"version": "0.7.185"}, {"version": "0.7.186"}], "long-running-extras@skills": [{"version": "9.0.0"}]}})
    )
    orca = heartbeat(shell, "orca", 73091, "0.7.186")
    landing = heartbeat(shell, "landing", 49612, "0.7.185")
    capsys.readouterr()

    assert drive.main(["digest"], shell) == 1

    assert capsys.readouterr().out.splitlines() == [
        f"orca: pid 73091 runs 0.7.186, installed 0.7.186, last alive {orca}: current",
        f"landing: pid 49612 runs 0.7.185, installed 0.7.186, last alive {landing}: stale; rotate it with `desk-runner.py handoff --config /drive/runner.json --desk landing`, which starts the next one",
    ]
    heartbeat(shell, "landing", 50113, "0.7.186")
    assert drive.main(["digest"], shell) == 0


def test_digest_names_a_desk_with_no_alive_record_and_exits_one(repo, capsys):
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    heartbeat(shell, "orca", 73091, "0.7.186")
    capsys.readouterr()

    assert drive.main(["digest"], shell) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("orca: pid 73091 runs 0.7.186, installed unknown") and lines[0].endswith(": current")
    assert lines[1] == f"landing: no landing alive record on cci drive {DRIVE}, so no landing runner has run there"


def test_digest_reads_a_desk_runner_on_another_machine_from_its_alive_record_and_names_a_silent_one_down(repo, capsys):
    """Oct 29: the runners must move off this Mac, so the digest reads cci records, never a heartbeat file or a local pid (cci #199401)."""
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    heartbeat(shell, "orca", 73091, "0.7.186")
    landing = heartbeat(shell, "landing", 49612, "0.7.186", ago=timedelta(minutes=6))
    capsys.readouterr()

    assert drive.main(["digest"], shell) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith(": current")
    assert lines[1] == f"landing: pid 49612 runs 0.7.186, installed unknown, last alive {landing}: down; restart it with `nohup desk-runner.py run --config /drive/runner.json --desk landing > /drive/landing-runner.log 2>&1 < /dev/null &`"


def installed(*versions: str, manifest: str | None = None) -> Path:
    plugins = drive.drives_dir().parents[1] / "plugins"
    for version in versions:
        (plugins / "cache" / version / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugins / "cache" / version / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "long-running", "version": manifest or version}))
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"long-running@skills": [{"version": version, "installPath": str(plugins / "cache" / version)} for version in versions]}}))
    return plugins / "cache"


@pytest.mark.parametrize(("caller", "reexecs"), [(None, True), ("0.7.99", False), ("0.7.320", True)])
def test_a_script_behind_the_installed_plugin_reexecutes_the_installed_copy_unless_a_process_of_its_own_version_started_it(repo, monkeypatch, caller, reexecs):
    """On Oct 26 root ran holds.py from 0.7.99 with 0.7.320 installed, and the runner could not read the holds line it left (#131579); a brief naming a versioned desk-wait.sh path then refused instead of running (#136583)."""
    monkeypatch.setattr(drive, "plugin_manifest", lambda: {"name": "long-running", "version": "0.7.99"})
    cache = installed("0.7.99", "0.7.320")
    if caller:
        monkeypatch.setenv(drive.CALLER_VERSION_ENV, caller)
    else:
        monkeypatch.delenv(drive.CALLER_VERSION_ENV, raising=False)
    execs: list[list[str]] = []
    monkeypatch.setattr(drive.os, "execv", lambda path, argv: execs.append(argv))
    monkeypatch.setattr(drive.sys, "argv", ["holds.py", "add", "#1"])

    drive.run_installed("/plugins/cache/skills/long-running/0.7.99/skills/long-running/scripts/holds.py")

    assert execs == ([[drive.sys.executable, str(cache / "0.7.320" / "skills" / "long-running" / "scripts" / "holds.py"), "add", "#1"]] if reexecs else [])


def test_an_installed_copy_whose_own_manifest_is_no_newer_runs_in_place_instead_of_re_executing_forever(repo, monkeypatch):
    monkeypatch.setattr(drive, "plugin_manifest", lambda: {"name": "long-running", "version": "0.7.99"})
    installed("0.7.320", manifest="0.7.99")
    monkeypatch.delenv(drive.CALLER_VERSION_ENV, raising=False)
    execs: list[list[str]] = []
    monkeypatch.setattr(drive.os, "execv", lambda path, argv: execs.append(argv))

    drive.run_installed("holds.py")

    assert execs == []


def test_a_current_script_marks_its_children_with_its_version(repo, monkeypatch):
    monkeypatch.setattr(drive, "plugin_manifest", lambda: {"name": "long-running", "version": "0.7.320"})
    installed("0.7.320")
    monkeypatch.delenv(drive.CALLER_VERSION_ENV, raising=False)
    drive.run_installed("holds.py")
    assert drive.os.environ[drive.CALLER_VERSION_ENV] == "0.7.320"


def test_digest_prints_the_stack_ledger_the_stall_watch_wrote_and_marks_each_stale_pr(repo, capsys):
    entry = started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    heartbeat(shell, "orca", 73091, "0.7.186")
    heartbeat(shell, "landing", 49612, "0.7.186")
    (Path(entry["state_dir"]) / "inbox").mkdir(parents=True)
    prs = [
        {"pr": "201", "title": "", "head": "abc1234def", "base": "dev", "below": "", "state": "conflicts with its base", "next": "restack #201 at abc1234 and post HEAD", "owner": "gone-lane", "since": "2026-10-27T17:00:00Z", "live": False, "stale": True},
        {"pr": "202", "title": "", "head": "fedcba9876", "base": "b201", "below": "201", "state": "in the merge queue", "next": "the landing desk reads why the merge queue still holds #202 at fedcba9", "owner": "api-lane", "since": "2026-10-27T19:30:00Z", "live": True, "stale": False},
    ]
    (Path(entry["state_dir"]) / "inbox" / drive.STACK).write_text(json.dumps({"at": "2026-10-27T20:00:00Z", "prs": prs}))
    capsys.readouterr()

    drive.main(["digest"], shell)

    assert capsys.readouterr().out.splitlines()[2:] == [
        "stack at 2026-10-27T20:00:00Z: 2 open PRs, 1 stale",
        "STALE #201 abc1234 on dev: conflicts with its base | owner gone-lane, not live | since 2026-10-27T17:00:00Z | next: restack #201 at abc1234 and post HEAD",
        "#202 fedcba9 on #201: in the merge queue | owner api-lane | since 2026-10-27T19:30:00Z | next: the landing desk reads why the merge queue still holds #202 at fedcba9",
    ]


def test_marketplace_copy_maps_a_cache_install_path_into_the_marketplace_checkout_it_came_from(tmp_path, monkeypatch):
    plugins = tmp_path / ".claude/plugins"
    plugin = plugins / "cache/skills/long-running/0.7.503"
    marketplace = plugins / "marketplaces/skills"
    (marketplace / ".claude-plugin").mkdir(parents=True)
    (marketplace / ".claude-plugin/marketplace.json").write_text(json.dumps({"plugins": [{"name": "cc-inbox", "source": "./plugins/cc-inbox"}, {"name": "long-running", "source": "./plugins/long-running"}]}))
    monkeypatch.setattr(drive, "PLUGIN", plugin)
    assert drive.marketplace_copy(plugin / "skills/long-running") == marketplace / "plugins/long-running/skills/long-running"
    checkout = tmp_path / "cc-skills/plugins/long-running"
    monkeypatch.setattr(drive, "PLUGIN", checkout)
    assert drive.marketplace_copy(checkout / "skills/long-running") is None
