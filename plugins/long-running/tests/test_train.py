from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import state, train

T0 = datetime(2026, 11, 15, 9, 0).astimezone()


@pytest.fixture
def detached(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], str]]:
    started: list[tuple[str, list[str], str]] = []
    monkeypatch.setattr(state, "detach", lambda name, args, log: started.append((name, args, log)) or 4242)
    monkeypatch.setattr(state, "now", lambda: T0)
    return started


def test_the_train_leaves_at_once_when_it_never_left_carrying_the_newest_sha_of_each_plugin_and_every_merge_boarded_with_it(detached: list[tuple[str, list[str], str]]) -> None:
    train.board("long-running@skills", "yasyf/cc-skills", "yasyf/cc-skills#1", "aaa111")
    train.board("long-running@skills", "yasyf/cc-skills", "yasyf/cc-skills#2", "bbb222")
    assert train.depart("demo", T0) == ["the install train left with long-running@skills at bbb222, carrying yasyf/cc-skills#1, yasyf/cc-skills#2"]
    assert detached == [("demo", ["install", "long-running@skills", "--sha", "bbb222", "--repo", "yasyf/cc-skills"], train.LOG)]
    board = train.read()
    assert (board["boarded"], board["left"], board["carried"]["long-running@skills"]["pid"]) == ({}, state.stamp(T0), 4242)


def test_the_train_leaves_at_most_every_thirty_minutes_and_a_second_watch_finding_it_gone_sends_none(detached: list[tuple[str, list[str], str]]) -> None:
    train.board("long-running@skills", "yasyf/cc-skills", "yasyf/cc-skills#1", "aaa111")
    train.depart("demo", T0)
    train.board("cc-inbox@skills", "yasyf/cc-skills", "yasyf/cc-skills#3", "ccc333")
    assert train.depart("other", T0 + timedelta(minutes=29)) == []
    assert train.depart("other", T0 + train.EVERY) == ["the install train left with cc-inbox@skills at ccc333, carrying yasyf/cc-skills#3"]
    assert train.depart("demo", T0 + train.EVERY) == []
    assert [args[1] for _, args, _ in detached] == ["long-running@skills", "cc-inbox@skills"]


def test_the_train_holds_a_merge_while_boarded_or_while_its_install_runs(detached: list[tuple[str, list[str], str]], monkeypatch: pytest.MonkeyPatch) -> None:
    merges = {"yasyf/cc-skills#1": "aaa111"}
    train.board("long-running@skills", "yasyf/cc-skills", "yasyf/cc-skills#1", "aaa111")
    assert train.holds(merges) == ["yasyf/cc-skills#1"]
    train.depart("demo", T0)
    monkeypatch.setattr(state, "alive", lambda pid: pid == 4242)
    assert train.holds(merges) == ["yasyf/cc-skills#1"]
    monkeypatch.setattr(state, "alive", lambda pid: False)
    assert train.holds(merges) == []


def test_an_install_by_hand_of_the_boarded_sha_takes_its_plugin_off_the_board_and_one_of_another_sha_leaves_it(detached: list[tuple[str, list[str], str]]) -> None:
    train.board("long-running@skills", "yasyf/cc-skills", "yasyf/cc-skills#1", "aaa111")
    train.alighted("long-running@skills", "fff999")
    assert list(train.read()["boarded"]) == ["long-running@skills"]
    train.alighted("long-running@skills", "aaa111")
    assert train.read()["boarded"] == {}
