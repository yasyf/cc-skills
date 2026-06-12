"""Flattery gates: thresholds come from the meta comment and are re-judged
against fresh data every run — a stat renders only when it flatters."""

from __future__ import annotations

import json

import update_profile


def _copy(dossier: dict) -> dict:
    return json.loads(json.dumps(dossier))


def _line_for(rendered: str, name: str) -> str:
    return next(line for line in rendered.splitlines() if f"[{name}](" in line)


def test_thresholds_from_meta_override_defaults():
    gates = update_profile.gates_from_meta({"min_stars_badge": 100, "intensity": "max"})
    assert gates["min_stars_badge"] == 100
    assert gates["min_contributions"] == 750
    assert gates["shipped_window_months"] == 6


def test_missing_meta_means_defaults():
    assert update_profile.gates_from_meta(None) == update_profile.DEFAULT_GATES


def test_star_counts_across_the_30_star_line(dossier, now):
    gates = update_profile.gates_from_meta(None)
    rendered = update_profile.render_featured(dossier, gates, now)

    assert "⭐ 128" in _line_for(rendered, "rocket")
    assert "⭐ 42" in _line_for(rendered, "nebula")
    comet = _line_for(rendered, "comet")  # 29 stars: just under
    assert "⭐" not in comet
    assert "29" not in comet  # below the gate, no numbers anywhere

    # The day comet crosses the line, the count appears with no other change.
    bumped = _copy(dossier)
    for repo in bumped["repos"]:
        if repo["name"] == "comet":
            repo["stars"] = 30
    assert "⭐ 30" in _line_for(update_profile.render_featured(bumped, gates, now), "comet")


def test_meta_threshold_moves_the_star_line(dossier, now):
    gates = update_profile.gates_from_meta({"min_stars_badge": 29})
    rendered = update_profile.render_featured(dossier, gates, now)
    assert "⭐ 29" in _line_for(rendered, "comet")


def test_contributions_hidden_below_threshold(dossier, now):
    gates = update_profile.gates_from_meta(None)
    rendered = update_profile.render_activity(dossier, gates, now)
    assert "982 contributions in the last year" in rendered

    shy = _copy(dossier)
    shy["contributions"]["total_last_year"] = 600
    hidden = update_profile.render_activity(shy, gates, now)
    assert "contributions" not in hidden
    assert "600" not in hidden


def test_meta_threshold_hides_contributions(dossier, now):
    gates = update_profile.gates_from_meta({"min_contributions": 2000})
    assert "contributions" not in update_profile.render_activity(dossier, gates, now)


def test_shipped_respects_window(dossier, now):
    gates = update_profile.gates_from_meta(None)
    rendered = update_profile.render_shipped(dossier, gates, now)
    assert "rocket v2.1.0" in rendered
    assert "comet v1.0.0" in rendered
    assert "nebula" not in rendered  # released 2025-09-15: outside 6 months


def test_shipped_window_from_meta(dossier, now):
    gates = update_profile.gates_from_meta({"shipped_window_months": 1})
    rendered = update_profile.render_shipped(dossier, gates, now)
    assert "rocket" in rendered
    assert "comet" not in rendered


def test_empty_shipped_renders_empty_interior(dossier, now):
    gates = update_profile.gates_from_meta(None)
    bare = _copy(dossier)
    bare["releases"] = []
    assert update_profile.render_shipped(bare, gates, now) == ""
