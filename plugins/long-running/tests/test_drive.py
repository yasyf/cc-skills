from __future__ import annotations

import json
import subprocess
from pathlib import Path

import drive
import pytest
from conftest import LEDGER, FakeShell

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
        "cci_drive": None,
        "started_at": "",
        "updated_at": "",
    }
    assert capsys.readouterr().out.startswith(f"drive 900424b6 on ledger {LEDGER} at ")


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


def test_context_rewrites_the_dashboard_context_with_the_state_dir_as_the_cci_drive(repo, tmp_path):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))
    path = tmp_path / "release-v3" / "dashboard" / "context.json"
    path.unlink()

    assert drive.main(["context", "--drive", "900424b6"]) == 0
    assert json.loads(path.read_text())["cci_drive"] == "release-v3"


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


def test_list_json_reads_back_every_drive(repo, capsys):
    started(repo)
    capsys.readouterr()

    assert drive.main(["list", "--json"]) == 0

    assert [entry["drive"] for entry in json.loads(capsys.readouterr().out)] == ["900424b6"]


def test_record_refuses_a_malformed_pr(repo):
    with pytest.raises(SystemExit):
        drive.main(["record", "--session", ROOT_SESSION, "--lane", "x", "--cwd", str(repo), "--pr", "#12"])



def thread(*argv: str) -> int:
    return drive.main(["thread", "--lane", "owner-links-comms", "--channel", "C0AAAAAAAA1", "--thread-ts", "1790901035.467689", "--posted-ts", "1790901997.422529", *argv])


def test_a_post_in_the_drive_joins_its_watch_list(repo, tmp_path, capsys):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))
    watched = tmp_path / "release-v3" / "slack" / "watched-threads.jsonl"

    assert thread("--session", ROOT_SESSION) == 0
    assert thread("--session", WORKER_SESSION, "--drive", "900424b6") == 0

    rows = [json.loads(line) for line in watched.read_text().splitlines()]
    assert [row | {"posted_at": ""} for row in rows] == [
        {
            "channel": "C0AAAAAAAA1",
            "thread_ts": "1790901035.467689",
            "posted_ts": "1790901997.422529",
            "posted_at": "",
            "session": session,
            "lane": "owner-links-comms",
        }
        for session in (ROOT_SESSION, WORKER_SESSION)
    ]
    assert capsys.readouterr().out.splitlines()[-1] == f"C0AAAAAAAA1/1790901035.467689 is on the drive's Slack watch list at {watched}"


def test_a_post_outside_any_drive_is_not_watched(repo, tmp_path):
    started(repo, "--state-dir", str(tmp_path / "release-v3"))

    assert thread("--session", WORKER_SESSION) == 0

    assert not (tmp_path / "release-v3" / "slack").exists()
