"""Repo scoring, exclusions with reasons, ordering, and the cap."""

from __future__ import annotations

import json

import update_profile


def test_score_rewards_recent_push(now):
    assert update_profile.score_repo(100, "2026-06-01T00:00:00Z", now) == 125.0
    assert update_profile.score_repo(0, "2026-03-03T00:00:00Z", now) == 12.5  # 90 days: half the bonus
    assert update_profile.score_repo(100, "2025-06-01T00:00:00Z", now) == 100.0  # bonus floors at 0


def test_score_tolerates_garbage_timestamp(now):
    assert update_profile.score_repo(7, "", now) == 7.0


def test_exclusions_carry_reasons(dossier):
    reasons = {entry["name"]: entry["reason"] for entry in dossier["excluded"]}
    assert reasons == {
        "forked-lib": "fork",
        "old-archive": "archived",
        "no-desc": "no description",
    }


def test_included_sorted_by_score_desc(dossier):
    names = [repo["name"] for repo in dossier["repos"]]
    assert names == ["rocket", "nebula", "comet", "dust", "tools", "zine"]
    assert all(not repo["archived"] for repo in dossier["repos"])
    scores = [repo["score"] for repo in dossier["repos"]]
    assert scores == sorted(scores, reverse=True)


def test_repo_cap_is_50(now):
    raw = [
        {
            "name": f"repo{i:02d}",
            "full_name": f"octocat/repo{i:02d}",
            "description": "filler",
            "html_url": f"https://github.com/octocat/repo{i:02d}",
            "stargazers_count": i,
            "forks_count": 0,
            "language": "Python",
            "topics": [],
            "pushed_at": "2026-05-01T00:00:00Z",
            "archived": False,
            "fork": False,
        }
        for i in range(60)
    ]
    included, excluded = update_profile.shape_repos(raw, now)
    assert len(included) == 50
    assert excluded == []


def test_harvest_cli_writes_dossier(fake_gh, now, tmp_path, monkeypatch):
    monkeypatch.setattr(update_profile, "_now", lambda: now)
    out = tmp_path / "dossier.json"
    assert update_profile.main(["harvest", "--login", "octocat", "--out", str(out)]) == 0
    dossier = json.loads(out.read_text())
    assert set(dossier) == {
        "generated_at",
        "user",
        "pinned",
        "repos",
        "languages",
        "recent_events",
        "releases",
        "contributions",
        "excluded",
    }
    assert dossier["user"]["login"] == "octocat"
    assert dossier["contributions"]["total_last_year"] == 982
    assert [p["name"] for p in dossier["pinned"]] == ["rocket", "nebula"]
    assert [r["repo"] for r in dossier["releases"]] == ["rocket", "comet", "nebula"]
