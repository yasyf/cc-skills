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


def test_keys_and_movers_a_newer_release_wrote_load_with_a_warning_and_configure_nothing(tmp_path, capsys):
    loaded = config.load(write(tmp_path, {**GOOD, "movers": ["lanes", "board"], "desks": [f"finder-{i}" for i in range(6)], "go_lanes_v9": "x"}))

    assert loaded == config.load(write(tmp_path, GOOD))
    err = capsys.readouterr().err
    assert "ignoring desks, go_lanes_v9" in err and "ignoring movers board" in err


def test_a_missing_field_is_named(tmp_path):
    with pytest.raises(config.BadConfig, match="missing author, label, movers"):
        config.load(write(tmp_path, {"repo": "org/repo", "drive": "d", "checkout": "/x"}))


def test_a_blank_required_field_is_refused(tmp_path):
    with pytest.raises(config.BadConfig, match="repo must be non-empty text"):
        config.load(write(tmp_path, {**GOOD, "repo": " "}))


def test_the_remote_box_is_optional_until_the_pool_runs_remotely(tmp_path):
    assert config.load(write(tmp_path, GOOD)).remote_box == ""


@pytest.mark.parametrize(("movers", "says"), [([], "movers must name at least one of lanes, shadow, landing, grading, items"), (["board"], "movers must name at least one of"), ("lanes", "movers must be a list of mover names")])
def test_movers_must_name_a_mover_this_release_runs(tmp_path, movers, says):
    with pytest.raises(config.BadConfig, match=says):
        config.load(write(tmp_path, {**GOOD, "movers": movers}))


def test_the_landing_mover_needs_the_queue_script(tmp_path):
    with pytest.raises(config.BadConfig, match="landing mover needs queue_script"):
        config.load(write(tmp_path, {**GOOD, "movers": ["lanes", "landing"]}))


def test_a_config_without_movers_runs_nothing_by_default(tmp_path):
    with pytest.raises(config.BadConfig, match="missing movers"):
        config.load(write(tmp_path, {key: value for key, value in GOOD.items() if key != "movers"}))


def test_go_lanes_name_the_lanes_besides_root_whose_go_may_name_an_exempt_class(tmp_path):
    assert config.load(write(tmp_path, {**GOOD, "go_lanes": "go-carry-sweep, go-sweep"})).go_lane_names == ("go-carry-sweep", "go-sweep")
