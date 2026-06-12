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


def test_push_head_hint_comes_from_the_newest_event(dossier):
    rocket = next(
        event
        for event in dossier["recent_events"]
        if event["type"] == "PushEvent" and event["repo"] == "octocat/rocket"
    )
    assert rocket["head"] == "aaaa1111bbbb"  # 12 chars, from the dedup winner
    assert "commits" not in rocket  # the live events API stopped shipping these


def test_title_hints_ride_along_when_payloads_carry_them(dossier):
    by_key = {(event["type"], event["repo"]): event for event in dossier["recent_events"]}
    assert by_key[("PullRequestEvent", "octocat/rocket")]["title"] == "Add orbital insertion burn"
    assert by_key[("IssuesEvent", "octocat/comet")]["title"] == "Fuel gauge drift"
    assert by_key[("ReleaseEvent", "octocat/rocket")]["title"] == "Orbital insertion"


def test_no_hints_for_payload_less_or_uninformative_events(dossier):
    by_key = {(event["type"], event["repo"]): event for event in dossier["recent_events"]}
    for key in (("WatchEvent", "someorg/widgets"), ("PushEvent", "octocat/dust")):
        assert "head" not in by_key[key]
        assert "title" not in by_key[key]


def test_release_bodies_harvested_and_truncated(dossier):
    by_repo = {release["repo"]: release for release in dossier["releases"]}
    assert by_repo["rocket"]["body"].startswith("**Full Changelog**")
    assert len(by_repo["comet"]["body"]) == 600  # fixture body is longer; harvest caps it
    assert by_repo["nebula"]["body"] == ""  # no body on the release
