from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_desk_runner import runner_module


@pytest.mark.parametrize("changed", runner_module.RULINGS_SOURCES)
def test_rulings_digest_tracks_the_canonical_cause_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    sources = tmp_path / "long_running"
    sources.mkdir()
    contents = {name: f"canonical {name}".encode() for name in runner_module.RULINGS_SOURCES}
    for name, body in contents.items():
        (sources / f"{name}.py").write_bytes(body)
    monkeypatch.setattr(runner_module, "SCRIPTS", tmp_path)
    expected = hashlib.sha256(b"".join(contents.values())).hexdigest()[:16]
    assert runner_module.rulings_digest() == expected
    for name in contents:
        (tmp_path / f"{name}.py").write_bytes(b"command wrapper")
    assert runner_module.rulings_digest() == expected
    contents[changed] += b" changed cause rule"
    (sources / f"{changed}.py").write_bytes(contents[changed])
    assert runner_module.rulings_digest() == hashlib.sha256(b"".join(contents.values())).hexdigest()[:16]
    assert runner_module.rulings_digest() != expected
