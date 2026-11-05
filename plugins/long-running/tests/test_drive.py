from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import drive
from long_running import release
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


def test_a_sessionless_codex_lane_resolves_the_drive_it_names(repo, monkeypatch, capsys):
    started(repo)
    capsys.readouterr()
    monkeypatch.delenv(drive.SESSION_ENV)

    assert drive.main(["digest", "--drive", "elsewhere"]) == 1
    assert capsys.readouterr().err == "no drive elsewhere is registered\n"


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
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()

    assert record(shell, repo, "--session", ROOT_SESSION, "--lane", "deploy-experience", "--pr", f"Forge-AI/monorepo#28534={HEAD}") == 0

    assert shell.fields("28534") == {"lane": "deploy-experience", "registered": "deploy-experience", "registered_head": HEAD}
    head = next(call for call in shell.calls if call[:2] == ["cci", "post"] and "head" in call)
    assert head[head.index("--head") + 1] == f"Forge-AI/monorepo#28534={HEAD}"
    assert capsys.readouterr().out.splitlines()[-1] == "registered deploy-experience #28534"


def test_a_record_of_a_stack_syncs_the_ledger_once(repo, monkeypatch):
    monkeypatch.setenv(drive.ledger.LEDGER_SYNC, "1")
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()

    assert record(shell, repo, "--session", ROOT_SESSION, "--lane", "deploy-experience", "--pr", f"28534={HEAD}", "--pr", f"28535={NEW_HEAD}") == 0

    assert [shell.fields(pr)["registered_head"] for pr in ("28534", "28535")] == [HEAD, NEW_HEAD]
    assert shell.ccn_syncs == [f"refs/cc-notes/ledgers/{LEDGER}"]


def test_a_rerun_record_that_writes_nothing_still_syncs_the_rows_its_killed_first_run_wrote(repo, monkeypatch):
    monkeypatch.setenv(drive.ledger.LEDGER_SYNC, "1")
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    argv = ["--session", ROOT_SESSION, "--lane", "deploy-experience", "--pr", f"28534={HEAD}"]
    record(shell, repo, *argv)
    shell.ccn_syncs.clear()

    assert record(shell, repo, *argv) == 0

    assert shell.ccn_syncs == [f"refs/cc-notes/ledgers/{LEDGER}"]


def test_recording_the_same_head_twice_writes_once_and_a_repush_moves_only_the_head(repo):
    started(repo, "--cci-drive", DRIVE)
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
    assert [(post["lane"], post["kind"], post["text"]) for post in shell.cci] == [("inc-unrun-fix", "opened", "opened #40359"), ("inc-unrun-fix", "head", f"#40359 at {HEAD[:9]}")]


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


def merged_pull(shell: FakeShell, pr: str) -> None:
    shell.pulls[pr] = {"number": int(pr), "state": "closed", "merged_at": "2026-10-07T03:53:00Z", "head": {"sha": HEAD, "ref": "lane/x"}, "base": {"ref": "dev"}, "created_at": "2026-10-06T00:00:00Z"}
    shell.pull_heads[pr] = HEAD
    shell.pr_files[pr] = ["go/ci/release.go"]
    shell.delivered[HEAD] = ("f2f8393f0000", "2026-10-07T03:53:00+00:00")


def test_a_landing_settles_the_rows_the_ledger_tracks_and_adds_none(repo, capsys):
    started(repo)
    shell = FakeShell(rows=[{"key": "31390", "fields": {"lane": "app-build-speed", "registered": "app-build-speed"}}])
    merged_pull(shell, "31390")
    merged_pull(shell, "296")

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


def heartbeat(shell: FakeShell, desk: str, pid: int, ago: timedelta = timedelta()) -> str:
    at = (datetime.now(timezone.utc) - ago).isoformat()
    attrs = {"pid": str(pid), "terminal": "term_root"}
    shell.cci.append({"seq": len(shell.cci) + 1, "drive": DRIVE, "lane": "desk-runner", "kind": "state", "topic": desk, "to": [f"drive-watchdog@{DRIVE}"], "at": at, "text": f"{desk} alive 9:26 PM pass 3", "fields": {"attrs": attrs}})
    return at


def test_digest_reads_the_relay_from_its_alive_record_and_names_a_silent_one_down(repo, capsys):
    """Oct 29: the runners must move off this Mac, so the digest reads cci records, never a heartbeat file or a local pid (cci #199401)."""
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    orca = heartbeat(shell, "orca", 73091)
    capsys.readouterr()

    assert drive.main(["digest"], shell) == 0
    assert capsys.readouterr().out.splitlines()[0] == f"orca: pid 73091 last alive {orca}: current"

    silent = heartbeat(shell, "orca", 73091, ago=timedelta(minutes=6))
    capsys.readouterr()
    assert drive.main(["digest"], shell) == 1
    assert capsys.readouterr().out.splitlines()[0] == f"orca: pid 73091 last alive {silent}: down; the supervisor restarts it within a minute, so read lr-relay.log and the supervisor's log"


def test_digest_names_a_relay_with_no_alive_record_and_exits_one(repo, capsys):
    started(repo, "--cci-drive", DRIVE)
    shell = FakeShell()
    capsys.readouterr()

    assert drive.main(["digest"], shell) == 1
    assert capsys.readouterr().out.splitlines()[0] == f"orca: no orca alive record on cci drive {DRIVE}, so no orca runner has run there"


def installed(*versions: str, named: str | None = None) -> Path:
    cache = drive.config_dir() / "plugins" / "cache" / "skills" / "long-running"
    for version in versions:
        (cache / version).mkdir(parents=True, exist_ok=True)
    release.link().parent.mkdir(parents=True, exist_ok=True)
    release.link().symlink_to(cache / (named or versions[-1]))
    return cache


@pytest.mark.parametrize(("caller", "reexecs"), [(None, True), ("0.7.99", False), ("0.7.320", True)])
def test_a_script_behind_the_installed_plugin_reexecutes_the_installed_copy_unless_a_process_of_its_own_version_started_it(repo, monkeypatch, caller, reexecs):
    """On Oct 26 the coordinator ran holds.py from 0.7.99 with 0.7.320 installed, and the runner could not read the holds line it left (#131579); a brief naming a versioned desk-wait.sh path then refused instead of running (#136583)."""
    monkeypatch.setattr(drive, "plugin_manifest", lambda: {"name": "long-running", "version": "0.7.99"})
    cache = installed("0.7.99", "0.7.320")
    if caller:
        monkeypatch.setenv(drive.CALLER_VERSION_ENV, caller)
    else:
        monkeypatch.delenv(drive.CALLER_VERSION_ENV, raising=False)
    execs: list[list[str]] = []
    monkeypatch.setattr(drive, "reexec", execs.append)
    monkeypatch.setattr(drive.sys, "argv", ["holds.py", "add", "#1"])

    drive.run_installed("/plugins/cache/skills/long-running/0.7.99/skills/long-running/scripts/holds.py")

    assert execs == ([[drive.sys.executable, str(cache / "0.7.320" / "skills" / "long-running" / "scripts" / "holds.py"), "add", "#1"]] if reexecs else [])


def test_a_script_no_older_than_the_install_link_runs_in_place_instead_of_re_executing_forever(repo, monkeypatch):
    monkeypatch.setattr(drive, "plugin_manifest", lambda: {"name": "long-running", "version": "0.7.99"})
    installed("0.7.50", "0.7.99", named="0.7.50")
    monkeypatch.delenv(drive.CALLER_VERSION_ENV, raising=False)
    execs: list[list[str]] = []
    monkeypatch.setattr(drive, "reexec", execs.append)

    drive.run_installed("holds.py")

    assert execs == []


def test_a_current_script_marks_its_children_with_its_version(repo, monkeypatch):
    monkeypatch.setattr(drive, "plugin_manifest", lambda: {"name": "long-running", "version": "0.7.320"})
    installed("0.7.320")
    monkeypatch.delenv(drive.CALLER_VERSION_ENV, raising=False)
    drive.run_installed("holds.py")
    assert drive.os.environ[drive.CALLER_VERSION_ENV] == "0.7.320"


def released(origin: Path) -> Path:
    plugin = origin / "plugins" / "long-running"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "scripts").mkdir()
    (plugin / "bin").mkdir()
    (plugin / "reference").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "long-running", "version": "1.2.3"}))
    (plugin / "scripts" / "tool.py").write_text("print('released')\n")
    (plugin / "scripts" / "queue_tool.py").write_text("QUEUE = 1\n")
    (plugin / "reference" / "finder-brief.md").write_text("brief\n")
    (plugin / "reference" / "orca-lane-brief.md").write_text("lane\n")
    (plugin / "bin" / "tool.py").symlink_to("../scripts/tool.py")
    (origin / "README.md").write_text("outside the plugin\n")
    commit(origin, "release")
    subprocess.run(["git", "-C", str(origin), "tag", "long-running--v1.2.3"], check=True)
    return plugin


def commit(origin: Path, message: str) -> None:
    git = ["git", "-C", str(origin), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    if not (origin / ".git").is_dir():
        subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)


def installed_copy(plugin: Path, copy: Path) -> Path:
    shutil.copytree(plugin, copy, symlinks=True)
    (copy / "scripts" / "__pycache__").mkdir()
    (copy / "scripts" / "__pycache__" / "queue_tool.cpython-313.pyc").write_bytes(b"\0")
    (copy / ".in_use").mkdir()
    (copy / ".in_use" / "4242").write_text("")
    (copy / ".orphaned_at").write_text("1793462400000")
    return copy


def test_tree_faults_names_each_release_file_an_install_lacks_or_holds_edited_and_ignores_its_caches(tmp_path):
    """Oct 30: 0.7.656 and 0.7.701 installed without skills/, and 0.7.682's desk-runner.py and merge_queue.py were patched in place; each read as a whole install."""
    origin = tmp_path / "origin"
    copy = installed_copy(released(origin), tmp_path / "cache" / "1.2.3")
    releases = tmp_path / "releases.git"

    assert drive.tree_faults(copy, str(origin), "long-running--v1.2.3", "plugins/long-running", releases) == []

    (copy / "scripts" / "queue_tool.py").write_text("QUEUE = 2\n")
    (copy / "reference" / "finder-brief.md").unlink()
    (copy / "bin" / "tool.py").unlink()
    assert drive.tree_faults(copy, str(origin), "long-running--v1.2.3", "plugins/long-running", releases) == [
        "the install holds 4 files where the release holds 6",
        "bin/tool.py is missing",
        "reference/finder-brief.md is missing",
        "scripts/queue_tool.py differs from the release",
    ]
    assert drive.tree_faults(copy, str(origin), "long-running--v9.9.9", "plugins/long-running", releases)[0].startswith("the release tag long-running--v9.9.9 did not fetch from")


def test_tree_faults_names_an_install_copied_from_the_default_branch_past_its_tag(tmp_path):
    """Oct 31 8:43 AM (#257362): 0.7.729's cache was copied from main at #1442, past its tag, and read as that release."""
    origin = tmp_path / "origin"
    plugin = released(origin)
    (plugin / "scripts" / "extra_tool.py").write_text("PARKED = 'waiting'\n")
    (plugin / "scripts" / "queue_tool.py").write_text("QUEUE = 2\n")
    commit(origin, "#1442 lands after the bump")
    copy = installed_copy(plugin, tmp_path / "cache" / "1.2.3")

    assert drive.tree_faults(copy, str(origin), "long-running--v1.2.3", "plugins/long-running", tmp_path / "releases.git") == [
        "the install holds 7 files where the release holds 6",
        "scripts/extra_tool.py is not in the release",
        "scripts/queue_tool.py differs from the release",
    ]


def test_tree_faults_names_every_file_of_a_directory_the_install_lacks(tmp_path):
    """Oct 31 8:43 AM (#257362): 0.7.730's cache lacked skills/long-running/reference/ while `claude plugin update` reported it current."""
    origin = tmp_path / "origin"
    copy = installed_copy(released(origin), tmp_path / "cache" / "1.2.3")
    shutil.rmtree(copy / "reference")

    assert drive.tree_faults(copy, str(origin), "long-running--v1.2.3", "plugins/long-running", tmp_path / "releases.git") == [
        "the install holds 4 files where the release holds 6",
        "reference/finder-brief.md is missing",
        "reference/orca-lane-brief.md is missing",
    ]


@pytest.mark.parametrize(
    ("runs", "fault"),
    [
        ([{"name": "long-running CI", "run_attempt": 1, "status": "completed", "conclusion": "success", "html_url": "u1"}], ""),
        ([{"name": "long-running CI", "run_attempt": 1, "status": "completed", "conclusion": "failure", "html_url": "u1"}, {"name": "long-running CI", "run_attempt": 2, "status": "completed", "conclusion": "success", "html_url": "u2"}], ""),
        ([{"name": "long-running CI", "run_attempt": 1, "status": "completed", "conclusion": "failure", "html_url": "u1"}], "the long-running CI run on 7ae7b77930, the commit long-running--v1.2.4 was bumped from, ended failure: u1"),
        ([{"name": "long-running CI", "run_attempt": 1, "status": "in_progress", "conclusion": None, "html_url": "u1"}], "the long-running CI run on 7ae7b77930, the commit long-running--v1.2.4 was bumped from, is in_progress: u1"),
        ([{"name": "Guides", "run_attempt": 1, "status": "completed", "conclusion": "success", "html_url": "u0"}], "no long-running CI run on main has started for 7ae7b77930, the commit long-running--v1.2.4 was bumped from"),
    ],
    ids=["passed", "passed-on-rerun", "failed", "running", "none"],
)
def test_ci_fault_reads_the_ci_run_on_the_commit_a_release_was_bumped_from(tmp_path, monkeypatch, runs, fault):
    plugins = tmp_path / ".claude" / "plugins"
    (plugins / "marketplaces" / "skills" / ".claude-plugin").mkdir(parents=True)
    (plugins / "marketplaces" / "skills" / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [
        {"name": "long-running", "source": {"source": "git-subdir", "url": "https://github.com/yasyf/cc-skills.git", "path": "plugins/long-running", "ref": "long-running--v1.2.4"}, "version": "1.2.4"},
    ]}))
    monkeypatch.setattr(drive, "PLUGIN", plugins / "cache" / "skills" / "long-running" / "1.2.4")
    merged = "7ae7b7793" + "0" * 31
    answers = {
        "repos/yasyf/cc-skills/commits/long-running--v1.2.4": {"sha": "d3b27ea6f" + "0" * 31, "parents": [{"sha": merged}]},
        f"repos/yasyf/cc-skills/actions/runs?head_sha={merged}&per_page=100": {"workflow_runs": runs},
    }
    monkeypatch.setattr(drive.subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, json.dumps(answers[argv[-1]]), ""))

    assert drive.ci_fault("1.2.4") == fault


def test_release_faults_reads_the_release_the_marketplace_entry_pins(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    plugins = tmp_path / ".claude" / "plugins"
    copy = installed_copy(released(origin), plugins / "cache" / "skills" / "long-running" / "1.2.3")
    (plugins / "marketplaces" / "skills" / ".claude-plugin").mkdir(parents=True)
    (plugins / "marketplaces" / "skills" / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [
        {"name": "cc-inbox", "source": "./plugins/cc-inbox"},
        {"name": "long-running", "source": {"source": "git-subdir", "url": str(origin), "path": "plugins/long-running", "ref": "long-running--v1.2.4"}, "version": "1.2.4"},
    ]}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setattr(drive, "PLUGIN", plugins / "cache" / "skills" / "long-running" / "1.2.4")

    assert drive.release_faults("1.2.3") == []

    (copy / "reference" / "orca-lane-brief.md").unlink()
    assert drive.release_faults("1.2.3") == ["the install holds 5 files where the release holds 6", "reference/orca-lane-brief.md is missing"]
    assert drive.release_faults("1.2.9") == [f"{plugins / 'cache' / 'skills' / 'long-running' / '1.2.9'} holds no install of 1.2.9"]
    monkeypatch.setattr(drive, "PLUGIN", tmp_path / "cc-skills" / "plugins" / "long-running")
    assert drive.release_faults("1.2.3") == []


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], {"role": "build", "successor-of": "conflicts-2"}),
        (["--role", "fix", "--successor-of", "desk-9"], {"role": "fix", "successor-of": "desk-9"}),
    ],
)
def test_a_launch_records_its_placement_role_and_the_worker_it_succeeds(repo, tmp_path, argv, expected):
    """cc-skills#1335 and #1474: a relaunch names the worker it takes over in its launch record, so the registry moves that worker's PRs and mail to it."""
    started(repo, "--cci-drive", DRIVE)
    brief = tmp_path / "conflicts-3.brief.md"
    brief.write_text("Replaces: conflicts-2\nccx: role=build place=local\n# conflicts-3\n")
    shell = FakeShell()

    assert drive.main(["spawned", "--lane", "conflicts-3", "--brief", str(brief), "--drive", "900424b6", "--model", "opus", "--placement", "sprite", *argv], shell) == 0

    [post] = [call for call in shell.calls if call[:2] == ["cci", "post"]]
    attrs = dict(value.split("=", 1) for flag, value in zip(post, post[1:]) if flag == "--attr")
    assert attrs == {"brief": str(brief), "model": "opus", "placement": "sprite"} | expected


def test_the_scratch_root_defaults_under_claude_and_follows_its_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("LONG_RUNNING_SCRATCH_ROOT", raising=False)
    assert drive.scratch_dir() == Path.home() / ".claude" / "scratch"
    monkeypatch.setenv("LONG_RUNNING_SCRATCH_ROOT", str(tmp_path))
    assert drive.scratch_dir() == tmp_path
