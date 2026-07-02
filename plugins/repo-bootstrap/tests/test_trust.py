"""trust subcommand: sets projects[<abspath>].hasTrustDialogAccepted in
~/.claude.json via an atomic 0600 read-modify-write, idempotently, preserving
every other key, and best-effort mirrors it into cc-pool account configs.

Every test points --home / config at a tmp dir; none ever touches the real
~/.claude.json or ~/.cc-pool.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bootstrap import trust

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "repo-bootstrap" / "scripts"
BOOTSTRAP = SCRIPTS / "bootstrap.py"


def _read(config: Path) -> dict:
    return json.loads(config.read_text())


# --- set_trusted unit ---

def test_sets_flag_true(tmp_path):
    config = tmp_path / ".claude.json"
    repo = "/abs/repo"
    assert trust.set_trusted(str(config), repo) is True
    assert _read(config)["projects"][repo]["hasTrustDialogAccepted"] is True


def test_missing_file_treated_as_empty(tmp_path):
    config = tmp_path / ".claude.json"  # never created
    assert not config.exists()
    assert trust.set_trusted(str(config), "/abs/repo") is True
    assert config.exists()
    assert _read(config)["projects"]["/abs/repo"]["hasTrustDialogAccepted"] is True


def test_empty_file_treated_as_empty(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text("")
    assert trust.set_trusted(str(config), "/abs/repo") is True
    assert _read(config)["projects"]["/abs/repo"]["hasTrustDialogAccepted"] is True


def test_idempotent_second_run(tmp_path):
    config = tmp_path / ".claude.json"
    repo = "/abs/repo"
    assert trust.set_trusted(str(config), repo) is True
    # Second run makes no change and reports it by returning False.
    assert trust.set_trusted(str(config), repo) is False
    assert _read(config)["projects"][repo]["hasTrustDialogAccepted"] is True


def test_creates_missing_projects_entry_preserving_other_projects(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"projects": {"/other/repo": {"hasTrustDialogAccepted": True, "allowedTools": ["a"]}}}))
    trust.set_trusted(str(config), "/abs/repo")
    projects = _read(config)["projects"]
    assert projects["/abs/repo"]["hasTrustDialogAccepted"] is True
    # The pre-existing project entry is untouched.
    assert projects["/other/repo"] == {"hasTrustDialogAccepted": True, "allowedTools": ["a"]}


def test_preserves_sibling_keys(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({
        "userID": "u-123",
        "numStartups": 7,
        "oauthAccount": {"emailAddress": "jane@example.com"},
        "projects": {"/abs/repo": {"history": [{"display": "hi"}]}},
    }))
    trust.set_trusted(str(config), "/abs/repo")
    data = _read(config)
    assert data["userID"] == "u-123"
    assert data["numStartups"] == 7
    assert data["oauthAccount"] == {"emailAddress": "jane@example.com"}
    # Existing keys inside the target project entry are preserved too.
    assert data["projects"]["/abs/repo"]["history"] == [{"display": "hi"}]
    assert data["projects"]["/abs/repo"]["hasTrustDialogAccepted"] is True


def test_leaves_file_mode_0600(tmp_path):
    config = tmp_path / ".claude.json"
    trust.set_trusted(str(config), "/abs/repo")
    assert (config.stat().st_mode & 0o777) == 0o600


# --- trust_repo: target resolution + cc-pool account fan-out ---

def test_target_resolved_to_absolute_path(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    workdir = tmp_path / "myrepo"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    assert trust.trust_repo(".", home=str(tmp_path), config=str(config)) == 0
    assert str(workdir) in _read(config)["projects"]


def test_applies_to_cc_pool_account_configs(tmp_path):
    home = tmp_path
    base = home / ".claude.json"
    accounts = home / ".cc-pool" / "accounts"
    acct1 = accounts / "acct-1" / ".claude.json"
    acct2 = accounts / "acct-2" / ".claude.json"
    for cfg in (acct1, acct2):
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"userID": f"u-{cfg.parent.name}", "projects": {}}))

    assert trust.trust_repo("/abs/repo", home=str(home)) == 0

    for cfg in (base, acct1, acct2):
        assert _read(cfg)["projects"]["/abs/repo"]["hasTrustDialogAccepted"] is True
    # Sibling key in each account config survives the fan-out.
    assert _read(acct1)["userID"] == "u-acct-1"


def test_no_cc_pool_accounts_is_silent(tmp_path):
    # No ~/.cc-pool at all: the glob matches nothing and trust_repo still succeeds.
    assert trust.trust_repo("/abs/repo", home=str(tmp_path)) == 0
    assert (tmp_path / ".claude.json").exists()


# --- end-to-end through the CLI dispatch ---

def test_cli_dispatch(tmp_path):
    home = tmp_path
    repo = tmp_path / "repo"
    repo.mkdir()
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "trust", "--target", str(repo), "--home", str(home)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TRUSTED" in result.stdout
    data = json.loads((home / ".claude.json").read_text())
    assert data["projects"][str(repo)]["hasTrustDialogAccepted"] is True
