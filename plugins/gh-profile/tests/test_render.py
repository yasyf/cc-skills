"""profile.py render: CRON_MINUTE substitution, leftover-token scan, --with
gating, PROFILE_GUIDE.md placement, and WROTE/SKIP/CONFLICT semantics."""

from __future__ import annotations

import re

CORE = (
    ".github/scripts/update_profile.py",
    ".github/workflows/profile-snake.yml",
    ".github/workflows/profile-refresh.yml",
)
METRICS = (".github/workflows/profile-metrics.yml",)
CLAUDE = (".github/workflows/profile-claude-refresh.yml", "PROFILE_GUIDE.md")


def test_render_default_places_core_files_only(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path)]) == 0
    for dest in CORE:
        assert (tmp_path / dest).exists(), dest
    for dest in METRICS + CLAUDE:
        assert not (tmp_path / dest).exists(), dest


def test_with_flags_add_exactly_the_right_files(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path), "--with", "metrics"]) == 0
    for dest in CORE + METRICS:
        assert (tmp_path / dest).exists(), dest
    for dest in CLAUDE:
        assert not (tmp_path / dest).exists(), dest

    claude_dir = tmp_path / "claude"
    assert profile_mod.main(["render", "--target", str(claude_dir), "--with", "metrics,claude"]) == 0
    for dest in CORE + METRICS + CLAUDE:
        assert (claude_dir / dest).exists(), dest


def test_profile_guide_lands_at_target_root_only(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path), "--with", "claude"]) == 0
    assert (tmp_path / "PROFILE_GUIDE.md").exists()
    assert not (tmp_path / ".github" / "PROFILE_GUIDE.md").exists()


def test_unknown_with_flag_is_rejected(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path), "--with", "ponies"]) == 2
    assert not (tmp_path / ".github").exists()


def test_cron_minute_substituted_with_int_0_to_59(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path), "--with", "metrics,claude"]) == 0
    workflows = list((tmp_path / ".github" / "workflows").glob("*.yml"))
    assert len(workflows) == 4
    for workflow in workflows:
        text = workflow.read_text()
        assert "{{CRON_MINUTE}}" not in text, workflow.name
        match = re.search(r'cron: "(\d{1,2}) ', text)
        assert match, workflow.name
        assert 0 <= int(match.group(1)) <= 59


def test_no_leftover_tokens_in_rendered_output(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path), "--with", "metrics,claude"]) == 0
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert profile_mod.find_unrendered(path.read_text()) == [], path


def test_leftover_scan_catches_stray_but_not_actions_syntax(profile_mod):
    assert profile_mod.find_unrendered("before {{STRAY_TOKEN}} after") == ["{{STRAY_TOKEN}}"]
    assert profile_mod.find_unrendered("${{ github.repository_owner }}") == []
    assert profile_mod.find_unrendered("${{ secrets.GITHUB_TOKEN }}") == []
    assert profile_mod.find_unrendered("group: ci-${{ github.ref }}") == []


def test_rendered_updater_matches_template_bytes(profile_mod, tmp_path):
    assert profile_mod.main(["render", "--target", str(tmp_path)]) == 0
    template = profile_mod.TEMPLATES / "scripts" / "update_profile.py"
    rendered = tmp_path / ".github" / "scripts" / "update_profile.py"
    assert rendered.read_text() == template.read_text()


def test_skip_conflict_force_semantics(profile_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(profile_mod, "_random_minute", lambda: 7)
    assert profile_mod.main(["render", "--target", str(tmp_path)]) == 0
    assert profile_mod.main(["render", "--target", str(tmp_path)]) == 0  # identical -> SKIP

    monkeypatch.setattr(profile_mod, "_random_minute", lambda: 8)
    assert profile_mod.main(["render", "--target", str(tmp_path)]) == 1  # CONFLICT, nothing written
    snake = tmp_path / ".github" / "workflows" / "profile-snake.yml"
    assert 'cron: "7 ' in snake.read_text()

    assert profile_mod.main(["render", "--target", str(tmp_path), "--force"]) == 0
    assert 'cron: "8 ' in snake.read_text()
