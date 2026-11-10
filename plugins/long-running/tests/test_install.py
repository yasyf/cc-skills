import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from drive import install

SHA = "abc123def4567890"
REPO = "yasyf/cc-skills"
SHOW_MARKETPLACE = ["show", f"{install.DEFAULT_BRANCH}:.claude-plugin/marketplace.json"]


class FakeGit:
    def __init__(self, config: Path, refs: list[str], holding: set[str], call_seconds: float, path: str | None) -> None:
        self.config = config
        self.refs = refs
        self.path = path
        self.holding = holding
        self.call_seconds = call_seconds
        self.now = 1000.0
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def run(self, cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        self.timeouts.append(options["timeout"])
        if self.call_seconds > options["timeout"]:
            self.now += options["timeout"]
            raise subprocess.TimeoutExpired(cmd, options["timeout"])
        self.now += self.call_seconds
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 1, "", "gh: API rate limit exceeded (HTTP 403)")
        return subprocess.CompletedProcess(cmd, 0, self.answer(cmd), "")

    def next_ref(self) -> str:
        return self.refs.pop(0) if len(self.refs) > 1 else self.refs[0]

    def answer(self, cmd: list[str]) -> str:
        verb = cmd[3:] if cmd[:2] == ["git", "-C"] else []
        if verb == SHOW_MARKETPLACE:
            source = self.path or {"source": "git-subdir", "url": f"https://github.com/{REPO}", "ref": self.next_ref()}
            return json.dumps({"name": "skills", "plugins": [{"name": "long-running", "source": source}]})
        if verb[:1] == ["log"]:
            return self.next_ref() + "\n"
        if verb[:2] == ["rev-list", "--count"]:
            return "0\n" if verb[3].removeprefix("^") in self.holding else "4\n"
        if cmd[:3] == ["claude", "plugin", "update"]:
            entry = {"scope": "user", "version": "1.0.3", "lastUpdated": "2026-11-09T10:00:00.000Z"}
            stale = {"scope": "project", "version": "0.7.2119", "lastUpdated": "2026-11-01T10:00:00.000Z"}
            installed = {"version": 2, "plugins": {"long-running@skills": [stale, entry]}}
            (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps(installed))
        return ""

    def git_calls(self) -> list[list[str]]:
        return [call[3:] for call in self.calls if call[:2] == ["git", "-C"]]

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
def fake(config: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeGit]:
    def make(refs: list[str], holding: set[str], call_seconds: float = 0, path: str | None = None) -> FakeGit:
        git = FakeGit(config, refs, holding, call_seconds, path)
        monkeypatch.setattr(install.subprocess, "run", git.run)
        monkeypatch.setattr(install, "clock", git.clock)
        monkeypatch.setattr(install, "sleep", git.sleep)
        return git

    return make


def test_a_tag_that_already_contains_the_sha_updates_and_prints_the_version(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    code = install.main("long-running@skills", SHA, None, 900)
    assert code == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert git.sleeps == []
    assert git.git_calls() == [
        ["remote", "add", "origin", f"https://github.com/{REPO}"],
        [*install.FETCH, f"+HEAD:{install.DEFAULT_BRANCH}"],
        SHOW_MARKETPLACE,
        [*install.FETCH, "+refs/tags/long-running--v1.0.3:refs/tags/long-running--v1.0.3"],
        ["rev-list", "--count", SHA, "^long-running--v1.0.3"],
    ]
    assert git.claude_calls() == [
        ["claude", "plugin", "marketplace", "update", "skills"],
        ["claude", "plugin", "update", "long-running@skills"],
    ]


def test_a_github_api_rate_limit_cannot_stall_an_install(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, None, 540) == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert (git.sleeps, [call for call in git.calls if call[0] == "gh"]) == ([], [])


def test_a_tag_behind_the_sha_is_polled_until_a_new_tag_catches_up(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.2", "long-running--v1.0.2", "long-running--v1.0.3"], {"long-running--v1.0.3"})
    code = install.main("long-running@skills", SHA, None, 900)
    assert code == 0
    assert git.sleeps == [20, 20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(git.claude_calls()) == 2


def test_a_timeout_names_the_last_ref_and_installs_nothing(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.1", "long-running--v1.0.2"], set())
    code = install.main("long-running@skills", SHA, None, 60)
    assert code == 1
    assert git.sleeps == [20, 20, 20]
    assert "last ref long-running--v1.0.2" in capsys.readouterr().err
    assert git.claude_calls() == []


@pytest.mark.parametrize(
    ("path", "manifest"),
    [("./captain_hook", "captain_hook/.claude-plugin/plugin.json"), ("./", ".claude-plugin/plugin.json")],
)
def test_a_path_source_waits_for_the_manifest_commit_that_contains_the_sha(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], path: str, manifest: str
) -> None:
    git = fake(["0ld5ync", "n3w5ync"], {"n3w5ync"}, path=path)
    assert install.main("long-running@skills", SHA, None, 900) == 0
    assert ["log", "-1", "--format=%H", install.DEFAULT_BRANCH, "--", manifest] in git.git_calls()
    assert ["rev-list", "--count", SHA, "^n3w5ync"] in git.git_calls()
    assert not [call for call in git.git_calls() if "refs/tags/" in call[-1]]
    assert git.sleeps == [20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(git.claude_calls()) == 2


def test_slow_reads_count_against_the_timeout(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.2"], set(), call_seconds=8)
    assert install.main("long-running@skills", SHA, None, 60) == 1
    assert git.now == 1060
    assert git.timeouts == [60, 52, 44, 36, 28, 20]
    assert git.sleeps == [12]
    assert "timed out after 60s" in capsys.readouterr().err
    assert git.claude_calls() == []


def test_a_hung_read_is_cut_at_the_deadline(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"}, call_seconds=3600)
    assert install.main("long-running@skills", SHA, None, 60) == 1
    assert git.now == 1060
    assert "ran past the deadline" in capsys.readouterr().err
    assert git.claude_calls() == []


def test_each_update_gets_only_the_time_left(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set(), call_seconds=5)
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.timeouts == [900, 895]


def test_without_a_sha_it_installs_what_the_marketplace_names_now(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900) == 0
    assert [call[0] for call in git.calls] == ["claude", "claude"]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"


def test_an_explicit_repo_wins_over_the_known_marketplace(fake: Callable[..., FakeGit]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, "someone/fork", 900) == 0
    assert git.git_calls()[0] == ["remote", "add", "origin", "https://github.com/someone/fork"]


@pytest.mark.parametrize(("marketplace", "repo"), [("skills", REPO), ("revdiff", "umputun/revdiff")])
def test_the_repo_resolves_from_known_marketplaces(config: Path, marketplace: str, repo: str) -> None:
    assert install.marketplace_repo(marketplace) == repo


def test_a_marketplace_with_no_github_repo_asks_for_repo(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    fake(["unused"], set())
    assert install.main("plugin@forge", SHA, None, 900) == 1
    assert "pass --repo" in capsys.readouterr().err
