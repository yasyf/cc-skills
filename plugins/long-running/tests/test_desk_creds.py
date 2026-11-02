from __future__ import annotations

import importlib.util
import json
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


def test_run_keeps_the_user_token_out_of_the_environment_and_points_reads_at_the_app(tmp_path, monkeypatch):
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d", "host": {"aws_profiles": ["core-auto"], "region": "us-west-2", "github_app": {"name": "app", "client_id": "Iv1", "private_key_command": ["aws", "ssm", "get-parameter"]}, "gh_user": "yasyf", "tz": "America/Los_Angeles"}}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DESK_GH_USER_TOKEN", "gho_user")
    monkeypatch.setenv("GH_TOKEN", "gho_user")
    monkeypatch.setattr(desk_creds, "SHM", tmp_path / "shm")
    monkeypatch.setattr(desk_creds.os, "environ", dict(desk_creds.os.environ))
    execs = []
    monkeypatch.setattr(desk_creds.os, "execv", lambda path, argv: execs.append(argv))
    desk_creds.run(config, "landing")
    assert "DESK_GH_USER_TOKEN" not in desk_creds.os.environ and "GH_TOKEN" not in desk_creds.os.environ
    assert "oauth_token: gho_user" in (tmp_path / "shm/d-gh/hosts.yml").read_text()
    assert desk_creds.os.environ["GH_CONFIG_DIR"] == str(tmp_path / "shm/d-gh")
    assert "credential_process = " in (tmp_path / ".aws/config").read_text() and " get core-auto\n" in (tmp_path / ".aws/config").read_text()
    assert 'private_key_command = ["aws", "ssm", "get-parameter"]' in (tmp_path / ".config/ccx/github-app.toml").read_text()
    assert execs[0][-4:] == ["--config", str(config), "--desk", "landing"]
