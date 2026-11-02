from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-creds.py"
spec = importlib.util.spec_from_file_location("desk_creds", SCRIPT)
desk_creds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk_creds)


@pytest.fixture
def port():
    server = desk_creds.server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def credentials(hours):
    return {"Version": 1, "AccessKeyId": "AKIA", "SecretAccessKey": "secret", "SessionToken": "token", "Expiration": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()}


def put(port, profile, body):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/{profile}", data=json.dumps(body).encode(), method="PUT", headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request).status
    except urllib.error.HTTPError as refused:
        return refused.code


def test_a_pushed_profile_reads_back_for_its_credential_process(port, capsys):
    pushed = credentials(1)
    assert put(port, "release-operator", pushed) == 204
    assert desk_creds.get("release-operator", "", port) == 0
    assert json.loads(capsys.readouterr().out) == pushed
    assert desk_creds.get("release-operator", "AccessKeyId", port) == 0
    assert capsys.readouterr().out == "AKIA\n"


def test_expired_or_unpushed_credentials_are_never_served(port):
    assert put(port, "core-auto", credentials(-1)) == 400
    assert desk_creds.get("core-auto", "", port) == 1


def test_the_host_reads_landing_with_its_overrides_merged_one_level_down():
    raw = {"drive": "d", "landing": {"adapter": {"kind": "trunk-stack-enqueue", "sprite_keys": {"GH_TOKEN": ["gh", "auth", "token"]}}, "env": {"AWS_PROFILE": "release-operator"}}, "host": {"landing": {"adapter": {"sprite_keys": {"GH_TOKEN": ["printenv", "GH_TOKEN"]}}, "status_card": False}}}
    landing = desk_creds.host_config(raw)["landing"]
    assert landing["adapter"] == {"kind": "trunk-stack-enqueue", "sprite_keys": {"GH_TOKEN": ["printenv", "GH_TOKEN"]}}
    assert landing["env"] == {"AWS_PROFILE": "release-operator"} and landing["status_card"] is False


def test_a_pushed_secret_reads_back_until_it_expires(port):
    ahead = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    assert put(port, "secret/GH_TOKEN", {"Value": "gho_user", "Expiration": ahead}) == 204
    assert desk_creds.fetch("secret/GH_TOKEN", port) == {"Value": "gho_user", "Expiration": ahead}
    assert put(port, "secret/GH_TOKEN", {"Value": "gho_old", "Expiration": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}) == 400
    assert put(port, "secret/BUILDKITE_TOKEN", {"Expiration": ahead}) == 400
    with pytest.raises(urllib.error.HTTPError):
        desk_creds.fetch("secret/BUILDKITE_TOKEN", port)


def test_a_pushed_user_token_replaces_the_one_each_running_desk_reads_gh_with(port, tmp_path, monkeypatch):
    """Root #335369: the landing runner on dar-desk-1 kept the GH_TOKEN it started with, which lapsed, so every pass failed its fetch of origin/dev."""
    monkeypatch.setattr(desk_creds, "SHM", tmp_path)
    gh = tmp_path / "d-gh"
    gh.mkdir()
    hosts = gh / "hosts.yml"
    hosts.touch(mode=0o600)
    hosts.write_text("github.com:\n    users:\n        yasyf:\n            oauth_token: gho_old\n    git_protocol: https\n    oauth_token: gho_old\n    user: yasyf\n")
    ahead = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    assert put(port, "secret/BUILDKITE_TOKEN", {"Value": "bk", "Expiration": ahead}) == 204
    assert "gho_old" in hosts.read_text()
    assert put(port, "secret/GH_TOKEN", {"Value": "gho_new", "Expiration": ahead}) == 204
    assert hosts.read_text() == "github.com:\n    users:\n        yasyf:\n            oauth_token: gho_new\n    git_protocol: https\n    oauth_token: gho_new\n    user: yasyf\n"
    assert hosts.stat().st_mode & 0o777 == 0o600 and [path.name for path in gh.iterdir()] == ["hosts.yml"]

def test_push_hands_each_secret_over_standard_input_with_an_expiry_three_pushes_out(tmp_path, monkeypatch):
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"host": {"sprite": "dar-desk-1", "aws_profiles": ["core-auto"], "secrets": {"GH_TOKEN": ["gh", "auth", "token"]}}}))
    calls = []

    def fake(argv, input=None, **_):
        calls.append((argv, input))
        if argv[:2] == ["aws", "configure"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(credentials(1)), "")
        if argv[0] == "gh":
            return subprocess.CompletedProcess(argv, 0, "gho_user\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(desk_creds.subprocess, "run", fake)
    assert desk_creds.push(config, 9181) == 0
    sent = {argv[-1].split("9181/")[1]: json.loads(body) for argv, body in calls if argv[0] == "sprite"}
    assert sent["core-auto"]["AccessKeyId"] == "AKIA"
    assert sent["secret/GH_TOKEN"]["Value"] == "gho_user"
    left = datetime.fromisoformat(sent["secret/GH_TOKEN"]["Expiration"]) - datetime.now(timezone.utc)
    assert timedelta(minutes=44) < left <= timedelta(minutes=45)
    assert not any("gho_user" in part for argv, _ in calls for part in argv)


def test_run_keeps_the_user_token_out_of_the_environment_and_exports_the_rest(tmp_path, monkeypatch):
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({
        "drive": "d",
        "host": {"aws_profiles": ["core-auto"], "region": "us-west-2", "github_app": {"name": "app", "client_id": "Iv1", "private_key_command": ["aws", "ssm", "get-parameter"]}, "gh_user": "yasyf", "tz": "America/Los_Angeles", "secrets": {"GH_TOKEN": ["gh", "auth", "token"]}, "ssm_profile": "core-auto", "ssm": {"BUILDKITE_TOKEN": "/ci/tools/buildkite-read-token"}},
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(desk_creds, "SHM", tmp_path / "shm")
    monkeypatch.setattr(desk_creds.os, "environ", dict(desk_creds.os.environ))
    monkeypatch.setattr(desk_creds, "fetch", lambda key, port: {"Value": {"secret/GH_TOKEN": "gho_user"}[key]})
    reads = []
    monkeypatch.setattr(desk_creds.subprocess, "run", lambda argv, **_: reads.append(argv) or subprocess.CompletedProcess(argv, 0, "bk-read\n", ""))
    monkeypatch.setattr(desk_creds.os, "dup2", lambda fd, to: None)
    execs = []
    monkeypatch.setattr(desk_creds.os, "execv", lambda path, argv: execs.append(argv))
    desk_creds.run(config, "landing")
    assert "GH_TOKEN" not in desk_creds.os.environ
    assert "oauth_token: gho_user" in (tmp_path / "shm/d-gh/hosts.yml").read_text()
    assert desk_creds.os.environ["GH_CONFIG_DIR"] == str(tmp_path / "shm/d-gh")
    assert desk_creds.os.environ["BUILDKITE_TOKEN"] == "bk-read"
    assert reads == [["aws", "ssm", "get-parameter", "--profile", "core-auto", "--region", "us-west-2", "--name", "/ci/tools/buildkite-read-token", "--with-decryption", "--query", "Parameter.Value", "--output", "text"]]
    assert "credential_process = " in (tmp_path / ".aws/config").read_text() and " get core-auto\n" in (tmp_path / ".aws/config").read_text()
    assert 'private_key_command = ["aws", "ssm", "get-parameter"]' in (tmp_path / ".config/ccx/github-app.toml").read_text()
    assert (tmp_path / "landing-runner.log").exists()
    assert execs[0][-4:] == ["--config", str(config), "--desk", "landing"]


def test_roll_refuses_a_version_whose_main_ci_is_red_and_moves_nothing(tmp_path, monkeypatch, capsys):
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"version": "0.7.927", "host": {"sprite": "dar-desk-1"}, "landing": {}}))
    calls = []
    monkeypatch.setattr(desk_creds.drive, "release_faults", lambda version: [])
    monkeypatch.setattr(desk_creds.drive, "ci_fault", lambda version: "the long-running CI run on abc1234 ended failure")
    monkeypatch.setattr(desk_creds.subprocess, "run", lambda argv, **_: calls.append(argv))
    assert desk_creds.roll(config, "landing", "") == 1
    assert calls == []
    assert "ended failure" in capsys.readouterr().err


def test_roll_copies_the_pinned_version_then_stops_the_desk_runner_at_the_end_of_its_pass(tmp_path, monkeypatch):
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"version": "0.7.927", "host": {"sprite": "dar-desk-1"}, "landing": {}}))
    calls = []

    def fake(argv, input=None, **_):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(desk_creds.drive, "release_faults", lambda version: [])
    monkeypatch.setattr(desk_creds.drive, "ci_fault", lambda version: "")
    monkeypatch.setattr(desk_creds.subprocess, "run", fake)
    assert desk_creds.roll(config, "landing", "") == 0
    stop = calls[-1]
    assert stop[:5] == ["sprite", "exec", "-s", "dar-desk-1", "--no-stdin"]
    assert stop[-5:] == ["/home/sprite/.claude/plugins/cache/skills/long-running/0.7.927", "/home/sprite/.claude/long-running/current", str(config), "landing", "1200"]
    assert ".stop.$pid" in stop[stop.index("-c") + 1]
