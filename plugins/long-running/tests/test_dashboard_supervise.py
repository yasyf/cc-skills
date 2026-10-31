from pathlib import Path

from supervise import HERE, job

CONFIG = Path("/drive/dashboard.json")
SHELL = {"PATH": "/opt/homebrew/bin:/usr/bin", "HOME": "/Users/me", "CEREBRAS_API_KEY": "k", "AWS_PROFILE": "release-operator"}


def test_launchd_restarts_the_dashboard_from_this_tree_whenever_it_exits():
    spec = job(CONFIG, {"drive": "d1"}, SHELL)
    assert spec["KeepAlive"] is True and spec["RunAtLoad"] is True
    assert spec["Label"] == "com.yasyf.long-running.dashboard.d1"
    assert spec["ProgramArguments"][1:] == [str(HERE / "app" / "server.py"), "--config", "/drive/dashboard.json"]
    assert spec["WorkingDirectory"] == "/drive"
    assert spec["StandardErrorPath"] == spec["StandardOutPath"] == "/drive/server.log"


def test_the_restarted_dashboard_finds_the_installing_shell_s_tools_and_assistant_key_and_nothing_else():
    assert job(CONFIG, {"drive": "d1"}, SHELL)["EnvironmentVariables"] == {"PATH": "/opt/homebrew/bin:/usr/bin:/Users/me/.claude/plugins/marketplaces/cc-inbox/plugin/bin", "HOME": "/Users/me", "CEREBRAS_API_KEY": "k"}


def test_the_restarted_dashboard_still_finds_cci_while_a_cask_upgrade_has_unlinked_brews_cci():
    """Oct 31 1:35 AM: /api/change raised FileNotFoundError for cci while brew upgraded the cask, since the plist's PATH held no cci but brew's link."""
    assert job(CONFIG, {"drive": "d1"}, SHELL)["EnvironmentVariables"]["PATH"].split(":")[-1] == "/Users/me/.claude/plugins/marketplaces/cc-inbox/plugin/bin"
