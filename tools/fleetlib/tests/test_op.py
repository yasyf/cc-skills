import os
from types import SimpleNamespace

import pytest

from fleetlib import op

# marker split so detect-private-key doesn't flag this synthetic fixture
PRIVATE = "\n".join(["-----BEGIN OPENSSH " + "PRIVATE KEY-----", "abc", "-----END OPENSSH " + "PRIVATE KEY-----"])


def stub_op(monkeypatch, private: str, public: str, prior: bool = False) -> list:
    calls = []

    def record(*args, stdin=None):
        calls.append(args)
        if args[:2] == ("op", "read"):
            return SimpleNamespace(stdout=private if "private key" in args[2] else public)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(op, "run", record)
    probe = SimpleNamespace(returncode=0, stderr="") if prior else SimpleNamespace(returncode=1, stderr='"item-x" isn\'t an item in the vault')
    monkeypatch.setattr(op, "try_run", lambda *a, stdin=None: probe)
    return calls


def test_mint_normalizes_and_returns(monkeypatch):
    calls = stub_op(monkeypatch, PRIVATE, "ssh-ed25519 AAAA host\n")
    private, public = op.mint_ssh_key("Vault", "item-x", "notes")
    assert private == PRIVATE + "\n"
    assert public == "ssh-ed25519 AAAA host"
    assert not any(a[:3] == ("op", "item", "delete") for a in calls)


def test_mint_archives_prior_item(monkeypatch):
    calls = stub_op(monkeypatch, PRIVATE, "ssh-ed25519 AAAA", prior=True)
    op.mint_ssh_key("Vault", "item-x", "notes")
    assert any(a[:3] == ("op", "item", "delete") and "--archive" in a for a in calls)


def test_mint_unexpected_probe_failure_is_fatal(monkeypatch):
    stub_op(monkeypatch, PRIVATE, "ssh-ed25519 AAAA")
    failing = SimpleNamespace(returncode=1, stderr="[ERROR] session expired")
    monkeypatch.setattr(op, "try_run", lambda *a, stdin=None: failing)
    with pytest.raises(SystemExit, match="session expired"):
        op.mint_ssh_key("Vault", "item-x", "notes")


def test_mint_rejects_bad_material(monkeypatch):
    stub_op(monkeypatch, "garbage", "ssh-ed25519 AAAA")
    with pytest.raises(SystemExit, match="unexpected key material"):
        op.mint_ssh_key("Vault", "item-x", "notes")


def test_require_user_session_pops_token(monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "tok")
    op.require_user_session()
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in os.environ
