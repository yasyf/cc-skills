from pathlib import Path

import pytest

from drive import gate


def test_a_gate_refuses_a_sessions_first_try_naming_every_finding_and_lets_its_repeat_through_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    findings = ["no --only", "a second release runs"]
    assert gate.gate(None, "release ci hotfix 7", findings, "s1") == f"refused once: no --only; a second release runs. {gate.REPEAT}"
    assert gate.gate(None, "release ci hotfix 7", findings, "s2") is not None
    assert gate.gate(None, "release ci hotfix 7", findings, "s1") is None
    assert "going through, as a repeat of a try this gate refused once" in capsys.readouterr().err
    assert gate.gate(None, "release ci hotfix 8", [], "s1") is None


def test_a_gate_whose_store_cannot_be_read_lets_the_try_through_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    path = gate.store(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("{not json")
    assert gate.gate(None, "page", ["no --users record"], "s1") is None
    assert "could not be read or written" in capsys.readouterr().err
