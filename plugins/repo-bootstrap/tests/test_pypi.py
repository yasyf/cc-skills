"""check-name subcommand: status -> exit code mapping, name validation."""

from __future__ import annotations

import urllib.error

from bootstrap import pypi


class FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_status(monkeypatch, *, status=None, http_error=None, exc=None):
    def fake_urlopen(url):
        if exc is not None:
            raise exc
        if http_error is not None:
            raise urllib.error.HTTPError(url, http_error, "err", {}, None)
        return FakeResp(status)

    monkeypatch.setattr(pypi.urllib.request, "urlopen", fake_urlopen)


def test_available_404(monkeypatch, capsys):
    _patch_status(monkeypatch, http_error=404)
    assert pypi.main("brand-new-pkg") == 0
    assert capsys.readouterr().out.strip() == "AVAILABLE"


def test_taken_200(monkeypatch, capsys):
    _patch_status(monkeypatch, status=200)
    assert pypi.main("requests") == 1
    assert "TAKEN (requests is an existing project)" in capsys.readouterr().out


def test_unknown_other_status(monkeypatch, capsys):
    _patch_status(monkeypatch, http_error=503)
    assert pypi.main("whatever") == 2
    assert "UNKNOWN: HTTP 503" in capsys.readouterr().out


def test_network_failure_is_unknown(monkeypatch, capsys):
    _patch_status(monkeypatch, exc=OSError("no network"))
    assert pypi.main("whatever") == 2
    assert "UNKNOWN: HTTP 000" in capsys.readouterr().out


def test_invalid_name_short_circuits(monkeypatch, capsys):
    # invalid name must return 3 before any network call
    def boom(url):
        raise AssertionError("should not hit network")

    monkeypatch.setattr(pypi.urllib.request, "urlopen", boom)
    assert pypi.main("bad name!") == 3
    assert "INVALID: bad name! is not a valid PyPI project name" in capsys.readouterr().out
