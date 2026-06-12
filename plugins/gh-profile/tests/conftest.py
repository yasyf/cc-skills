"""Shared fixtures: a monkeypatched gh boundary + a canonical fixture dossier.

``update_profile`` imports straight off pyproject's pythonpath
(skills/gh-profile/templates/github/scripts) — the exact file that gets
committed into profile repos. Zero network: every gh call resolves to
tests/fixtures/*.json, or raises GhError exactly like a live 404 would.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import update_profile

PLUGIN = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
LOGIN = "octocat"
USER = {
    "login": "octocat",
    "name": "Octo Cat",
    "bio": "Builds small sharp tools.",
    "followers": 12,
    "company": None,
    "blog": "https://octo.example",
    "location": "Internet",
}

META_LINE = (
    '<!-- gh-profile:meta {"intensity": "fancy", "last_refresh": "2026-05-01T00:00:00Z", '
    '"min_contributions": 750, "min_stars_badge": 30, "shipped_window_months": 6, '
    '"version": "0.1.0"} -->'
)

ALL_IDS = ("featured", "shipped", "activity", "languages")


def _make_readme(ids: tuple[str, ...] = ALL_IDS, meta: str = META_LINE) -> str:
    parts = [meta, "", "# Hi, I'm Octo ($1 \\d+ regex bait)", ""]
    for section_id in ids:
        parts += [
            f"## {section_id.title()}",
            f"<!-- gh-profile:start:{section_id} -->",
            "stale",
            f"<!-- gh-profile:end:{section_id} -->",
            "",
        ]
    parts.append("Footer prose stays byte-identical.")
    return "\n".join(parts) + "\n"


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def make_readme():
    return _make_readme


@pytest.fixture
def fake_gh(monkeypatch):
    """Route update_profile._gh to fixture payloads; record every call."""
    releases = json.loads((FIXTURES / "releases.json").read_text())
    calls: list[list[str]] = []

    def gh(args: list[str]) -> str:
        calls.append(list(args))
        endpoint = args[1] if len(args) > 1 and args[0] == "api" else ""
        path = endpoint.split("?", 1)[0]
        if path == "graphql":
            return (FIXTURES / "graphql.json").read_text()
        if path == "user":
            return f"{LOGIN}\n"
        if path == f"users/{LOGIN}":
            return json.dumps(USER)
        if path == f"users/{LOGIN}/repos":
            return (FIXTURES / "repos.json").read_text()
        if path == f"users/{LOGIN}/events":
            return (FIXTURES / "events.json").read_text()
        if path.startswith(f"repos/{LOGIN}/") and path.endswith("/releases/latest"):
            repo = path.split("/")[2]
            if repo in releases:
                return json.dumps(releases[repo])
            raise update_profile.GhError(args, 1, "HTTP 404: Not Found")
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(update_profile, "_gh", gh)
    gh.calls = calls
    return gh


@pytest.fixture
def dossier(fake_gh, now) -> dict:
    return update_profile.harvest_dossier(LOGIN, now)


@pytest.fixture(scope="session")
def profile_mod():
    """Load scripts/profile.py by path (avoids shadowing stdlib `profile`)."""
    path = PLUGIN / "skills" / "gh-profile" / "scripts" / "profile.py"
    spec = importlib.util.spec_from_file_location("gh_profile_cli", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module
