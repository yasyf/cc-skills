"""Summaries sidecar: loading, the file-level staleness gate, key matching,
sanitizing, shipped precedence, idempotence, and default-path resolution.
The sidecar is Claude-maintained and this script only reads it — a missing or
broken file must render exactly today's plain lines."""

from __future__ import annotations

import json

import update_profile

FRESH = "2026-05-31T00:00:00Z"  # conftest NOW is 2026-06-01

ROCKET_PUSH = {
    "PushEvent:octocat/rocket": {"as_of": "aaaa1111bbbb", "summary": "built the orbital insertion burn planner"}
}
ROCKET_RELEASE = {"rocket@v2.1.0": {"summary": "orbital insertion with mid-course corrections"}}
COMET_RELEASE = {"comet@v1.0.0": {"summary": "fixed fuel gauge drift under sustained thrust"}}


def _sidecar(generated_at: str = FRESH, activity: dict | None = None, shipped: dict | None = None) -> dict:
    return {"version": 1, "generated_at": generated_at, "activity": activity or {}, "shipped": shipped or {}}


def test_no_sidecar_is_byte_identical_to_plain_render(dossier, now, make_readme):
    text = make_readme()
    plain, _ = update_profile.update_readme_text(text, dossier, now)
    with_none, _ = update_profile.update_readme_text(text, dossier, now, summaries=None)
    with_empty, _ = update_profile.update_readme_text(text, dossier, now, summaries={})
    assert plain == with_none == with_empty
    assert "burn planner" not in plain


def test_load_summaries_missing_file_is_silent_empty(tmp_path, capsys):
    assert update_profile.load_summaries(tmp_path / "absent.json") == {}
    assert capsys.readouterr().err == ""


def test_load_summaries_malformed_warns_and_returns_empty(tmp_path, capsys):
    sidecar = tmp_path / "summaries.json"
    for garbage in ("{not json", '[1, 2]', '"a string"'):
        sidecar.write_text(garbage)
        assert update_profile.load_summaries(sidecar) == {}
        assert "WARN" in capsys.readouterr().err


def test_activity_summary_appends_only_on_key_match(dossier, now):
    gates = update_profile.gates_from_meta(None)
    rendered = update_profile.render_activity(dossier, gates, now, _sidecar(activity=ROCKET_PUSH))
    lines = rendered.splitlines()
    rocket = next(line for line in lines if "Pushed to" in line and "octocat/rocket" in line)
    assert rocket.endswith(" — built the orbital insertion burn planner")
    nebula = next(line for line in lines if "Pushed to" in line and "octocat/nebula" in line)
    assert " — " not in nebula
    pr = next(line for line in lines if "pull request" in line)  # same repo, different type
    assert " — " not in pr


def test_staleness_gate_flips_at_ten_days(dossier, now):
    gates = update_profile.gates_from_meta(None)
    on_the_line = update_profile.render_activity(
        dossier, gates, now, _sidecar(generated_at="2026-05-22T00:00:00Z", activity=ROCKET_PUSH)
    )
    assert "burn planner" in on_the_line
    past_it = update_profile.render_activity(
        dossier, gates, now, _sidecar(generated_at="2026-05-21T23:59:59Z", activity=ROCKET_PUSH)
    )
    assert "burn planner" not in past_it
    unstamped = dict(_sidecar(activity=ROCKET_PUSH))
    del unstamped["generated_at"]
    assert "burn planner" not in update_profile.render_activity(dossier, gates, now, unstamped)


def test_naive_generated_at_degrades_instead_of_crashing(dossier, now):
    # An LLM-written sidecar may drop the Z; naive stamps count as UTC.
    gates = update_profile.gates_from_meta(None)
    naive_fresh = update_profile.render_activity(
        dossier, gates, now, _sidecar(generated_at="2026-05-31T00:00:00", activity=ROCKET_PUSH)
    )
    assert "burn planner" in naive_fresh
    naive_stale = update_profile.render_activity(
        dossier, gates, now, _sidecar(generated_at="2026-01-01", activity=ROCKET_PUSH)
    )
    assert "burn planner" not in naive_stale


def test_far_future_generated_at_is_broken_not_immortal(dossier, now):
    gates = update_profile.gates_from_meta(None)
    future = update_profile.render_activity(
        dossier, gates, now, _sidecar(generated_at="2027-06-01T00:00:00Z", activity=ROCKET_PUSH)
    )
    assert "burn planner" not in future
    # ...but ordinary clock skew (same day) stays fresh.
    skewed = update_profile.render_activity(
        dossier, gates, now, _sidecar(generated_at="2026-06-01T00:30:00Z", activity=ROCKET_PUSH)
    )
    assert "burn planner" in skewed


def test_shipped_summary_beats_name_beats_bare_tag(dossier, now):
    gates = update_profile.gates_from_meta(None)
    plain = update_profile.render_shipped(dossier, gates, now)
    rocket_plain = next(line for line in plain.splitlines() if "rocket" in line)
    assert rocket_plain.endswith(" — Orbital insertion")  # name != tag fallback
    comet_plain = next(line for line in plain.splitlines() if "comet" in line)
    assert " — " not in comet_plain  # name == tag: bare line

    enriched = update_profile.render_shipped(
        dossier, gates, now, _sidecar(shipped={**ROCKET_RELEASE, **COMET_RELEASE})
    )
    rocket_line = next(line for line in enriched.splitlines() if "rocket" in line)
    assert rocket_line.endswith(" — orbital insertion with mid-course corrections")
    assert "Orbital insertion —" not in rocket_line  # summary replaces, never stacks
    comet_line = next(line for line in enriched.splitlines() if "comet" in line)
    assert comet_line.endswith(" — fixed fuel gauge drift under sustained thrust")
    assert "nebula" not in enriched  # outside the shipped window either way


def test_clean_summary_neutralizes_hostile_or_sloppy_values():
    assert update_profile._clean_summary("  shipped   the\tthing  ") == "shipped the thing"
    assert update_profile._clean_summary("first line\nsecond line") == "first line"
    assert update_profile._clean_summary("evil --> breakout") == ""
    assert update_profile._clean_summary("<!-- sneaky") == ""
    assert update_profile._clean_summary("x" * 300) == "x" * update_profile.SUMMARY_MAX_LEN
    assert update_profile._clean_summary(42) == ""
    assert update_profile._clean_summary("   ") == ""


def test_non_dict_entries_are_ignored(dossier, now):
    gates = update_profile.gates_from_meta(None)
    sidecar = _sidecar(activity={"PushEvent:octocat/rocket": "a bare string, not an entry"})
    assert " — " not in update_profile.render_activity(dossier, gates, now, sidecar).splitlines()[0]
    sidecar = _sidecar()
    sidecar["activity"] = ["not", "a", "dict"]
    update_profile.render_activity(dossier, gates, now, sidecar)  # must not raise


def test_update_twice_with_sidecar_is_byte_identical(dossier, now, make_readme):
    sidecar = _sidecar(activity=ROCKET_PUSH, shipped=ROCKET_RELEASE)
    once, nomarker = update_profile.update_readme_text(make_readme(), dossier, now, summaries=sidecar)
    assert nomarker == []
    assert "burn planner" in once
    twice, _ = update_profile.update_readme_text(once, dossier, now, summaries=sidecar)
    assert twice == once


def test_cli_reads_sidecar_beside_the_readme_not_the_cwd(dossier, now, make_readme, tmp_path, monkeypatch, fake_gh):
    monkeypatch.setattr(update_profile, "_now", lambda: now)
    monkeypatch.setenv("GITHUB_REPOSITORY", "octocat/octocat")
    readme = tmp_path / "README.md"
    readme.write_text(make_readme())
    sidecar = tmp_path / ".github" / "profile-summaries.json"
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps(_sidecar(activity=ROCKET_PUSH)))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert update_profile.main(["update", "--readme", str(readme)]) == 0
    assert "burn planner" in readme.read_text()
    assert update_profile.main(["update", "--readme", str(readme), "--check"]) == 0

    # Editing the sidecar dirties --check: the sidecar is a render input like any other.
    sidecar.write_text(json.dumps(_sidecar(activity={
        "PushEvent:octocat/rocket": {"as_of": "aaaa1111bbbb", "summary": "rewrote the burn planner"}
    })))
    assert update_profile.main(["update", "--readme", str(readme), "--check"]) == 1
    assert "burn planner" in readme.read_text()  # --check wrote nothing


def test_cli_summaries_flag_overrides_the_default_path(dossier, now, make_readme, tmp_path, monkeypatch, fake_gh):
    monkeypatch.setattr(update_profile, "_now", lambda: now)
    monkeypatch.setenv("GITHUB_REPOSITORY", "octocat/octocat")
    readme = tmp_path / "README.md"
    readme.write_text(make_readme())
    custom = tmp_path / "custom-summaries.json"
    custom.write_text(json.dumps(_sidecar(shipped=ROCKET_RELEASE)))

    assert update_profile.main(["update", "--readme", str(readme), "--summaries", str(custom)]) == 0
    assert "mid-course corrections" in readme.read_text()
