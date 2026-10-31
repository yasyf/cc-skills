"""The config file holds configuration only; a worker named there is refused by name."""

from __future__ import annotations

import json

import pytest
from lr import config

GOOD = {"repo": "org/repo", "drive": "d", "checkout": "/srv/checkout", "author": "drive-bot", "label": "drive", "movers": ["lanes"]}


def write(tmp_path, raw):
    path = tmp_path / "runner.json"
    path.write_text(json.dumps(raw))
    return path


def test_configuration_loads(tmp_path):
    loaded = config.load(write(tmp_path, {**GOOD, "remote_box": "box-1"}))

    assert loaded == config.Config("org/repo", "d", "/srv/checkout", "drive-bot", "drive", ("lanes",), "", "box-1")


def test_six_desks_listed_in_the_config_are_refused_by_name(tmp_path):
    with pytest.raises(config.BadConfig, match=r"desks, workers are not configuration; workers and desks register themselves with their first heartbeat"):
        config.load(write(tmp_path, {**GOOD, "desks": [f"finder-{i}" for i in range(6)], "workers": {}}))


def test_a_missing_field_is_named(tmp_path):
    with pytest.raises(config.BadConfig, match="missing author, label, movers"):
        config.load(write(tmp_path, {"repo": "org/repo", "drive": "d", "checkout": "/x"}))


def test_a_blank_required_field_is_refused(tmp_path):
    with pytest.raises(config.BadConfig, match="repo must be non-empty text"):
        config.load(write(tmp_path, {**GOOD, "repo": " "}))


def test_the_remote_box_is_optional_until_the_pool_runs_remotely(tmp_path):
    assert config.load(write(tmp_path, GOOD)).remote_box == ""


@pytest.mark.parametrize("movers", [[], ["lanes", "board"], "lanes"])
def test_movers_name_only_known_movers_and_have_no_default(tmp_path, movers):
    with pytest.raises(config.BadConfig, match="movers must be a non-empty list drawn from lanes, landing, grading"):
        config.load(write(tmp_path, {**GOOD, "movers": movers}))


def test_a_config_without_movers_runs_nothing_by_default(tmp_path):
    with pytest.raises(config.BadConfig, match="missing movers"):
        config.load(write(tmp_path, {key: value for key, value in GOOD.items() if key != "movers"}))
