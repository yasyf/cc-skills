"""Marker splicing: idempotence, NOMARKER semantics, regex-metacharacter
safety, meta round-trip, and --check exit codes."""

from __future__ import annotations

import update_profile


def test_update_twice_is_byte_identical(dossier, now, make_readme):
    text = make_readme()
    once, nomarker = update_profile.update_readme_text(text, dossier, now)
    assert nomarker == []
    assert once != text
    twice, nomarker_again = update_profile.update_readme_text(once, dossier, now)
    assert nomarker_again == []
    assert twice == once


def test_nomarker_leaves_file_untouched(dossier, now, make_readme):
    text = make_readme(ids=("featured", "activity", "languages"))
    new_text, nomarker = update_profile.update_readme_text(text, dossier, now, sections=("shipped",))
    assert nomarker == ["shipped"]
    assert new_text == text


def test_prose_outside_markers_is_byte_preserved(dossier, now, make_readme):
    text = make_readme()
    new_text, _ = update_profile.update_readme_text(text, dossier, now)
    assert "# Hi, I'm Octo ($1 \\d+ regex bait)" in new_text
    assert new_text.endswith("Footer prose stays byte-identical.\n")


def test_splice_is_safe_for_regex_metacharacters_and_dollars():
    text = "<!-- gh-profile:start:featured -->\nold\n<!-- gh-profile:end:featured -->\n"
    content = "price $1.00 \\1 \\g<0> (.*) [a-z]+ $& ${name} C:\\path"
    spliced = update_profile.splice_section(text, "featured", content)
    assert content in spliced
    assert update_profile.splice_section(spliced, "featured", content) == spliced


def test_splice_empty_content_collapses_interior():
    text = "<!-- gh-profile:start:shipped -->\nold stuff\n<!-- gh-profile:end:shipped -->\n"
    assert (
        update_profile.splice_section(text, "shipped", "")
        == "<!-- gh-profile:start:shipped -->\n<!-- gh-profile:end:shipped -->\n"
    )


def test_meta_round_trip(dossier, now, make_readme):
    text = make_readme()
    before = update_profile.parse_meta(text)
    new_text, _ = update_profile.update_readme_text(text, dossier, now)
    after = update_profile.parse_meta(new_text)
    for key in ("min_stars_badge", "min_contributions", "shipped_window_months", "intensity", "version"):
        assert after[key] == before[key]
    assert after["last_refresh"] == "2026-06-01T00:00:00Z"


def test_meta_only_bumps_when_content_changes(dossier, now, make_readme):
    text = make_readme()
    once, _ = update_profile.update_readme_text(text, dossier, now)
    twice, _ = update_profile.update_readme_text(once, dossier, now)
    assert update_profile.parse_meta(twice) == update_profile.parse_meta(once)


def test_check_semantics(dossier, now, make_readme, tmp_path, monkeypatch, fake_gh):
    monkeypatch.setattr(update_profile, "_now", lambda: now)
    monkeypatch.setenv("GITHUB_REPOSITORY", "octocat/octocat")
    readme = tmp_path / "README.md"
    readme.write_text(make_readme())

    # Stale README: --check reports dirty and writes nothing.
    assert update_profile.main(["update", "--readme", str(readme), "--check"]) == 1
    assert readme.read_text() == make_readme()

    # A real update writes; an immediate --check is then clean.
    assert update_profile.main(["update", "--readme", str(readme)]) == 0
    refreshed = readme.read_text()
    assert refreshed != make_readme()
    assert update_profile.main(["update", "--readme", str(readme), "--check"]) == 0
    assert readme.read_text() == refreshed


def test_cli_prints_nomarker_and_touches_nothing(dossier, now, make_readme, tmp_path, monkeypatch, fake_gh, capsys):
    monkeypatch.setattr(update_profile, "_now", lambda: now)
    monkeypatch.setenv("GITHUB_REPOSITORY", "octocat/octocat")
    readme = tmp_path / "README.md"
    foreign = "# Hand-written README\n\nNo markers anywhere.\n"
    readme.write_text(foreign)

    assert update_profile.main(["update", "--readme", str(readme)]) == 0
    out = capsys.readouterr().out
    for section_id in ("featured", "shipped", "activity", "languages"):
        assert f"NOMARKER {section_id}" in out
    assert readme.read_text() == foreign


def test_unknown_section_is_rejected(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("x\n")
    assert update_profile.main(["update", "--readme", str(readme), "--sections", "bogus"]) == 2
