import json
import plistlib
import re
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import chain, repeat
from pathlib import Path, PurePosixPath

import pytest
from conftest import commit_files, git_in
from drive import card, cli, dashboard, github, install, receiver, settled, state

SHA = "abc123def4567890"
REPO = "yasyf/cc-skills"
SHOW_MARKETPLACE = ["show", f"{install.DEFAULT_BRANCH}:.claude-plugin/marketplace.json"]
FOLDER = "plugins/long-running"
INBOX = "yasyf/cc-inbox"
INBOX_MANIFEST = "plugin/.claude-plugin/plugin.json"
CCI_BINRUN = "plugin/bin/cci.binrun"
RELEASE_TAG_WORKFLOW = ".github/workflows/release-tag.yml"
RELEASE_TAG = """name: Release tag

on:
  push:
    branches: [main]

jobs:
  release:
    uses: yasyf/cc-skills/.github/workflows/plugin-release-tag.yml@64dfeead8756405a13ecb86fb51fd0b3bb5d83b4
    with:
      release-workflow: release.yml
      paths: |
        internal
        go.mod
        :(exclude,glob)**/*_test.go
"""
CC_SLACK_AUTOBUMP = """name: Autobump
jobs:
  autobump:
    uses: yasyf/cc-skills/.github/workflows/plugin-autobump.yml@plugin-v1
    with:
      guard-plugins: "cc-slack"
  release:
    needs: autobump
    uses: yasyf/cc-skills/.github/workflows/plugin-release-tag.yml@plugin-v1
    with:
      release-workflow: release.yml
      dry-run: false
"""
CODEX_RELEASE_TAG = """jobs:
  release:
    uses: ./.github/workflows/plugin-release-tag.yml
    with:
      release-workflow: codex-release.yml
      paths: "plugins/codex/*.go :(exclude)plugins/codex/*_test.go"
"""
IDLE = {"ok": True, "result": {"wait": {"condition": "tui-idle", "satisfied": True, "status": "running", "exitCode": None}}}
BUSY = {"ok": False, "error": {"code": "timeout", "message": "timeout"}}
SENT = {"ok": True, "result": {"send": {"accepted": True}}}
TAP_DIR = "/opt/homebrew/Library/Taps/yasyf/homebrew-tap"
BREW_TAP = ["brew", "--repository", "yasyf/tap"]
TAP_PULL = ["git", "-C", TAP_DIR, "pull", "--ff-only", "--quiet"]
SEEDED = ("long-running@skills", "cc-context@skills", "cc-inbox@skills", "codex@skills")
OLD_COMMIT = "0ld0c0mm1t0ld"
LISTED = ("long-running", "cc-context", "cc-inbox", "codex", "cc-slack", "captain-hook")
HOOKS_FLOOR = {"name": "captain-hook", "marketplace": "captain-hook", "version": ">=12.121.70"}
SLACK_FLOOR = {"name": "cc-slack", "marketplace": "cc-slack", "version": ">=0.19.143"}
HELPER_CARD = install.HELPER_ASKED.format(release="12.121.70")
RESTART = ["capt-hook", "helper", "install"]
BUILD = "b1u1d0f1x"
URL = "https://github.com/yasyf/cc-skills/pull/7"
HOOKS_URL = "https://github.com/yasyf/captain-hook/pull/709"
RUN = subprocess.run


def upgrade(binary: str) -> list[str]:
    return ["env", "HOMEBREW_NO_AUTO_UPDATE=1", "brew", "upgrade", f"yasyf/tap/{binary}"]


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
        self.heard: list[str] = []
        self.delivered_every = 30.0
        self.orca_calls: list[list[str]] = []
        self.orca_timeouts: list[float] = []
        self.waits: dict[str, dict] = {}
        self.installed_sha: str | None = None
        self.bare: list[str] = []
        self.install_root = Path("/cache")
        self.together = threading.Barrier(1)
        self.projects: list[str] = []
        self.stuck: set[str] = set()
        self.cwds: list[str | None] = []
        self.needs: dict[str, list[dict]] = {}
        self.versions: dict[str, str] = {}
        self.app = config / "Captain Hook.app" / "Contents" / "Info.plist"
        self.helper = "12.121.70"
        self.proofs: set[str] = set()
        self.changed = f"{FOLDER}/lib/drive/install.py"
        self.drive_status = subprocess.CompletedProcess([], 0, "", "")
        self.tried: list[tuple[list[str], dict]] = []

    @property
    def helper(self) -> str:
        return plistlib.loads(self.app.read_bytes())["CFBundleShortVersionString"]

    @helper.setter
    def helper(self, version: str) -> None:
        self.app.parent.mkdir(parents=True, exist_ok=True)
        self.app.write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))

    def seed(self) -> None:
        def entry(scope: str, **fields: str) -> dict:
            return {"scope": scope, "version": "1.0.2", "lastUpdated": "2026-11-08T10:00:00.000Z", "installPath": "/cache/1.0.2", "gitCommitSha": OLD_COMMIT} | fields

        gone = entry("project", projectPath="/gone/worktree", version="0.7.2119", lastUpdated="2026-11-12T10:00:00.000Z", installPath="/cache/old")
        plugins = {key: [gone, entry("user"), *(entry("project", projectPath=folder) for folder in self.projects)] for key in SEEDED}
        self.write_installed(plugins)

    def write_installed(self, plugins: dict) -> None:
        (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": plugins}))

    def update(self, key: str, scope: str, cwd: str | None) -> None:
        plugins = json.loads((self.config / "plugins" / "installed_plugins.json").read_text())["plugins"]
        moved = next(each for each in plugins[key] if each["scope"] == scope and each.get("projectPath") == (None if scope == "user" else cwd))
        if scope not in self.stuck:
            plugin = key.partition("@")[0]
            moved |= {"version": self.versions.get(plugin, "1.0.3"), "lastUpdated": "2026-11-09T10:00:00.000Z", "installPath": str(self.install_root / plugin / "1.0.3"), "gitCommitSha": self.installed_sha or self.refs[0]}
        self.write_installed(plugins)

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def hear(self, repo: str, kind: str, since: str | None, seconds: float) -> None:
        self.heard.append(f"{repo} {kind}")
        self.now += min(self.delivered_every, seconds)

    def run(self, cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] == "orca":
            return self.orca(cmd, options["timeout"])
        if cmd[0] in self.proofs:
            return RUN(cmd, **options)
        self.calls.append(cmd)
        self.timeouts.append(options["timeout"])
        self.cwds.append(options.get("cwd"))
        if self.call_seconds > options["timeout"]:
            self.now += options["timeout"]
            raise subprocess.TimeoutExpired(cmd, options["timeout"])
        self.now += self.call_seconds
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 1, "", "gh: API rate limit exceeded (HTTP 403)")
        if cmd[0] == "env" and cmd[3:5] == ["status", "--drive"]:
            with pytest.MonkeyPatch.context() as copied:
                copied.setenv("LONG_RUNNING_STATE", cmd[1].partition("=")[2])
                self.tried.append((cmd, state.stored(cmd[5])))
            return subprocess.CompletedProcess(cmd, self.drive_status.returncode, "", self.drive_status.stderr)
        if cmd[:3] == ["claude", "plugin", "update"]:
            self.update(cmd[3], cmd[5], options.get("cwd"))
        if cmd == RESTART:
            self.helper = "12.121.70"
        return subprocess.CompletedProcess(cmd, 0, self.answer(cmd), "")

    def orca(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.orca_calls.append(cmd)
        self.orca_timeouts.append(timeout)
        if cmd[2] == "wait":
            self.together.wait()
        reply = self.waits.get(cmd[4], IDLE) if cmd[2] == "wait" else SENT
        return subprocess.CompletedProcess(cmd, 0 if reply["ok"] else 1, json.dumps(reply), "")

    def typed_into(self, terminal: str) -> list[list[str]]:
        return [call for call in self.orca_calls if call[4] == terminal]

    def next_ref(self) -> str:
        return self.refs.pop(0) if len(self.refs) > 1 else self.refs[0]

    def next_bare(self) -> str:
        return self.bare.pop(0) if len(self.bare) > 1 else self.bare[0]

    def answer(self, cmd: list[str]) -> str:
        verb = cmd[3:] if cmd[:2] == ["git", "-C"] else []
        if verb == SHOW_MARKETPLACE:
            ref = None if self.path else self.next_ref()
            sources = {name: self.path or {"source": "git-subdir", "url": f"https://github.com/{REPO}", "path": f"plugins/{name}", "ref": ref} for name in LISTED}
            return json.dumps({"name": "skills", "plugins": [{"name": name, "source": source} for name, source in sources.items()]})
        if verb[:1] == ["show"] and verb[1].endswith(install.MANIFEST):
            folder = PurePosixPath(verb[1].partition(":")[2]).parent.parent
            return json.dumps({"version": self.versions.get(folder.name, "1.0.3"), "dependencies": self.needs.get(folder.name, [])})
        if verb[:1] == ["log"]:
            return self.next_ref() + "\n"
        if cmd[1:] == ["--version"]:
            return self.next_bare() + "\n"
        if cmd == BREW_TAP:
            return TAP_DIR + "\n"
        if verb[:1] == ["diff-tree"]:
            return self.changed + "\n"
        if verb[:2] == ["rev-list", "--count"]:
            return "0\n" if verb[-1].removeprefix("^") in self.holding else "4\n"
        return ""

    def git_calls(self) -> list[list[str]]:
        return [call[3:] for call in self.calls if call[:2] == ["git", "-C"] and call[2] != TAP_DIR]

    def homebrew_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] in ("brew", "env") or call[:3] == ["git", "-C", TAP_DIR] or call[1:] == ["--version"]]

    def claude_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "claude"]

    def other_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] not in ("git", "claude")]


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_root: Path) -> Path:
    config = tmp_path / "claude"
    (config / "plugins").mkdir(parents=True)
    known = {
        "skills": {"source": {"source": "github", "repo": REPO}, "installLocation": str(config / "m")},
        "revdiff": {"source": {"source": "git", "url": "https://github.com/umputun/revdiff.git"}},
        "forge": {"source": {"source": "directory", "path": "/somewhere"}},
        "cc-slack": {"source": {"source": "github", "repo": "yasyf/cc-slack"}},
        "captain-hook": {"source": {"source": "github", "repo": "yasyf/captain-hook"}},
    }
    (config / "plugins" / "known_marketplaces.json").write_text(json.dumps(known))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def fake(config: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeGit]:
    def make(refs: list[str], holding: set[str], call_seconds: float = 0, path: str | None = None) -> FakeGit:
        git = FakeGit(config, refs, holding, call_seconds, path)
        git.seed()
        monkeypatch.setattr(install.subprocess, "run", git.run)
        monkeypatch.setattr(install, "APP_PLIST", git.app)
        monkeypatch.setattr(install, "clock", git.clock)
        monkeypatch.setattr(install, "sleep", git.sleep)
        monkeypatch.setattr(receiver, "hear", git.hear)
        monkeypatch.setattr(dashboard, "serving", lambda name, port: None)
        return git

    return make


def holds(sha: str, build: str) -> list[str]:
    return ["rev-list", "--count", "--first-parent", "--no-merges", sha, f"^{build}"]


def binrun(name: str, repo: str, version: str) -> str:
    provider = {"type": "github-release", "repo": repo, "tag": f"v{version}", "name": f"{name}_{version}_darwin_arm64.tar.gz"}
    spec = {"schema": 1, "name": name, "kind": "release-binary", "version": {"static": version}, "platforms": {"macos-aarch64": {"path": name, "providers": [provider]}}}
    return "#!/usr/bin/env binrun\n" + json.dumps(spec, indent=2)


def test_a_tag_that_already_contains_the_sha_updates_and_prints_the_version(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    code = install.main("long-running@skills", SHA, None, 900, None)
    assert code == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert git.sleeps == []
    assert git.git_calls() == [
        ["remote", "add", "origin", f"https://github.com/{REPO}"],
        [*install.FETCH, f"+HEAD:{install.DEFAULT_BRANCH}"],
        SHOW_MARKETPLACE,
        [*install.FETCH, "+refs/tags/long-running--v1.0.3:refs/tags/long-running--v1.0.3"],
        holds(SHA, "long-running--v1.0.3"),
        ["ls-tree", "-r", "--name-only", "long-running--v1.0.3", "--", FOLDER],
        ["show", f"long-running--v1.0.3:{FOLDER}/{install.MANIFEST}"],
        [*install.FETCH, "long-running--v1.0.3"],
        holds(SHA, "long-running--v1.0.3"),
    ]
    assert git.claude_calls() == [
        ["claude", "plugin", "marketplace", "update", "skills"],
        ["claude", "plugin", "update", "long-running@skills", "--scope", "user"],
    ]


def test_each_scope_installed_in_a_folder_still_here_is_updated_in_that_folder(fake: Callable[..., FakeGit], tmp_path: Path) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.projects = [str(tmp_path / "durable-agents")]
    (tmp_path / "durable-agents").mkdir()
    git.seed()
    assert install.main("long-running@skills", SHA, None, 900, None) == 0
    updates = [(call[-1], cwd) for call, cwd in zip(git.calls, git.cwds, strict=True) if call[:3] == ["claude", "plugin", "update"]]
    assert updates == [("user", None), ("project", str(tmp_path / "durable-agents"))]


def test_a_scope_the_update_left_without_the_sha_is_refused_naming_its_folder(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.projects = [str(tmp_path / "durable-agents")]
    (tmp_path / "durable-agents").mkdir()
    git.seed()
    git.stuck = {"project"}
    assert install.main("long-running@skills", SHA, None, 900, None) == 1
    assert capsys.readouterr().err.strip() == (
        f"drive install: long-running@skills in the project scope of {tmp_path / 'durable-agents'} is still at 1.0.2 ({OLD_COMMIT[:12]}) after the update,"
        f" which does not hold {SHA[:12]}, the sha awaited"
    )


def test_an_update_left_at_a_commit_without_the_sha_is_refused_naming_both(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.installed_sha = "0ldc0mm1t5ha99"
    assert install.main("long-running@skills", SHA, None, 900, None) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == f"drive install: long-running@skills in the user scope is still at 1.0.3 (0ldc0mm1t5ha) after the update, which does not hold {SHA[:12]}, the sha awaited"
    assert git.git_calls()[-2:] == [[*install.FETCH, "0ldc0mm1t5ha99"], holds(SHA, "0ldc0mm1t5ha99")]
    assert git.orca_calls == []


def test_an_update_running_a_binary_from_a_release_without_the_sha_is_refused_naming_the_release(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.install_root = tmp_path / "cache"
    bin_dir = git.install_root / "long-running" / "1.0.3" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "codex-ask.binrun").write_text(binrun("codex-ask", REPO, "1.15.2"))
    (bin_dir / "codex-ask.binrun.tmpl").write_text("{{ not json }}")
    assert install.main("long-running@skills", SHA, None, 900, None) == 1
    assert capsys.readouterr().err.strip() == (
        f"drive install: long-running@skills 1.0.3 in the user scope runs codex-ask from release v1.15.2 after the update, which does not hold {SHA[:12]}, the sha awaited"
    )
    assert git.git_calls()[-2:] == [[*install.FETCH, "+refs/tags/v1.15.2:refs/tags/v1.15.2"], holds(SHA, "v1.15.2")]


def test_a_binary_released_from_another_repo_is_not_held_to_the_sha(fake: Callable[..., FakeGit], tmp_path: Path) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.install_root = tmp_path / "cache"
    bin_dir = git.install_root / "long-running" / "1.0.3" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "slop-cop.binrun").write_text(binrun("slop-cop", "yasyf/slop-cop", "0.1.75"))
    assert install.main("long-running@skills", SHA, None, 900, None) == 0
    assert not [call for call in git.git_calls() if "v0.1.75" in call[-1]]


def test_a_github_api_rate_limit_cannot_stall_an_install(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, None, 540, None) == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert (git.sleeps, [call for call in git.calls if call[0] == "gh"]) == ([], [])


def test_a_tag_behind_the_sha_is_polled_until_a_new_tag_catches_up(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.2", "long-running--v1.0.2", "long-running--v1.0.3"], {"long-running--v1.0.3"})
    code = install.main("long-running@skills", SHA, None, 900, None)
    assert code == 0
    assert git.sleeps == [20, 20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(git.claude_calls()) == 2


def test_a_wait_names_each_new_release_lacking_the_sha_and_its_timeout_the_last(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.1", "long-running--v1.0.2"], set())
    code = install.main("long-running@skills", SHA, None, 60, None)
    assert code == 1
    assert git.sleeps == [20, 20, 20]
    waited, *_, timed_out = capsys.readouterr().err.splitlines()
    assert waited == f"drive install: waiting for a long-running release holding {SHA[:12]}: the newest, long-running--v1.0.1, lacks it"
    assert timed_out.startswith(f"drive install: timed out after 60s waiting for a long-running release holding {SHA[:12]}; the newest, long-running--v1.0.2, lacks it")
    assert git.claude_calls() == []


@pytest.mark.parametrize(
    ("path", "manifest", "folder"),
    [("./captain_hook", "captain_hook/.claude-plugin/plugin.json", "captain_hook"), ("./", ".claude-plugin/plugin.json", ".")],
)
def test_a_path_source_waits_for_the_manifest_commit_that_contains_the_sha(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], path: str, manifest: str, folder: str
) -> None:
    git = fake(["0ld5ync", "n3w5ync"], {"n3w5ync"}, path=path)
    assert install.main("long-running@skills", SHA, None, 900, None) == 0
    assert ["log", "-1", "--first-parent", "--format=%H", install.DEFAULT_BRANCH, "--", manifest] in git.git_calls()
    assert holds(SHA, "n3w5ync") in git.git_calls()
    assert ["ls-tree", "-r", "--name-only", "n3w5ync", "--", folder] in git.git_calls()
    assert not [call for call in git.git_calls() if "refs/tags/" in call[-1]]
    assert git.sleeps == [20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(git.claude_calls()) == 2


def test_slow_reads_count_against_the_timeout(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.2"], set(), call_seconds=8)
    assert install.main("long-running@skills", SHA, None, 60, None) == 1
    assert git.now == 1060
    assert git.timeouts == [60, 52, 44, 36, 28, 20, 12]
    assert git.sleeps == [4]
    assert "timed out after 60s" in capsys.readouterr().err
    assert git.claude_calls() == []


def test_a_hung_read_is_cut_at_the_deadline(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"}, call_seconds=3600)
    assert install.main("long-running@skills", SHA, None, 60, None) == 1
    assert git.now == 1060
    assert "ran past the deadline" in capsys.readouterr().err
    assert git.claude_calls() == []


def test_each_update_gets_only_the_time_left(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set(), call_seconds=5)
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert [timeout for call, timeout in zip(git.calls, git.timeouts, strict=True) if call[0] == "claude"] == [870, 865]


def test_without_a_sha_it_installs_what_the_marketplace_names_now(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.claude_calls() == [["claude", "plugin", "marketplace", "update", "skills"], ["claude", "plugin", "update", "long-running@skills", "--scope", "user"]]
    assert not [call for call in git.git_calls() if call[0] == "rev-list"]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"


def test_an_explicit_repo_wins_over_the_known_marketplace(fake: Callable[..., FakeGit]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, "someone/fork", 900, None) == 0
    assert git.git_calls()[0] == ["remote", "add", "origin", "https://github.com/someone/fork"]


@pytest.mark.parametrize(("marketplace", "repo"), [("skills", REPO), ("revdiff", "umputun/revdiff")])
def test_the_repo_resolves_from_known_marketplaces(config: Path, marketplace: str, repo: str) -> None:
    assert install.marketplace_repo(marketplace) == repo


def test_a_merge_touches_each_installed_plugin_of_its_repo_whose_folder_it_changes_and_a_marketplaces_only_plugin_on_any_change(config: Path) -> None:
    inbox = {"source": {"source": "github", "repo": "yasyf/cc-inbox"}, "installLocation": str(config / "i")}
    (config / "plugins" / "known_marketplaces.json").write_text(json.dumps(json.loads((config / "plugins" / "known_marketplaces.json").read_text()) | {"inbox": inbox}))
    listed = {
        "m": [
            {"name": "slop-cop", "source": "./plugins/slop-cop"},
            {"name": "long-running", "source": {"source": "git-subdir", "url": f"https://github.com/{REPO}", "path": "plugins/long-running", "ref": "long-running--v1.0.3"}},
            {"name": "cci", "source": {"source": "github", "repo": "yasyf/cc-inbox"}},
        ],
        "i": [{"name": "cc-inbox", "source": "./"}],
    }
    for folder, plugins in listed.items():
        (config / folder / ".claude-plugin").mkdir(parents=True)
        (config / folder / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": plugins}))
    installed = {key: [] for key in ("slop-cop@skills", "long-running@skills", "cci@skills", "cc-inbox@inbox", "revdiff@revdiff")}
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": installed}))
    assert install.touched(REPO, ["plugins/long-running/lib/drive/land.py", "README.md"]) == ["long-running@skills"]
    assert install.touched(REPO, ["plugins/slop-cop/x.py", "plugins/long-running/SKILL.md"]) == ["slop-cop@skills", "long-running@skills"]
    assert install.touched(REPO, ["README.md"]) == []
    assert install.touched("yasyf/cc-inbox", ["cmd/cci/main.go"]) == ["cc-inbox@inbox"]


def test_a_marketplace_with_no_github_repo_asks_for_repo(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    fake(["unused"], set())
    assert install.main("plugin@forge", SHA, None, 900, None) == 1
    assert "pass --repo" in capsys.readouterr().err


def test_a_plugin_the_marketplace_does_not_name_fails_at_once(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("cci@skills", SHA, None, 900, None) == 1
    assert "the skills marketplace names no plugin cci" in capsys.readouterr().err
    assert (git.sleeps, git.claude_calls()) == ([], [])


@pytest.fixture
def inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    git_in(origin, "init", "-q", "-b", "main")
    marketplace = {"name": "cc-inbox", "plugins": [{"name": "cc-inbox", "source": "./plugin"}]}
    files = {
        ".claude-plugin/marketplace.json": json.dumps(marketplace),
        INBOX_MANIFEST: json.dumps({"version": "0.54.26"}),
        CCI_BINRUN: binrun("cci", INBOX, "0.54.26"),
        "internal/grep.go": "package grep",
    }
    commit_files(origin, "base", files)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"https://github.com/{INBOX}")
    return origin


def tag_release(origin: Path, version: str) -> None:
    git_in(origin, "checkout", "-q", "-b", f"sync-{version}")
    commit_files(origin, f"chore: sync cci.binrun + manifest to v{version}", {INBOX_MANIFEST: json.dumps({"version": version}), CCI_BINRUN: binrun("cci", INBOX, version)})
    git_in(origin, "tag", f"v{version}")
    git_in(origin, "checkout", "-q", "main")


def merge_release(origin: Path, version: str) -> str:
    git_in(origin, "merge", "-q", "--no-ff", "-m", f"Merge sync-{version}", f"sync-{version}")
    return git_in(origin, "rev-parse", "HEAD")


def test_a_release_tagged_before_a_fix_landed_waits_for_the_next_though_its_sync_merged_after_the_fix(
    inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tag_release(inbox, "0.54.27")
    fix = commit_files(inbox, "Fix: cci grep prints its cut line once", {"internal/grep.go": "package grep\n\nfunc cut() {}"})
    synced = merge_release(inbox, "0.54.27")
    released: list[str] = []

    def release_next(seconds: float) -> None:
        tag_release(inbox, "0.54.28")
        released.append(merge_release(inbox, "0.54.28"))

    monkeypatch.setattr(install, "sleep", release_next)
    install.await_release(str(tmp_path / "clone"), INBOX, "cc-inbox", fix, install.clock() + 120, 120)
    assert len(released) == 1
    assert capsys.readouterr().err.splitlines() == [
        f"drive install: waiting for a cc-inbox release holding {fix[:12]}: the newest, {synced}, runs cci from release v0.54.27, which lacks it"
    ]


def test_a_release_sync_merged_straight_back_holds_its_own_merge_commit(
    inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tag_release(inbox, "0.54.27")
    synced = merge_release(inbox, "0.54.27")
    monkeypatch.setattr(install, "sleep", lambda seconds: pytest.fail("waited for a release that already holds its own sync merge"))
    install.await_release(str(tmp_path / "clone"), INBOX, "cc-inbox", synced, install.clock() + 120, 120)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("workflow", "jobs"),
    [
        (RELEASE_TAG, [["internal", "go.mod", ":(exclude,glob)**/*_test.go"]]),
        (CC_SLACK_AUTOBUMP, [[]]),
        (CODEX_RELEASE_TAG, [["plugins/codex/*.go", ":(exclude)plugins/codex/*_test.go"]]),
        ("jobs:\n  autobump:\n    uses: yasyf/cc-skills/.github/workflows/plugin-autobump.yml@plugin-v1\n", []),
    ],
    ids=["a paths block", "no paths", "inline paths", "no release-tag job"],
)
def test_each_release_tag_job_reads_as_the_pathspecs_release_tag_py_splits_from_its_paths_input(workflow: str, jobs: list[list[str]]) -> None:
    assert install.release_tag_paths(workflow) == jobs


def released_workflow(origin: Path) -> None:
    commit_files(origin, "Ship the release tag workflow", {RELEASE_TAG_WORKFLOW: RELEASE_TAG})
    tag_release(origin, "0.54.27")
    merge_release(origin, "0.54.27")


def test_a_merge_changing_no_path_its_release_tag_workflow_releases_from_owes_no_install(
    inbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    released_workflow(inbox)
    changed = {RELEASE_TAG_WORKFLOW: RELEASE_TAG + "\n", "AGENTS.md": "Each merge releases at once.", "internal/grep_test.go": "package grep"}
    merge = commit_files(inbox, "Fix: each merge to main that changes cci's sources tags and releases cci (#566)", changed)
    git_run = install.subprocess.run

    def git_only(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        assert cmd[0] == "git", f"ran {cmd} for a merge no release holds"
        return git_run(cmd, **options)

    monkeypatch.setattr(install.subprocess, "run", git_only)
    monkeypatch.setattr(install, "sleep", lambda seconds: pytest.fail("waited for a release no workflow cuts"))
    assert install.main("cc-inbox@cc-inbox", merge, INBOX, 120, None) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"no release: no commit from v0.54.27 to {merge[:12]} changes a path {RELEASE_TAG_WORKFLOW} tags a cc-inbox release from, so the next release that does carries it"
    ]


@pytest.mark.parametrize("workflow_only_after", [False, True], ids=["the source merge", "a workflow-only merge after it"])
def test_a_merge_on_a_line_that_changed_a_release_path_since_the_newest_tag_waits_for_the_release_that_carries_it(
    inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workflow_only_after: bool
) -> None:
    released_workflow(inbox)
    merge = commit_files(inbox, "Fix: cci grep prints its cut line once (#565)", {"internal/grep.go": "package grep\n\nfunc cut() {}"})
    if workflow_only_after:
        merge = commit_files(inbox, "Fix: each merge to main that changes cci's sources tags and releases cci (#566)", {RELEASE_TAG_WORKFLOW: RELEASE_TAG + "\n"})
    released: list[str] = []

    def release_next(seconds: float) -> None:
        tag_release(inbox, "0.54.28")
        released.append(merge_release(inbox, "0.54.28"))

    monkeypatch.setattr(install, "sleep", release_next)
    install.await_release(str(tmp_path / "clone"), INBOX, "cc-inbox", merge, install.clock() + 120, 120)
    assert len(released) == 1


def test_installing_cc_inbox_moves_the_cci_daemon_onto_the_new_build(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["unused"], set())
    assert install.main("cc-inbox@skills", None, None, 900, None) == 0
    assert git.other_calls() == [["/cache/cc-inbox/1.0.3/bin/cci", "serve"], BREW_TAP, upgrade("cci")]
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("cci")]
    assert capsys.readouterr().out.strip() == "installed cc-inbox@skills 1.0.3"


def test_installing_codex_upgrades_the_homebrew_codex_ask(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set())
    assert install.main("codex@skills", None, None, 900, None) == 0
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("codex-ask")]


def test_cco_is_upgraded_through_the_cc_orchestrate_cask(fake: Callable[..., FakeGit], tmp_path: Path) -> None:
    git = fake(["unused"], set())
    install.await_homebrew(str(tmp_path / "clone"), "cco", None, install.clock() + 900, 900)
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("cc-orchestrate")]


def test_a_homebrew_twin_is_upgraded_until_the_bare_binary_runs_a_release_holding_the_sha(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["cc-context--v1.0.3"], {"cc-context--v1.0.3", "v0.95.70"})
    git.bare = ["v0.95.69", "v0.95.70"]
    assert install.main("cc-context@skills", SHA, None, 900, None) == 0
    each_pass = [TAP_PULL, upgrade("ccx"), ["ccx", "--version"]]
    assert git.homebrew_calls() == [BREW_TAP, *each_pass, *each_pass]
    assert holds(SHA, "v0.95.69") in git.git_calls()
    assert git.sleeps == [20]
    out, err = capsys.readouterr()
    assert out.strip() == "installed cc-context@skills 1.0.3"
    assert err.strip() == f"drive install: waiting for yasyf/tap/ccx to hold {SHA[:12]}: bare ccx runs release v0.95.69, which lacks it"


def test_a_bare_binary_still_lacking_the_sha_at_the_deadline_refuses_the_install(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["cc-context--v1.0.3"], {"cc-context--v1.0.3"})
    git.bare = ["0.95.69 (5aff6f4)"]
    assert install.main("cc-context@skills", SHA, None, 60, None) == 1
    assert git.sleeps == [20, 20]
    out, err = capsys.readouterr()
    assert out == ""
    assert err.splitlines()[-1] == f"drive install: timed out after 60s waiting for bare ccx to hold {SHA[:12]}; bare ccx runs release v0.95.69, which lacks it"


def test_a_homebrew_pass_fetches_only_the_yasyf_tap_so_another_taps_fetch_cannot_fail_the_install(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["cc-context--v1.0.3"], {"cc-context--v1.0.3", "v0.95.70"})
    git.bare = ["v0.95.70"]
    assert install.main("cc-context@skills", SHA, None, 900, None) == 0
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("ccx"), ["ccx", "--version"]]
    assert not [call for call in git.calls if call[:2] == ["brew", "update"]]
    assert capsys.readouterr().out.strip() == "installed cc-context@skills 1.0.3"


def test_other_plugins_run_nothing_after_the_update(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.other_calls() == []


def test_installs_run_at_once_take_turns_on_the_plugin_updates(fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch) -> None:
    git = fake(["unused"], set())
    running: list[list[str]] = []
    most_at_once: list[int] = []

    def slow_claude(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] != "claude":
            return git.run(cmd, **options)
        running.append(cmd)
        most_at_once.append(len(running))
        time.sleep(0.05)
        running.remove(cmd)
        return git.run(cmd, **options)

    monkeypatch.setattr(install.subprocess, "run", slow_claude)
    keys = ["long-running@skills", "cc-inbox@skills", "codex@skills"]
    with ThreadPoolExecutor() as pool:
        versions = list(pool.map(lambda key: install.install(key, None, None, git.clock() + 900, 900, None), keys))
    assert versions == ["1.0.3"] * 3
    assert len(git.claude_calls()) == 6
    assert max(most_at_once) == 1


def start_drive(name: str, **terminals: str | None) -> None:
    (first, terminal), *rest = terminals.items()
    data = state.new_drive(name, first, f"{name}-{first}", "/work", None, terminal)
    for role, held in rest:
        data["roles"][role] = {**data["roles"][first], "session": f"{name}-{role}", "terminal": held}
    state.create(data)


def waited(terminal: str, wait_ms: str) -> list[str]:
    return ["orca", "terminal", "wait", "--terminal", terminal, "--for", "tui-idle", "--timeout-ms", wait_ms, "--json"]


def sent(terminal: str) -> list[str]:
    return ["orca", "terminal", "send", "--terminal", terminal, "--text", "/reload-plugins", "--enter", "--wait-submit", "10", "--json"]


def by_hand(terminal: str) -> str:
    return f"send it with: orca terminal send --terminal {terminal} --text /reload-plugins --enter"


def test_an_install_types_reload_plugins_into_both_drive_sessions_once_each_is_idle(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "30000"), sent("term_comm")]
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: reloaded in term_comm",
        "demo coordinator: reloaded in term_coord",
    ]


def test_an_install_reloads_the_sessions_of_a_drive_a_newer_install_saved_after_its_update(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    state.store("demo", state.stored("demo") | {"saved_by": "9.9.9"})
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: reloaded in term_comm",
        "demo coordinator: reloaded in term_coord",
    ]


def test_a_session_still_busy_after_the_wait_gets_reload_plugins_queued_behind_its_turn(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    git.waits["term_coord"] = BUSY
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "30000"), sent("term_comm")]
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert capsys.readouterr().out.splitlines()[1:] == [
        "demo communicator: reloaded in term_comm",
        "demo coordinator: busy for 30s, so /reload-plugins is queued in term_coord and runs when its turn ends",
    ]


def test_an_install_near_its_deadline_waits_only_the_time_left(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["unused"], set(), call_seconds=5)
    git.waits["term_comm"] = BUSY
    assert install.main("long-running@skills", None, None, 80, None) == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "10000"), sent("term_comm")]
    assert sorted(git.orca_timeouts) == [60, 70]
    assert "demo communicator: busy for 10s" in capsys.readouterr().out


def test_drive_reload_reloads_every_drive_session_an_install_already_reloaded(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    earlier = {"at": state.stamp(state.now() - timedelta(minutes=5)), "version": "1.0.2"}
    (state.drive_dir("demo") / install.RELOADS).write_text(json.dumps({"communicator": earlier, "coordinator": earlier}))
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    assert install.reload_main() == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "30000"), sent("term_comm")]
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert git.calls == []
    assert capsys.readouterr().out.splitlines() == ["demo communicator: reloaded in term_comm", "demo coordinator: reloaded in term_coord"]


def test_a_terminal_orca_cannot_find_or_that_shows_a_prompt_is_typed_nothing(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_gone", coordinator="term_menu")
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    git.waits["term_gone"] = {"ok": False, "error": {"code": "terminal_not_found", "message": "no terminal term_gone"}}
    git.waits["term_menu"] = {"ok": True, "result": {"wait": {"satisfied": False, "blockedReason": "agent-trust-workspace"}}}
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert [call[2] for call in git.orca_calls] == ["wait", "wait"]
    assert capsys.readouterr().out.splitlines()[1:] == [
        f"demo communicator: not reloaded (orca terminal wait: terminal_not_found: no terminal term_gone); {by_hand('term_gone')}",
        f"demo coordinator: not reloaded (orca terminal wait: it shows agent-trust-workspace); {by_hand('term_menu')}",
    ]


def test_a_drive_session_with_no_terminal_recorded_is_skipped_with_a_line(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("solo", coordinator=None)
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.orca_calls == []
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "solo coordinator: no terminal recorded, skipped",
    ]


def test_a_session_another_install_reloaded_after_this_update_is_not_reloaded_twice(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    reloads = state.drive_dir("demo") / install.RELOADS
    stamps = {
        "communicator": {"at": state.stamp(state.now() + timedelta(minutes=1)), "version": "1.0.3"},
        "coordinator": {"at": state.stamp(state.now() - timedelta(hours=1)), "version": "1.0.2"},
    }
    reloads.write_text(json.dumps(stamps))
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.typed_into("term_comm") == []
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: already reloaded onto 1.0.3",
        "demo coordinator: reloaded in term_coord",
    ]
    assert json.loads(reloads.read_text())["coordinator"]["version"] == "1.0.3"


def test_an_install_reloads_each_sub_coordinator_beside_both_roles_stamping_each_session(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    with state.edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "demo-iris", "terminal": "term_iris", "cwd": "/work", "started": state.stamp()}
    git = fake(["unused"], set())
    git.together = threading.Barrier(3, timeout=5)
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.typed_into("term_iris") == [waited("term_iris", "30000"), sent("term_iris")]
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: reloaded in term_comm",
        "demo coordinator: reloaded in term_coord",
        "demo sub-coordinator Iris: reloaded in term_iris",
    ]
    assert json.loads((state.drive_dir("demo") / install.RELOADS).read_text())["sub-coordinator Iris"]["version"] == "1.0.3"


def test_a_reload_never_sent_leaves_no_stamp_so_the_next_install_sends_it(fake: Callable[..., FakeGit]) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["unused"], set())
    git.waits["term_comm"] = {"ok": False, "error": {"code": "terminal_not_found", "message": "no terminal term_comm"}}
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert "communicator" not in json.loads((state.drive_dir("demo") / install.RELOADS).read_text())


def test_a_long_running_release_whose_own_drive_status_fails_on_a_copy_of_a_drive_is_refused_before_any_scope_updates(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.drive_status = subprocess.CompletedProcess([], 1, "", "NameError: name 'report_path' is not defined")
    assert install.main("long-running@skills", SHA, None, 900, None) == 1
    assert git.claude_calls() == []
    assert git.orca_calls == []
    err = capsys.readouterr().err
    assert "refused long-running--v1.0.3, keeping the release installed now: its own drive status failed on a copy of the demo drive" in err
    assert "NameError: name 'report_path' is not defined" in err


def test_a_long_running_release_reads_a_copy_of_every_drive_with_its_own_drive_status_before_it_installs(fake: Callable[..., FakeGit]) -> None:
    start_drive("demo", communicator="term_comm")
    start_drive("other", communicator="term_other")
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, None, 900, None) == 0
    copies = {Path(cmd[1].partition("=")[2]) for cmd, _ in git.tried}
    assert [cmd[3:] for cmd, _ in git.tried] == [["status", "--drive", "demo"], ["status", "--drive", "other"]]
    assert all(cmd[2].endswith(f"{FOLDER}/bin/drive") for cmd, _ in git.tried)
    assert len(copies) == 1 and state.root() not in copies
    assert [saved for _, saved in git.tried] == [state.stored(name) for name in ("demo", "other")]
    assert git.calls.index(git.tried[-1][0]) < git.calls.index(["claude", "plugin", "marketplace", "update", "skills"])
    checkout = next(call for call in git.git_calls() if "checkout" in call)
    assert checkout[1:] == ["checkout", "long-running--v1.0.3", "--", FOLDER]


def test_a_plugin_other_than_long_running_runs_no_drive_status(fake: Callable[..., FakeGit]) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["codex--v1.0.3"], {"codex--v1.0.3", "v1.0.3"})
    git.bare = ["v1.0.3"]
    assert install.main("codex@skills", SHA, None, 900, None) == 0
    assert git.tried == []


def needing_the_app(fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, answers: list[dict]) -> FakeGit:
    start_drive("demo", coordinator=None)
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.needs = {"long-running": [HOOKS_FLOOR]}
    git.versions = {"captain-hook": "12.121.70"}
    git.helper = "12.121.68"
    hooks = {"scope": "user", "version": "12.121.70", "lastUpdated": "2026-11-12T04:00:00.000Z", "installPath": "/cache/captain-hook/12.121.70", "gitCommitSha": OLD_COMMIT}
    installed = json.loads((git.config / "plugins" / "installed_plugins.json").read_text())["plugins"]
    git.write_installed(installed | {"captain-hook@captain-hook": [hooks]})
    monkeypatch.setattr(github, "read", lambda what, cmd, max_age="0s", text=True: "v12.121.70\n")
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: json.dumps(answers))
    return git


@pytest.mark.parametrize("key", ["long-running@skills", "captain-hook@captain-hook"])
def test_a_plugin_needing_a_newer_captain_hook_app_installs_nothing_until_his_install_now_tap(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], key: str
) -> None:
    git = needing_the_app(fake, monkeypatch, [{"title": HELPER_CARD, "body": "Not now\nOptions: Install now (recommended) | Installs never need me | Not now"}])
    assert install.main(key, None, None, 900, "demo") == 1
    assert capsys.readouterr().err.strip() == (
        f'drive install: holds {key} for his card "{HELPER_CARD}" (Install now (recommended), Installs never need me and Not now), asked with drive job ask:'
        " it needs captain-hook 12.121.70, and Captain Hook.app, which runs every hook, runs 12.121.68; run this again once `drive answer` records his pick"
    )
    with state.edit("demo") as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "ship", [], state.stamp()) | {"questions": [state.new_question("/asks/j1-helper.md", HELPER_CARD, state.stamp())]}
    assert install.main(key, None, None, 900, "demo") == 1
    assert capsys.readouterr().err.startswith(f"drive install: holds {key} for his card /asks/j1-helper.md: it needs captain-hook 12.121.70")
    assert (git.claude_calls(), RESTART in git.calls, git.helper) == ([], False, "12.121.68")


def test_his_install_now_tap_moves_captain_hook_app_onto_the_release_before_the_plugin_installs(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = needing_the_app(fake, monkeypatch, [{"title": HELPER_CARD, "body": "Install now\nOptions: Install now (recommended) | Installs never need me | Not now"}])
    assert install.main("long-running@skills", SHA, None, 900, "demo") == 0
    assert git.calls.index(RESTART) < git.calls.index(["claude", "plugin", "marketplace", "update", "skills"])
    assert git.helper == "12.121.70"
    assert capsys.readouterr().out.splitlines()[0] == "installed long-running@skills 1.0.3"


def test_an_app_already_at_the_floor_is_not_restarted(fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch) -> None:
    git = needing_the_app(fake, monkeypatch, [])
    git.helper = "12.121.70"
    monkeypatch.setattr(github, "read", lambda *args, **kwargs: pytest.fail("read captain-hook's newest release for an app at the floor"))
    assert install.main("long-running@skills", SHA, None, 900, "demo") == 0
    assert RESTART not in git.calls


def needing_slack(fake: Callable[..., FakeGit], released: str) -> FakeGit:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.needs = {"long-running": [SLACK_FLOOR]}
    git.versions = {"cc-slack": released}
    git.bare = [f"v{released}"]
    slack = {"scope": "user", "version": "0.19.142", "lastUpdated": "2026-11-12T04:00:00.000Z", "installPath": "/cache/cc-slack/0.19.142", "gitCommitSha": OLD_COMMIT}
    installed = json.loads((git.config / "plugins" / "installed_plugins.json").read_text())["plugins"]
    git.write_installed(installed | {"cc-slack@cc-slack": [slack]})
    return git


def test_a_sibling_below_the_release_a_plugin_needs_installs_before_it(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = needing_slack(fake, "0.19.143")
    assert install.main("long-running@skills", SHA, None, 900, None) == 0
    assert git.claude_calls() == [
        ["claude", "plugin", "marketplace", "update", "cc-slack"],
        ["claude", "plugin", "update", "cc-slack@cc-slack", "--scope", "user"],
        ["claude", "plugin", "marketplace", "update", "skills"],
        ["claude", "plugin", "update", "long-running@skills", "--scope", "user"],
    ]
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("cc-slack"), ["cc-slack", "--version"]]
    assert capsys.readouterr().out.splitlines()[0] == "installed long-running@skills 1.0.3"


def test_a_plugin_whose_sibling_release_is_not_out_waits_for_it_and_installs_nothing_past_the_deadline(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = needing_slack(fake, "0.19.142")
    assert install.main("long-running@skills", SHA, None, 60, None) == 1
    lacking = "cc-slack@cc-slack in the user scope is at 0.19.142"
    assert capsys.readouterr().err.splitlines() == [
        f"drive install: waiting for cc-slack@cc-slack 0.19.143: {lacking}",
        f"drive install: timed out after 60s waiting for cc-slack@cc-slack 0.19.143; {lacking}",
    ]
    assert git.sleeps == [20, 20]
    assert ["claude", "plugin", "update", "long-running@skills", "--scope", "user"] not in git.calls


def proving(config: Path, tmp_path: Path, git: FakeGit, exits: int) -> Path:
    (config / "m" / ".claude-plugin").mkdir(parents=True)
    listed = [{"name": "long-running", "source": FOLDER}, {"name": "cc-context", "source": "plugins/cc-context"}]
    (config / "m" / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": listed}))
    script = tmp_path / "proof.sh"
    script.write_text(f'#!/bin/sh\necho "ran on ${{PATH%%:*}}"\nexit {exits}\n')
    script.chmod(0o755)
    git.proofs.add(str(script))
    start_drive("demo", coordinator=None)
    with state.edit("demo") as data:
        part = "Drive tooling: prove each install"
        data["orders"]["o1"] = state.new_order(part, "slack", None, {part: state.new_part()}, state.stamp()) | {"jobs": ["j1"]}
        data["jobs"]["j1"] = state.new_job("o1", "builder", "prove", [part], state.stamp()) | {"state": "waiting", "said": f"PUSHED {URL}", "prs": [URL], "repro": str(script)}
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(URL, "j1", "o1") | {"state": "merged", "merged_sha": SHA, "title": "Fix: prove each install"}
    return script


def proof_events() -> list[tuple[str, str]]:
    return [(f"{event['repo']}#{event['number']}", event["change"]) for event, _ in state.read_events("demo") if event["kind"] == "proof"]


def test_a_proof_installs_a_merge_its_plugin_lacks_runs_the_jobs_proof_file_on_it_takes_done_and_keeps_the_close_report_for_the_watch(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    kept = state.proof_path("demo", "j1")
    checked = f"{install.PROVEN} on long-running@skills 1.0.3: {script} exits 0, its output {kept}"
    proven = f"{checked}; its close report of o1 waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
    assert install.prove("demo", "j1", 900) == f"j1: {proven}"
    assert ["claude", "plugin", "update", "long-running@skills", "--scope", "user"] in git.calls
    assert kept.read_text() == "ran on /cache/long-running/1.0.3/bin\n"
    data = state.load("demo")
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("done", f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3")
    assert data["orders"]["o1"]["proof"] == str(kept)
    assert data["orders"]["o1"]["close_report"] == {
        "id": "install-proved-j1",
        "kind": "report",
        "to": ["communicator@demo"],
        "topic": "install-proved-o1",
        "attr": {"close": "o1"},
        "text": "POST Fix: prove each install is live in long-running@skills 1.0.3",
        "body": f"Fixed: Fix: prove each install, {URL}\nLive: long-running@skills 1.0.3\nProof: {checked}\n",
    }
    assert [call for call in git.other_calls() if call[:1] == ["cci"]] == []
    assert proof_events() == [("yasyf/cc-skills#7", proven)]


def test_a_proof_holds_no_install_lock_past_its_update_and_hands_its_result_to_the_newer_install_that_saved_meanwhile(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    held, handed = [], []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION
    real = subprocess.run

    def newer(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == str(script):
            held.append(dict(state.HELD_INSTALL))
            monkeypatch.setattr(state, "VERSION", "0.0.1")
        if argv[0] != str(newest):
            return real(argv, **kwargs)
        handed.append(argv[1:])
        monkeypatch.setattr(state, "VERSION", version)
        code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capsys.readouterr().out, "")

    monkeypatch.setattr(install, "upgraded", lambda: newest)
    monkeypatch.setattr(install.subprocess, "run", newer)
    state.hold_install()
    assert install.prove("demo", "j1", 900).startswith(f"j1: {install.PROVEN} on long-running@skills 1.0.3: {script} exits 0")
    [argv] = handed
    assert (held, argv[:3], argv[4:], Path(argv[3]).exists()) == ([{}], ["job", "proved", "j1"], ["--drive", "demo", "--json"], False)
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3")
    assert state.proof_path("demo", "j1").read_text() == "ran on /cache/long-running/1.0.3/bin\n"


def test_a_proof_of_merges_in_two_repositories_whose_installs_hold_them_runs_the_proof_file_with_no_install(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {OLD_COMMIT})
    script = proving(config, tmp_path, git, 0)
    slack = "https://github.com/yasyf/cc-slack/pull/3"
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(said=f"READY {URL} {slack}", prs=[URL, slack])
        data["prs"]["yasyf/cc-slack#3"] = state.new_pr(slack, "j1", "o1") | {"state": "merged", "merged_sha": "def456", "title": "Fix: the card"}
    assert install.prove("demo", "j1", 900).startswith(f"j1: {install.PROVEN} on long-running@skills 1.0.2: {script} exits 0")
    assert git.claude_calls() == []
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} {slack} live: {script} exits 0 on long-running@skills 1.0.2"


def proving_hooks(config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, app: str) -> tuple[FakeGit, Path]:
    git = fake([BUILD], {BUILD, OLD_COMMIT, "v12.121.71"})
    script = proving(config, tmp_path, git, 0)
    marketplace = config / "hooks-m" / ".claude-plugin"
    marketplace.mkdir(parents=True)
    (marketplace / "marketplace.json").write_text(json.dumps({"plugins": [{"name": "captain-hook", "source": "./plugin"}]}))
    known = json.loads((config / "plugins" / "known_marketplaces.json").read_text())
    known["captain-hook"]["installLocation"] = str(config / "hooks-m")
    (config / "plugins" / "known_marketplaces.json").write_text(json.dumps(known))
    hooks = {"scope": "user", "version": "12.121.71", "lastUpdated": "2026-11-12T17:43:53.491Z", "installPath": "/cache/captain-hook/12.121.71", "gitCommitSha": OLD_COMMIT, "auto": True}
    installed = json.loads((config / "plugins" / "installed_plugins.json").read_text())["plugins"]
    git.write_installed(installed | {"captain-hook@captain-hook": [hooks]})
    git.versions = {"captain-hook": "12.121.71"}
    git.helper = app
    monkeypatch.setattr(github, "read", lambda what, cmd, max_age="0s", text=True: "v12.121.71\n" if cmd[1] == "release" else "")
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: "[]")
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(said=f"PUSHED {HOOKS_URL}", prs=[HOOKS_URL])
        data["prs"]["yasyf/captain-hook#709"] = state.new_pr(HOOKS_URL, "j1", "o1") | {"state": "merged", "merged_sha": SHA, "title": "Fix: the process guard"}
    return git, script


def test_a_proof_of_a_captain_hook_merge_claude_code_updated_the_plugin_to_installs_it_while_captain_hook_app_lacks_it(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git, script = proving_hooks(config, tmp_path, fake, monkeypatch, "12.121.70")
    install.prove("demo", "j1", 900)
    card = install.HELPER_ASKED.format(release="12.121.71")
    assert state.load("demo")["jobs"]["j1"]["said"].startswith(f'BLOCKED {HOOKS_URL}: not installed, so {script} did not run: holds captain-hook@captain-hook for his card "{card}"')
    assert [*install.FETCH, "+refs/tags/v12.121.70:refs/tags/v12.121.70"] in git.git_calls()
    assert (git.claude_calls(), RESTART in git.calls, git.helper) == ([], False, "12.121.70")


def test_a_proof_of_a_captain_hook_merge_captain_hook_app_holds_runs_the_proof_file_with_no_install(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git, script = proving_hooks(config, tmp_path, fake, monkeypatch, "12.121.71")
    install.prove("demo", "j1", 900)
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {HOOKS_URL} live: {script} exits 0 on captain-hook@captain-hook 12.121.71"
    assert (git.claude_calls(), RESTART in git.calls) == ([], False)


def test_a_proof_of_a_merge_whose_plugin_holds_it_but_whose_bare_homebrew_binary_lacks_it_installs_it(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD, OLD_COMMIT, "v0.95.72"})
    git.changed = "plugins/cc-context/bin/ccx.binrun"
    git.bare = ["v0.95.71", "v0.95.72"]
    script = proving(config, tmp_path, git, 0)
    install.prove("demo", "j1", 900)
    assert ["claude", "plugin", "update", "cc-context@skills", "--scope", "user"] in git.calls
    assert upgrade("ccx") in git.calls
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on cc-context@skills 1.0.3"


def test_a_proof_of_a_yasyf_merge_beside_a_product_merge_installs_the_yasyf_one_alone_and_runs_the_proof_file(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    product = "https://github.com/Forge-AI/monorepo/pull/9"
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(said=f"READY {URL} {product}", prs=[URL, product])
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr(product, "j1", "o1") | {"state": "merged", "merged_sha": "fedcba", "title": "Fix: the page"}
    assert install.proof_gap(state.load("demo"), "j1") is None
    assert install.prove("demo", "j1", 900).startswith(f"j1: {install.PROVEN} on long-running@skills 1.0.3: {script} exits 0")
    assert ["claude", "plugin", "update", "long-running@skills", "--scope", "user"] in git.calls
    data = state.load("demo")
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("done", f"DONE {URL} {product} live: {script} exits 0 on long-running@skills 1.0.3")
    assert [key for key, _ in proof_events()] == ["yasyf/cc-skills#7", "Forge-AI/monorepo#9"]


def test_a_product_merge_takes_done_at_once_its_merge_commit_its_check_keeping_no_proof_or_close_report_and_names_the_release_it_waits_on(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    product = "https://github.com/Forge-AI/monorepo/pull/9"
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(said=f"READY {product}", prs=[product], repro=None)
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr(product, "j1", "o1") | {"state": "merged", "merged_sha": "fedcba9876543210", "title": "Fix: the page"}
        change = install.merged_done("demo", data, "j1")
        install.said_on_prs("demo", data, "j1", change)
    merged = "Forge-AI/monorepo#9 at fedcba987654"
    assert change == (
        f"{install.MERGED_DONE}: {merged}; no proof kept, since only a release holding fedcba987654 puts it live, so o1's fix-live note waits on that release:"
        ' drive job add builder --order o1 --goal "prove Forge-AI/monorepo#9 live" --waits release:<service>:Forge-AI/monorepo#9 adds the builder with a merge'
        " handoff that proves it there once the watch's FREE says that release is out, its DONE giving o1 its proof; <service> is a component"
        " `tools/ci release select --pr` finds its paths reach, and paths reaching none take no --waits"
    )
    data = state.load("demo")
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("done", f"DONE {product} merged: {merged}")
    assert (data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (None, None)
    assert proof_events() == [("Forge-AI/monorepo#9", change)]


def test_a_product_merge_names_the_surface_its_own_brief_names_for_the_proof_on_its_release(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    page = "the dashboard's run details page: the browser view of a step shows its screenshot"
    port, shown = "https://github.com/Forge-AI/monorepo/pull/9", "https://github.com/Forge-AI/monorepo/pull/10"
    with state.edit("demo") as data:
        order = data["orders"]["o1"]
        order["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "show each step", [*order["parts"]], state.stamp()) | {
            "state": "waiting",
            "said": f"READY {shown}",
            "prs": [shown],
            "brief_flags": {"native": page, "interactions": "open a run's page, click a step"},
        }
        data["jobs"]["j1"].update(said=f"READY {port}", prs=[port], repro=None, brief_flags={"native": "none"})
        for url, job_id, sha in ((port, "j1", "fedcba9876543210"), (shown, "j2", "0123456789abcdef")):
            data["prs"][state.pr_key(url)] = state.new_pr(url, job_id, "o1") | {"state": "merged", "merged_sha": sha, "title": "Fix: the page"}
        asked = install.merged_done("demo", data, "j2")
        merged = install.merged_done("demo", data, "j1")
    assert f"proves it there on {page}, where he sees it, once the watch's FREE says that release is out" in asked
    assert "proves it there once the watch's FREE says that release is out" in merged
    data = state.load("demo")
    assert [data["jobs"][job_id]["state"] for job_id in ("j1", "j2")] == ["done", "done"]
    assert (data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (None, None)


def test_an_incidents_fix_proven_live_records_it_live_and_leaves_its_close_to_the_watch(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    with state.edit("demo") as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["jobs"]["j1"]["parts"] = ["fix"]
    install.prove("demo", "j1", 900)
    order = state.load("demo")["orders"]["o1"]
    assert (order["close_report"], order["proof"]) == (None, f"{URL} live: {script} exits 0 on long-running@skills 1.0.3")
    assert order["fixed"] == f"j1 proved it live: {order['proof']}"
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROVEN} on long-running@skills 1.0.3: {script} exits 0, its output {state.proof_path('demo', 'j1')}")]


def test_a_proof_file_that_fails_takes_blocked_and_closes_nothing(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 1)
    install.prove("demo", "j1", 900)
    blocked = f"BLOCKED {URL}: {script} exits 1 on long-running@skills 1.0.3: ran on /cache/long-running/1.0.3/bin"
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("failed", blocked)
    assert state.load("demo")["orders"]["o1"]["proof"] is None
    assert not [call for call in git.other_calls() if call[0] == "cci"]
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROOF_FAILED}: {blocked.removeprefix('BLOCKED ')}")]


@pytest.mark.parametrize(("printed", "taken"), [("ran on the queue", "failed"), ("/tmp/press-j.png /tmp/hover-a-card.png", "done")])
def test_a_proof_of_something_he_sees_takes_done_only_once_its_output_pictures_each_user_story(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], printed: str, taken: str
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    script.write_text(f"#!/bin/sh\necho '{printed}'\n")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["brief_flags"] = {"native": "the dashboard's queue", "interactions": "press j and the next card opens; hover a card and its title shows"}
    install.prove("demo", "j1", 900)
    job = state.load("demo")["jobs"]["j1"]
    assert job["state"] == taken
    if taken == "failed":
        assert job["said"].startswith(
            f"BLOCKED {URL}: {script} exits 0 but falls short of its surface on long-running@skills 1.0.3: its brief names 2 user stories"
            " and its proof pictures or records 0 of them"
        )


def test_a_pr_merged_and_proved_before_the_watch_reads_it_names_its_close_report_by_the_title_drive_pr_add_read(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], on_github: dict
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    title = "Fix: an owner record about the drive adds its Drive tooling: order in the same record call"
    on_github["title"] = title
    with state.edit("demo") as data:
        data["prs"].clear()
        data["jobs"]["j1"]["prs"] = []
    cli.add_pr("demo", "yasyf/cc-skills#7", "j1", card.read("yasyf/cc-skills#7"))
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#7"].update(state="merged", merged_sha=SHA)
    install.prove("demo", "j1", 900)
    report = state.load("demo")["orders"]["o1"]["close_report"]
    assert (report["text"], report["body"].splitlines()[0]) == (f"POST {title} is live in long-running@skills 1.0.3", f"Fixed: {title}, {URL}")


def newest_in_the_user_scope(config: Path, git: FakeGit) -> None:
    plugins = json.loads((config / "plugins" / "installed_plugins.json").read_text())["plugins"]
    git.write_installed(plugins | {"long-running@skills": [entry for entry in plugins["long-running@skills"] if entry["scope"] == "user"]})


def test_a_proof_file_runs_once_the_drives_dashboard_runs_the_long_running_just_installed_and_not_before(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    newest_in_the_user_scope(config, git)
    shown = tmp_path / "dashboard-code"
    script.write_text(f"#!/bin/sh\ngrep -qx /cache/long-running/1.0.3/lib/drive {shown} || {{ echo 'the dashboard does not run the installed long-running'; exit 1; }}\n")
    answers = iter([{"code": "/cache/1.0.2/lib/drive"}, {"code": "/cache/1.0.2/lib/drive"}, {"code": "/cache/long-running/1.0.3/lib/drive"}])

    def serving(name: str, port: int) -> dict:
        drawn = next(answers)
        shown.write_text(drawn["code"] + "\n")
        return drawn

    monkeypatch.setattr(dashboard, "serving", serving)
    install.prove("demo", "j1", 900)
    assert git.sleeps == [dashboard.HANDOVER_POLL_SECONDS, dashboard.HANDOVER_POLL_SECONDS]
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"


def test_a_dashboard_still_on_an_older_install_at_the_deadline_takes_blocked_naming_it_and_runs_no_proof_file(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    newest_in_the_user_scope(config, git)
    monkeypatch.setattr(dashboard, "serving", lambda name, port: {"code": "/cache/1.0.2/lib/drive"})
    install.prove("demo", "j1", 60)
    stale = f"the dashboard on port {dashboard.port_of('demo')} still runs /cache/1.0.2, not the newest long-running installed, /cache/long-running/1.0.3"
    assert state.load("demo")["jobs"]["j1"]["said"] == f"BLOCKED {URL}: {script} exits unrun on long-running@skills 1.0.3: {stale}"
    assert state.proof_path("demo", "j1").read_text() == stale
    assert state.load("demo")["orders"]["o1"]["proof"] is None


@pytest.mark.parametrize(("lands", "runs"), [("while the dashboard hands over", 1), ("while the proof file fails", 2)])
def test_a_proof_file_runs_on_the_newest_install_read_right_before_it_and_again_on_one_that_landed_while_it_failed(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, lands: str, runs: int
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    script.write_text(
        '#!/bin/sh\necho "ran on ${PATH%%:*}"\n'
        '[ "${PATH%%:*}" = /cache/long-running/1.0.4/bin ] || { echo "drive.state.NewerDrive: the demo drive was saved by long-running 1.0.4, newer than this 1.0.3"; exit 1; }\n'
    )
    ran = []

    def land() -> None:
        plugins = json.loads((config / "plugins" / "installed_plugins.json").read_text())["plugins"]
        newer = {"scope": "user", "version": "1.0.4", "lastUpdated": "2026-11-10T10:00:00.000Z", "installPath": "/cache/long-running/1.0.4", "gitCommitSha": BUILD}
        git.write_installed(plugins | {"long-running@skills": [newer]})

    def run(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        done = git.run(cmd, **options)
        if cmd[0] == str(script):
            ran.append(done.stdout)
            if lands == "while the proof file fails":
                land()
        return done

    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr(dashboard, "serving", lambda name, port: land() if lands == "while the dashboard hands over" else None)
    install.prove("demo", "j1", 900)
    assert ran[-1:] == ["ran on /cache/long-running/1.0.4/bin\n"] and len(ran) == runs
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.4"
    assert state.proof_path("demo", "j1").read_text() == "ran on /cache/long-running/1.0.4/bin\n"


def ci(run_id: int, name: str, created: str, status: str = "in_progress", conclusion: str | None = None) -> dict:
    workflow = {"long-running CI": 366540480, "Guides": 310890487}[name]
    return {"id": run_id, "workflow_id": workflow, "name": name, "created_at": created, "status": status, "conclusion": conclusion, "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}"}


def on_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, triggered: list[dict], polls: list[list[dict]]) -> Path:
    shown = tmp_path / "runs"
    answers = chain(polls, repeat(polls[-1]))

    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        if cmd[-1] == ".default_branch":
            return "main\n"
        if cmd[1:3] == ["pr", "view"]:
            return f"{SHA}\n"
        runs = triggered if f"&head_sha={SHA}" in cmd[3] else next(answers)
        shown.write_text("".join(f"{run['id']} {run['status']} {run['conclusion']}\n" for run in runs))
        return "".join(json.dumps(run) + "\n" for run in runs)

    monkeypatch.setattr(github, "read", read)
    return shown


def test_a_proof_reading_main_ci_after_the_merge_runs_once_that_run_completed_and_not_while_it_runs(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    guides = ci(43375546815, "Guides", "2026-11-13T05:59:05Z", "completed", "success")
    running = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z")
    shown = on_actions(monkeypatch, tmp_path, [guides, running], [[guides, running], [guides, running | {"status": "completed", "conclusion": "success"}]])
    script.write_text(f"#!/bin/sh\ngrep -qx '43375546307 completed success' {shown}\n")
    install.prove("demo", "j1", 900)
    assert (git.heard, git.sleeps) == ([f"{REPO} ci"], [])
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"


def test_a_merge_whose_own_run_its_concurrency_group_cancelled_waits_on_the_run_of_the_push_that_superseded_it(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    cancelled = ci(43370495715, "long-running CI", "2026-11-13T05:12:31Z", "completed", "cancelled")
    superseding = ci(43370536229, "long-running CI", "2026-11-13T05:12:53Z")
    shown = on_actions(monkeypatch, tmp_path, [cancelled], [[superseding, cancelled], [superseding | {"status": "completed", "conclusion": "success"}, cancelled]])
    script.write_text(f"#!/bin/sh\ngrep -qx '43370536229 completed success' {shown}\n")
    install.prove("demo", "j1", 900)
    assert (git.heard, git.sleeps) == ([f"{REPO} ci"], [])
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"


def test_tonights_proof_reads_its_merges_ci_once_per_workflow_run_finishing_on_main_not_every_20_seconds(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake([BUILD], {BUILD})
    git.delivered_every = 180
    reads: list[str] = []
    running = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z")
    polls = iter([[running]] * 4 + [[running | {"status": "completed", "conclusion": "success"}]])

    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        if cmd[-1] == ".default_branch":
            return "main\n"
        reads.append(cmd[3])
        return "".join(json.dumps(run) + "\n" for run in ([running] if f"&head_sha={SHA}" in cmd[3] else next(polls)))

    monkeypatch.setattr(github, "read", read)
    start = install.clock()
    install.await_ci({f"{REPO}#7": SHA}, start + 3600)
    assert (git.now - start, len(git.heard), len(reads), git.sleeps) == (720, 4, 6, [])
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_a_ci_read_that_fails_is_read_again_within_poll_seconds_since_no_delivery_says_when_it_would_answer(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    done = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z", "completed", "success")
    answers = iter([github.GithubError("ccx: GitHub rate limit floor"), [done], [done]])

    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        if cmd[-1] == ".default_branch":
            return "main\n"
        if isinstance(answer := next(answers), Exception):
            raise answer
        return "".join(json.dumps(run) + "\n" for run in answer)

    monkeypatch.setattr(github, "read", read)
    install.await_ci({f"{REPO}#7": SHA}, install.clock() + 900)
    assert (git.sleeps, git.heard) == ([install.POLL_SECONDS], [])


def test_drive_pr_ci_judges_a_merge_by_the_run_that_superseded_its_cancelled_own_never_by_a_push_merged_seconds_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    earlier = ci(43384583289, "long-running CI", "2026-11-13T07:16:37Z", "completed", "failure")
    cancelled = ci(43384590795, "long-running CI", "2026-11-13T07:16:41Z", "completed", "cancelled")
    superseding = ci(43384815193, "long-running CI", "2026-11-13T07:18:31Z", "completed", "success")
    guides = ci(43384591254, "Guides", "2026-11-13T07:16:42Z", "completed", "success")
    on_actions(monkeypatch, tmp_path, [cancelled, guides], [[earlier, cancelled, guides, superseding]])
    assert cli.main(["pr", "ci", URL]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"yasyf/cc-skills#7 merged at {SHA[:12]}: passed",
        f"Guides https://github.com/{REPO}/actions/runs/43384591254 completed success",
        f"long-running CI https://github.com/{REPO}/actions/runs/43384815193 completed success",
    ]


def test_drive_pr_ci_exits_1_while_a_workflow_of_the_merge_failed_or_has_no_run_that_was_not_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = ci(43384583289, "long-running CI", "2026-11-13T07:16:37Z", "completed", "failure")
    cancelled = ci(43384591254, "Guides", "2026-11-13T07:16:42Z", "completed", "cancelled")
    on_actions(monkeypatch, tmp_path, [failed, cancelled], [[failed, cancelled]])
    assert cli.main(["pr", "ci", URL]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"yasyf/cc-skills#7 merged at {SHA[:12]}: not passed",
        f"long-running CI https://github.com/{REPO}/actions/runs/43384583289 completed failure",
        f"Guides: no run since {SHA[:12]} that was not cancelled",
    ]


def test_a_ci_run_still_running_at_the_deadline_takes_blocked_naming_it_and_runs_no_proof_file(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    running = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z")
    on_actions(monkeypatch, tmp_path, [running], [[running]])
    install.prove("demo", "j1", 60)
    waiting = f"yasyf/cc-skills#7's merge {SHA[:12]} still waits on CI at the deadline: long-running CI https://github.com/{REPO}/actions/runs/43375546307 in_progress"
    assert state.load("demo")["jobs"]["j1"]["said"] == f"BLOCKED {URL}: {script} exits unrun on long-running@skills 1.0.3: {waiting}"
    assert state.proof_path("demo", "j1").read_text() == waiting


def test_a_merge_no_release_will_carry_takes_blocked_naming_it_and_runs_no_proof_file(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], set())
    script = proving(config, tmp_path, git, 0)
    reason = "no release: no commit from v1.0.2 to abc123def456 changes a path release-tag.yml tags a long-running release from, so the next release that does carries it"

    def unreleased(*args: object) -> str:
        raise install.NoRelease(reason)

    monkeypatch.setattr(install, "install", unreleased)
    install.prove("demo", "j1", 900)
    blocked = f"BLOCKED {URL}: not installed, so {script} did not run: {reason}"
    assert state.load("demo")["jobs"]["j1"]["said"] == blocked
    assert state.proof_path("demo", "j1").read_text() == reason
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROOF_FAILED}: {blocked.removeprefix('BLOCKED ')}")]


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"repro": None}, "j1 keeps no proof file to run: keep one with drive job set j1 --repro <file>"),
        ({"prs": ["https://github.com/Forge-AI/monorepo/pull/9"]}, "Forge-AI/monorepo#9 merged outside yasyf/*, where a merge goes live only in its repository's release"),
        ({"said": "WAITING on the release"}, "j1 waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove"),
    ],
)
def test_a_proof_is_refused_naming_who_proves_it_for_a_job_with_no_proof_file_only_merges_outside_yasyf_or_no_merged_ready(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], change: dict, refusal: str
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr("https://github.com/Forge-AI/monorepo/pull/9", "j1", "o1") | {"state": "merged", "merged_sha": "fedcba"}
        data["jobs"]["j1"].update(change)
    with pytest.raises(state.DriveError, match=re.escape(refusal)):
        install.prove("demo", "j1", 900)
    assert (state.load("demo")["jobs"]["j1"]["state"], proof_events()) == ("waiting", [])


def test_an_install_holding_a_merged_jobs_fix_proves_nothing_since_the_watch_does(config: Path, tmp_path: Path, fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert capsys.readouterr().out.splitlines() == ["installed long-running@skills 1.0.3", "demo coordinator: no terminal recorded, skipped"]
    assert (state.load("demo")["jobs"]["j1"]["state"], proof_events()) == ("waiting", [])


def test_a_release_read_that_fails_is_said_as_it_fails_and_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reads = iter([install.InstallError("git fetch exited 128: unable to access"), ({"name": "cc-notes", "source": "./plugin"}, True)])

    def listed(clone: str, plugin: str, deadline: float) -> tuple[dict, bool]:
        read = next(reads)
        if isinstance(read, install.InstallError):
            raise read
        return read

    monkeypatch.setattr(install, "listed", listed)
    monkeypatch.setattr(install, "newest_release", lambda clone, entry, deadline: ("v0.76.11", "plugin"))
    monkeypatch.setattr(install, "release_lacking", lambda clone, repo, named, sha, deadline: None)
    monkeypatch.setattr(install, "sleep", lambda seconds: None)
    assert install.await_release(str(tmp_path / "clone"), "yasyf/cc-notes", "cc-notes", SHA, install.clock() + 60, 60) == ("v0.76.11", "plugin")
    assert capsys.readouterr().err.splitlines() == [
        f"drive install: a read of the cc-notes release failed, reading again within {install.POLL_SECONDS}s: git fetch exited 128: unable to access"
    ]
