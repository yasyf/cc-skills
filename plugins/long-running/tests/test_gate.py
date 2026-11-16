import json
from pathlib import Path

import pytest

from drive import gate, state


def test_a_gate_refuses_a_sessions_first_try_naming_every_finding_and_lets_its_repeat_through_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    findings = ["no --only", "a second release runs"]
    assert gate.gate(None, "release ci hotfix 7", findings, "s1") == f"refused once: no --only; a second release runs. {gate.REPEAT}"
    assert gate.gate(None, "release ci hotfix 7", findings, "s2") is not None
    assert gate.gate(None, "release ci hotfix 7", findings, "s1") is None
    assert "going through, as a repeat of a try this gate refused once" in capsys.readouterr().err
    assert gate.gate(None, "release ci hotfix 8", [], "s1") is None


def test_a_gate_refuses_a_new_finding_once_on_its_own_and_a_dry_run_never_uses_up_the_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.gate(None, "ask j1", ["no link"], "s1", dry_run=True) is not None
    assert gate.gate(None, "ask j1", ["no link"], "s1") is not None
    assert gate.gate(None, "ask j1", ["no link"], "s1", dry_run=True) is None
    assert gate.gate(None, "ask j1", ["no link", "summary too long"], "s1") == f"refused once: no link; summary too long. {gate.REPEAT}"
    assert gate.gate(None, "ask j1", ["summary too long"], "s1") is None
    assert gate.gate(None, "ask j1", ["no link", "summary too long"], "s1") is None
    assert capsys.readouterr().err.count("going through, as a repeat of a try this gate refused once") == 2


def test_a_gate_whose_store_cannot_be_read_lets_the_try_through_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    path = gate.store(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("a file where the store's folder goes")
    assert gate.gate(None, "page", ["no --users record"], "s1") is None
    assert "could not be made" in capsys.readouterr().err


def test_a_refusal_on_an_asked_or_incident_order_appends_a_blocked_event_and_a_process_order_none() -> None:
    state.drive_dir("demo").mkdir(parents=True, exist_ok=True)
    asked = state.new_order("ship it", "terminal", None, {}, state.stamp())
    assert gate.gate("demo", "drop o1", ["a PR is queued to land"], "s1", ("o1", asked)) is not None
    assert gate.gate("demo", "drop o2", ["a PR is queued to land"], "s1", ("o2", asked | {"class": "process"})) is not None
    lines = [json.loads(line) for line in state.events_path("demo").read_text().splitlines()]
    assert [(line["kind"], line["order"], line["action"], line["repeat"]) for line in lines] == [(gate.BLOCKED, "o1", "drop o1", False)]
