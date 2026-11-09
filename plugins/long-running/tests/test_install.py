import json
from collections.abc import Callable
from pathlib import Path

import pytest
from drive import install

SHA = "abc123def4567890"
REPO = "yasyf/cc-skills"


class FakeGitHub:
    def __init__(self, config: Path, refs: list[str], statuses: dict[str, str]) -> None:
        self.config = config
        self.refs = refs
        self.statuses = statuses
        self.calls: list[list[str]] = []
        self.sleeps: list[float] = []

    def run(self, cmd: list[str]) -> str:
        self.calls.append(cmd)
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("marketplace.json"):
            ref = self.refs.pop(0) if len(self.refs) > 1 else self.refs[0]
            source = {"source": "git-subdir", "url": f"https://github.com/{REPO}", "ref": ref}
            return json.dumps({"name": "skills", "plugins": [{"name": "long-running", "source": source}]})
        if cmd[:2] == ["gh", "api"] and "/compare/" in cmd[2]:
            return self.statuses[cmd[2].rpartition("...")[2]] + "\n"
        if cmd[:3] == ["claude", "plugin", "update"]:
            entry = {"scope": "user", "version": "1.0.3", "lastUpdated": "2026-11-09T10:00:00.000Z"}
            stale = {"scope": "project", "version": "0.7.2119", "lastUpdated": "2026-11-01T10:00:00.000Z"}
            installed = {"version": 2, "plugins": {"long-running@skills": [stale, entry]}}
            (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps(installed))
        return ""

    def claude_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "claude"]


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "claude"
    (config / "plugins").mkdir(parents=True)
    known = {
        "skills": {"source": {"source": "github", "repo": REPO}, "installLocation": str(config / "m")},
        "revdiff": {"source": {"source": "git", "url": "https://github.com/umputun/revdiff.git"}},
        "forge": {"source": {"source": "directory", "path": "/somewhere"}},
    }
    (config / "plugins" / "known_marketplaces.json").write_text(json.dumps(known))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def fake(config: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeGitHub]:
    def make(refs: list[str], statuses: dict[str, str]) -> FakeGitHub:
        github = FakeGitHub(config, refs, statuses)
        monkeypatch.setattr(install, "run", github.run)
        monkeypatch.setattr(install, "sleep", github.sleeps.append)
        return github

    return make


def test_a_tag_that_already_contains_the_sha_updates_and_prints_the_version(
    fake: Callable[..., FakeGitHub], capsys: pytest.CaptureFixture[str]
) -> None:
    github = fake(["long-running--v1.0.3"], {"long-running--v1.0.3": "ahead"})
    code = install.main("long-running@skills", SHA, None, 900)
    assert code == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert github.sleeps == []
    assert ["gh", "api", f"repos/{REPO}/compare/{SHA}...long-running--v1.0.3", "--jq", ".status"] in github.calls
    assert github.claude_calls() == [
        ["claude", "plugin", "marketplace", "update", "skills"],
        ["claude", "plugin", "update", "long-running@skills"],
    ]


def test_a_tag_behind_the_sha_is_polled_until_a_new_tag_catches_up(
    fake: Callable[..., FakeGitHub], capsys: pytest.CaptureFixture[str]
) -> None:
    github = fake(
        ["long-running--v1.0.2", "long-running--v1.0.2", "long-running--v1.0.3"],
        {"long-running--v1.0.2": "behind", "long-running--v1.0.3": "identical"},
    )
    code = install.main("long-running@skills", SHA, None, 900)
    assert code == 0
    assert github.sleeps == [20, 20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(github.claude_calls()) == 2


def test_a_timeout_names_the_last_ref_and_installs_nothing(
    fake: Callable[..., FakeGitHub], capsys: pytest.CaptureFixture[str]
) -> None:
    github = fake(["long-running--v1.0.1", "long-running--v1.0.2"], {"long-running--v1.0.1": "behind", "long-running--v1.0.2": "diverged"})
    code = install.main("long-running@skills", SHA, None, 60)
    assert code == 1
    assert github.sleeps == [20, 20, 20]
    assert "last ref long-running--v1.0.2" in capsys.readouterr().err
    assert github.claude_calls() == []


def test_without_a_sha_it_installs_what_the_marketplace_names_now(
    fake: Callable[..., FakeGitHub], capsys: pytest.CaptureFixture[str]
) -> None:
    github = fake(["unused"], {})
    assert install.main("long-running@skills", None, None, 900) == 0
    assert [call[0] for call in github.calls] == ["claude", "claude"]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"


def test_an_explicit_repo_wins_over_the_known_marketplace(fake: Callable[..., FakeGitHub]) -> None:
    github = fake(["long-running--v1.0.3"], {"long-running--v1.0.3": "ahead"})
    assert install.main("long-running@skills", SHA, "someone/fork", 900) == 0
    assert github.calls[0][2] == "repos/someone/fork/contents/.claude-plugin/marketplace.json"


@pytest.mark.parametrize(("marketplace", "repo"), [("skills", REPO), ("revdiff", "umputun/revdiff")])
def test_the_repo_resolves_from_known_marketplaces(config: Path, marketplace: str, repo: str) -> None:
    assert install.marketplace_repo(marketplace) == repo


def test_a_marketplace_with_no_github_repo_asks_for_repo(
    fake: Callable[..., FakeGitHub], capsys: pytest.CaptureFixture[str]
) -> None:
    fake(["unused"], {})
    assert install.main("plugin@forge", SHA, None, 900) == 1
    assert "pass --repo" in capsys.readouterr().err
