"""Events digest: dedup per (type, repo), 30-day window, cap 12, newest first."""

from __future__ import annotations


def test_dedup_keeps_newest_per_type_repo_pair(dossier):
    rocket_pushes = [
        event
        for event in dossier["recent_events"]
        if event["type"] == "PushEvent" and event["repo"] == "octocat/rocket"
    ]
    assert len(rocket_pushes) == 1
    assert rocket_pushes[0]["created_at"] == "2026-05-31T12:00:00Z"


def test_30_day_window(dossier):
    # NOW is 2026-06-01; nothing before 2026-05-02 survives.
    assert all(event["created_at"] >= "2026-05-02" for event in dossier["recent_events"])
    assert not any(event["repo"] == "octocat/old" for event in dossier["recent_events"])


def test_cap_12(dossier):
    # The fixture holds 15 unique in-window (type, repo) pairs.
    assert len(dossier["recent_events"]) == 12


def test_newest_first(dossier):
    stamps = [event["created_at"] for event in dossier["recent_events"]]
    assert stamps == sorted(stamps, reverse=True)


def test_cap_drops_the_oldest_pairs(dossier):
    pairs = {(event["type"], event["repo"]) for event in dossier["recent_events"]}
    assert ("PushEvent", "someorg/widgets") not in pairs  # 2026-05-17: oldest unique
    assert ("PushEvent", "octocat/rocket") in pairs
