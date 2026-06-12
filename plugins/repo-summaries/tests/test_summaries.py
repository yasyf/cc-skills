"""The summaries module: loading, the whole-file staleness gate, key matching,
sanitizing, and the stale_days knob. The sidecar is Claude-maintained and
consumer scripts only read it — a missing or broken file must yield empty
summaries, never an exception. Consumer-side rendering (how a summary lands on
a line) is each consumer's own test surface; gh-profile's lives in its plugin."""

from __future__ import annotations

from datetime import datetime, timezone

import summaries

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
FRESH = "2026-05-31T00:00:00Z"

ROCKET_KEY = "PushEvent:octocat/rocket"
ROCKET_PUSH = {ROCKET_KEY: {"as_of": "aaaa1111bbbb", "summary": "built the orbital insertion burn planner"}}


def _sidecar(generated_at: str = FRESH, activity: dict | None = None, shipped: dict | None = None) -> dict:
    return {"version": 1, "generated_at": generated_at, "activity": activity or {}, "shipped": shipped or {}}


def test_load_summaries_missing_file_is_silent_empty(tmp_path, capsys):
    assert summaries.load_summaries(tmp_path / "absent.json") == {}
    assert capsys.readouterr().err == ""


def test_load_summaries_malformed_warns_and_returns_empty(tmp_path, capsys):
    sidecar = tmp_path / "summaries.json"
    for garbage in ("{not json", "[1, 2]", '"a string"'):
        sidecar.write_text(garbage)
        assert summaries.load_summaries(sidecar) == {}
        assert "WARN" in capsys.readouterr().err


def test_summary_for_matches_group_and_key_exactly(tmp_path):
    sidecar = _sidecar(activity=ROCKET_PUSH)
    assert summaries.summary_for(sidecar, "activity", ROCKET_KEY, NOW) == "built the orbital insertion burn planner"
    assert summaries.summary_for(sidecar, "activity", "PushEvent:octocat/nebula", NOW) == ""
    assert summaries.summary_for(sidecar, "shipped", ROCKET_KEY, NOW) == ""  # same key, wrong group
    assert summaries.summary_for(None, "activity", ROCKET_KEY, NOW) == ""
    assert summaries.summary_for({}, "activity", ROCKET_KEY, NOW) == ""


def test_staleness_gate_flips_at_ten_days():
    on_the_line = _sidecar(generated_at="2026-05-22T00:00:00Z", activity=ROCKET_PUSH)
    assert summaries.summaries_fresh(on_the_line, NOW)
    assert "burn planner" in summaries.summary_for(on_the_line, "activity", ROCKET_KEY, NOW)
    past_it = _sidecar(generated_at="2026-05-21T23:59:59Z", activity=ROCKET_PUSH)
    assert not summaries.summaries_fresh(past_it, NOW)
    assert summaries.summary_for(past_it, "activity", ROCKET_KEY, NOW) == ""
    unstamped = dict(_sidecar(activity=ROCKET_PUSH))
    del unstamped["generated_at"]
    assert not summaries.summaries_fresh(unstamped, NOW)
    assert summaries.summary_for(unstamped, "activity", ROCKET_KEY, NOW) == ""


def test_stale_days_parameter_overrides_the_default():
    sidecar = _sidecar(generated_at="2026-05-22T00:00:00Z", activity=ROCKET_PUSH)  # 10 days before NOW
    assert not summaries.summaries_fresh(sidecar, NOW, stale_days=3)
    assert summaries.summary_for(sidecar, "activity", ROCKET_KEY, NOW, stale_days=3) == ""
    assert summaries.summaries_fresh(sidecar, NOW, stale_days=30)
    assert "burn planner" in summaries.summary_for(sidecar, "activity", ROCKET_KEY, NOW, stale_days=30)


def test_naive_generated_at_degrades_instead_of_crashing():
    # An LLM-written sidecar may drop the Z; naive stamps count as UTC.
    assert summaries.summaries_fresh(_sidecar(generated_at="2026-05-31T00:00:00"), NOW)
    assert not summaries.summaries_fresh(_sidecar(generated_at="2026-01-01"), NOW)


def test_far_future_generated_at_is_broken_not_immortal():
    assert not summaries.summaries_fresh(_sidecar(generated_at="2027-06-01T00:00:00Z"), NOW)
    # ...but ordinary clock skew (same day) stays fresh.
    assert summaries.summaries_fresh(_sidecar(generated_at="2026-06-01T00:30:00Z"), NOW)


def test_clean_summary_neutralizes_hostile_or_sloppy_values():
    assert summaries.clean_summary("  shipped   the\tthing  ") == "shipped the thing"
    assert summaries.clean_summary("first line\nsecond line") == "first line"
    assert summaries.clean_summary("evil --> breakout") == ""
    assert summaries.clean_summary("<!-- sneaky") == ""
    assert summaries.clean_summary("x" * 300) == "x" * summaries.SUMMARY_MAX_LEN
    assert summaries.clean_summary(42) == ""
    assert summaries.clean_summary("   ") == ""


def test_non_dict_entries_are_ignored():
    sidecar = _sidecar(activity={ROCKET_KEY: "a bare string, not an entry"})
    assert summaries.summary_for(sidecar, "activity", ROCKET_KEY, NOW) == ""
    sidecar = _sidecar()
    sidecar["activity"] = ["not", "a", "dict"]
    assert summaries.summary_for(sidecar, "activity", ROCKET_KEY, NOW) == ""  # must not raise


def test_parse_iso_tolerates_z_suffix_and_garbage():
    parsed = summaries.parse_iso("2026-05-31T00:00:00Z")
    assert parsed == datetime(2026, 5, 31, tzinfo=timezone.utc)
    assert summaries.parse_iso("not a date") is None
    assert summaries.parse_iso("") is None
    assert summaries.parse_iso(None) is None
