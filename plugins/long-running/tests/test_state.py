from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import state
from drive.state import DriveError

REFERENCE = datetime(2026, 11, 9, 14, 0).astimezone()


def test_root_prefers_explicit_then_xdg_then_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LONG_RUNNING_STATE", str(tmp_path / "explicit"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert state.root() == tmp_path / "explicit"
    monkeypatch.delenv("LONG_RUNNING_STATE")
    assert state.root() == tmp_path / "xdg" / "long-running"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.root() == tmp_path / ".local" / "state" / "long-running"


def make(name: str, session: str | None = None, started: str | None = None) -> None:
    data = state.new_drive(name, session, "/work", None)
    if started:
        data["driver"]["started"] = started
    state.create(data)


def test_edit_saves_atomically_and_leaves_no_temp(state_root: Path) -> None:
    make("demo")
    with state.edit("demo") as data:
        data["orders"]["o1"] = {"text": "ship it"}
    assert state.load("demo")["orders"]["o1"]["text"] == "ship it"
    assert not (state_root / "demo" / "drive.json.tmp").exists()
    assert (state_root / "demo" / ".lock").exists()


def test_edit_discards_changes_when_the_body_raises(state_root: Path) -> None:
    make("demo")
    with pytest.raises(RuntimeError), state.edit("demo") as data:
        data["orders"]["o1"] = {"text": "never saved"}
        raise RuntimeError
    assert state.load("demo")["orders"] == {}


def test_load_of_a_missing_drive_names_it(state_root: Path) -> None:
    with pytest.raises(DriveError, match="no drive named ghost"):
        state.load("ghost")


def test_next_id_is_monotonic_past_gaps() -> None:
    assert state.next_id({}, "o") == "o1"
    assert state.next_id({"o1": {}, "o2": {}, "o10": {}}, "o") == "o11"
    assert state.next_id({"j4": {}}, "j") == "j5"


def test_resolve_prefers_flag_then_env(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("alpha")
    make("beta")
    monkeypatch.setenv("LONG_RUNNING_DRIVE", "beta")
    assert state.resolve("alpha") == "alpha"
    assert state.resolve() == "beta"


def test_resolve_rejects_an_unknown_named_drive(state_root: Path) -> None:
    make("alpha")
    with pytest.raises(DriveError, match="no drive named ghost"):
        state.resolve("ghost")


def test_resolve_finds_the_newest_drive_bound_to_this_session(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make("old", "me", "2026-11-08T10:00:00-08:00")
    make("new", "me", "2026-11-09T10:00:00-08:00")
    make("other", "someone-else")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "me")
    assert state.resolve() == "new"


def test_resolve_falls_back_to_the_only_drive(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make("solo", "someone-else")
    assert state.resolve() == "solo"


def test_resolve_lists_drives_when_ambiguous(state_root: Path) -> None:
    make("alpha")
    make("beta")
    with pytest.raises(DriveError, match="alpha, beta"):
        state.resolve()


def test_resolve_with_no_drives_says_how_to_start(state_root: Path) -> None:
    with pytest.raises(DriveError, match="drive start"):
        state.resolve()


def test_events_append_and_read_from_an_offset(state_root: Path) -> None:
    make("demo")
    state.append_event("demo", "tap", label="Approve", pr="o/n#1")
    middle = state.events_end("demo")
    state.append_event("demo", "shown", pr="o/n#2")
    everything = list(state.read_events("demo"))
    assert [event["kind"] for event, _ in everything] == ["tap", "shown"]
    assert everything[0][0]["label"] == "Approve"
    assert everything[0][1] == middle
    assert everything[1][1] == state.events_end("demo")
    assert [event["kind"] for event, _ in state.read_events("demo", middle)] == ["shown"]


def test_read_events_stops_before_a_partial_line(state_root: Path) -> None:
    make("demo")
    state.append_event("demo", "tap")
    complete = state.events_end("demo")
    with open(state.events_path("demo"), "a") as handle:
        handle.write('{"kind": "half')
    read = list(state.read_events("demo"))
    assert len(read) == 1
    assert read[0][1] == complete


def test_read_events_without_a_log_yields_nothing(state_root: Path) -> None:
    make("demo")
    assert list(state.read_events("demo")) == []
    assert state.events_end("demo") == 0


def test_parse_due_clock_later_today() -> None:
    assert state.parse_due("16:30", REFERENCE) == REFERENCE.replace(hour=16, minute=30)


def test_parse_due_clock_already_passed_is_tomorrow() -> None:
    assert state.parse_due("9:15", REFERENCE) == REFERENCE.replace(hour=9, minute=15) + timedelta(days=1)


def test_parse_due_offsets() -> None:
    assert state.parse_due("+30m", REFERENCE) == REFERENCE + timedelta(minutes=30)
    assert state.parse_due("+2h", REFERENCE) == REFERENCE + timedelta(hours=2)
    assert state.parse_due("+1d", REFERENCE) == REFERENCE + timedelta(days=1)


def test_parse_due_iso_keeps_its_offset() -> None:
    due = state.parse_due("2026-11-10T09:30:00-08:00", REFERENCE)
    assert due.isoformat() == "2026-11-10T09:30:00-08:00"


def test_parse_due_naive_iso_is_local() -> None:
    assert state.parse_due("2026-11-10T09:30", REFERENCE).tzinfo is not None


def test_parse_due_rejects_nonsense() -> None:
    with pytest.raises(DriveError, match="HH:MM"):
        state.parse_due("soon", REFERENCE)


@pytest.mark.parametrize(
    "ref",
    [
        "https://github.com/Forge-AI/monorepo/pull/123",
        "https://github.com/Forge-AI/monorepo/pull/123/files",
        "github.com/Forge-AI/monorepo/pull/123",
        "Forge-AI/monorepo#123",
    ],
)
def test_pr_key(ref: str) -> None:
    assert state.pr_key(ref) == "Forge-AI/monorepo#123"


def test_pr_key_rejects_non_pr_urls() -> None:
    with pytest.raises(DriveError):
        state.pr_key("https://github.com/Forge-AI/monorepo/issues/123")


def test_pr_url_round_trips() -> None:
    assert state.pr_url("yasyf/cc-skills#9") == "https://github.com/yasyf/cc-skills/pull/9"
