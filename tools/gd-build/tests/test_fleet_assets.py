from __future__ import annotations

import importlib.resources

import pytest

from gd_build import fleet_assets


def packaged_css() -> str:
    return importlib.resources.files("gd_build").joinpath("assets/fleet-theme.css").read_text()


def test_materialize_writes_packaged_css(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    dest = fleet_assets.materialize_fleet_css()
    assert dest == fleet_assets.CSS_DEST
    written = tmp_path / "docs/assets/.gd-build/fleet-theme.css"
    assert written.is_file()
    assert written.read_text() == packaged_css()


def test_materialize_overwrites_stale_css(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    stale = tmp_path / "docs/assets/.gd-build/fleet-theme.css"
    stale.parent.mkdir(parents=True)
    stale.write_text("/* stale */\n")
    fleet_assets.materialize_fleet_css()
    assert stale.read_text() == packaged_css()


def test_quarto_config_entries_reference_basename() -> None:
    assert fleet_assets.quarto_config_entries() == {"css": ["fleet-theme.css"]}


def test_packaged_css_sets_fleet_link_tokens() -> None:
    css = packaged_css()
    assert "--fleet-link: var(--gd-accent, #2563eb)" in css
    assert "--fleet-link: var(--gd-accent, #8ea9ff)" in css
