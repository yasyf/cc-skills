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
