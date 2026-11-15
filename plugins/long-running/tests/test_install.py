import fcntl
import json
import os
import plistlib
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from itertools import chain, repeat
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import REAL_PLUGIN_COPIES_FIRST, carrying, commit_files, git_in, Jev
from drive import card, cli, dashboard, github, install, reads, receiver, settled, state, view, watch

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
SENT = {"ok": True, "result": {"send": {"accepted": True}}}
SEEDED_UPDATE = "2026-11-08T10:00:00.000Z"
UPDATED = "2026-11-09T10:00:00.000Z"
UNANSWERED = {"ok": False, "error": {"code": "runtime_busy", "message": "long-poll capacity reached; retry with backoff"}}
RELOADED = {"type": "system", "subtype": "local_command", "content": "<local-command-stdout>Reloaded: 33 plugins · 51 skills</local-command-stdout>"}
CREATED = {"ok": True, "result": {"terminal": {"handle": "term_shown"}}}
TAP_DIR = "/opt/homebrew/Library/Taps/yasyf/homebrew-tap"
BREW_TAP = ["brew", "--repository", "yasyf/tap"]
BREW_PREFIX = ["brew", "--prefix"]
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
PRODUCT = "https://github.com/Forge-AI/monorepo/pull/9"
HOOKS_URL = "https://github.com/yasyf/captain-hook/pull/709"


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
        self.sends: dict[str, list[dict]] = {}
        self.unreloaded: set[str] = set()
        self.reloading: Callable[[], None] = lambda: None
        self.detached: list[list[str]] = []
        self.exited: Callable[[], None] = lambda: None
        self.installed_sha: str | None = None
        self.bare: list[str] = []
        self.install_root = Path("/cache")
        self.projects: list[str] = []
        self.repos: dict[str, str] = {}
        self.stuck: set[str] = set()
        self.cwds: list[str | None] = []
        self.needs: dict[str, list[dict]] = {}
        self.versions: dict[str, str] = {}
        self.app = config / "Captain Hook.app" / "Contents" / "Info.plist"
        self.helper = "12.121.70"
        self.changed = f"{FOLDER}/lib/drive/install.py"
        self.drive_status = subprocess.CompletedProcess([], 0, "", "")
        self.tried: list[tuple[list[str], dict]] = []
        self.refusal = subprocess.CompletedProcess([], 2, "", "drive: no order, job or PR j0; name an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL, #N or its bare number\n")
        self.refusals: list[list[str]] = []

    @property
    def helper(self) -> str:
        return plistlib.loads(self.app.read_bytes())["CFBundleShortVersionString"]

    @helper.setter
    def helper(self, version: str) -> None:
        self.app.parent.mkdir(parents=True, exist_ok=True)
        self.app.write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))

    def seed(self) -> None:
        def entry(scope: str, **fields: str) -> dict:
            return {"scope": scope, "version": "1.0.2", "lastUpdated": SEEDED_UPDATE, "installPath": "/cache/1.0.2", "gitCommitSha": OLD_COMMIT} | fields

        gone = entry("project", projectPath="/gone/worktree", version="0.7.2119", lastUpdated="2026-11-12T10:00:00.000Z", installPath="/cache/old")
        plugins = {key: [gone, entry("user"), *(entry("project", projectPath=folder) for folder in self.projects)] for key in SEEDED}
        self.write_installed(plugins)

    def write_installed(self, plugins: dict) -> None:
        (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": plugins}))

    def update(self, key: str, scope: str, cwd: str | None) -> None:
        plugins = json.loads((self.config / "plugins" / "installed_plugins.json").read_text())["plugins"]
        home = self.repos.get(cwd, cwd)
        moved = next(
            each
            for each in plugins[key]
            if each["scope"] == scope and (scope == "user" or (Path(each["projectPath"]).is_dir() and self.repos.get(each["projectPath"], each["projectPath"]) == home))
        )
        if scope not in self.stuck:
            plugin = key.partition("@")[0]
            moved |= {"version": self.versions.get(plugin, "1.0.3"), "lastUpdated": UPDATED, "installPath": str(self.install_root / plugin / "1.0.3"), "gitCommitSha": self.installed_sha or self.refs[0]}
        self.write_installed(plugins)

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def hear(self, repo: str, kind: str, since: str | None) -> None:
        self.heard.append(f"{repo} {kind}")
        self.now += self.delivered_every

    def run(self, cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] == "orca":
            return self.orca(cmd, options["timeout"])
        if cmd[3:] == ["rev-parse", "--path-format=absolute", "--git-common-dir"]:
            if cmd[2] in self.repos:
                return subprocess.CompletedProcess(cmd, 0, f"{self.repos[cmd[2]]}\n", "")
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: not a git repository (or any of the parent directories): .git\n")
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
        if cmd[0] == "env" and cmd[3:5] == ["status", install.NO_JOB]:
            self.refusals.append(cmd)
            return subprocess.CompletedProcess(cmd, self.refusal.returncode, "", self.refusal.stderr)
        if cmd[:3] == ["claude", "plugin", "update"]:
            self.update(cmd[3], cmd[5], options.get("cwd"))
        if cmd == RESTART:
            self.helper = "12.121.70"
        return subprocess.CompletedProcess(cmd, 0, self.answer(cmd), "")

    def orca(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.orca_calls.append(cmd)
        self.orca_timeouts.append(timeout)
        if cmd[2] == "create":
            return subprocess.CompletedProcess(cmd, 0, json.dumps(CREATED), "")
        replies = self.sends.get(cmd[4], [SENT])
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if reply["ok"] and "/exit" in cmd:
            self.exited()
        if reply["ok"] and "/reload-plugins" in cmd and cmd[4] not in self.unreloaded:
            self.reloading()
            path = watch.session_transcript(next(held for name in state.names() for held in state.bound_sessions(state.saved(name)).values() if held["terminal"] == cmd[4]))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as transcript:
                transcript.write(json.dumps({"type": "user", "message": {"role": "user", "content": "<command-name>/reload-plugins</command-name>"}}) + "\n" + json.dumps(RELOADED) + "\n")
        return subprocess.CompletedProcess(cmd, 0 if reply["ok"] else 1, json.dumps(reply), "")

    def detach(self, name: str, args: list[str], log: str) -> int:
        self.detached.append([*args, "--drive", name])
        return 4242

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
        if cmd == BREW_PREFIX:
            return "/opt/homebrew\n"
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
        monkeypatch.setattr(state, "detach", git.detach)
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


def test_only_a_repositorys_first_project_install_is_updated_and_checked_since_no_session_loads_a_later_worktrees(
    fake: Callable[..., FakeGit], tmp_path: Path
) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    checkout, workspace = tmp_path / "durable-agents", tmp_path / "durable-agents-j15"
    checkout.mkdir()
    workspace.mkdir()
    git.projects = [str(checkout), str(workspace)]
    git.repos = {str(checkout): str(tmp_path / "monorepo" / ".git"), str(workspace): str(tmp_path / "monorepo" / ".git")}
    git.seed()
    assert install.main("long-running@skills", SHA, None, 900, None) == 0
    updates = [(call[-1], cwd) for call, cwd in zip(git.calls, git.cwds, strict=True) if call[:3] == ["claude", "plugin", "update"]]
    assert updates == [("user", None), ("project", str(checkout))]
    assert [entry.get("projectPath") for entry in install.installs("long-running@skills")] == [None, str(checkout)]


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


def manifest_released(origin: Path, subject: str) -> str:
    commit_files(origin, "Ship the release tag workflow", {RELEASE_TAG_WORKFLOW: RELEASE_TAG + f"        {INBOX_MANIFEST}\n"})
    tag_release(origin, "0.54.27")
    commit_files(origin, subject, {INBOX_MANIFEST: json.dumps({"version": "0.54.27", "mcpServers": {}})})
    return commit_files(origin, "Docs: name the channel's launcher", {"AGENTS.md": "The channel runs the plugin's own cci."})


def test_a_release_s_own_manifest_sync_on_main_owes_no_release_where_the_manifest_is_a_release_path(
    inbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    merge = manifest_released(inbox, "chore(release): sync cci.binrun + manifest to v0.54.27")
    monkeypatch.setattr(install, "sleep", lambda seconds: pytest.fail("waited for a release no workflow cuts"))
    assert install.main("cc-inbox@cc-inbox", merge, INBOX, 120, None) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"no release: no commit from v0.54.27 to {merge[:12]} changes a path {RELEASE_TAG_WORKFLOW} tags a cc-inbox release from, so the next release that does carries it"
    ]


def test_a_manifest_fix_where_the_manifest_is_a_release_path_waits_for_the_release_that_carries_it(
    inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge = manifest_released(inbox, "Fix: the channel server runs the plugin's own cci (#570)")
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
    each_pass = [TAP_PULL, upgrade("ccx"), BREW_PREFIX, ["/opt/homebrew/bin/ccx", "--version"]]
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
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("ccx"), BREW_PREFIX, ["/opt/homebrew/bin/ccx", "--version"]]
    assert not [call for call in git.calls if call[:2] == ["brew", "update"]]
    assert capsys.readouterr().out.strip() == "installed cc-context@skills 1.0.3"


def test_a_process_the_drive_starts_runs_the_newest_plugin_copy_of_each_homebrew_twin_before_homebrews(config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    homebrew, older, newer = tmp_path / "homebrew" / "bin", tmp_path / "cc-context" / "0.96.6", tmp_path / "cc-context" / "0.96.10"
    for folder in (homebrew, older / "bin", newer / "bin"):
        folder.mkdir(parents=True)
        (folder / "ccx").write_text("#!/bin/sh\n")
        (folder / "ccx").chmod(0o755)
    plugins = {
        "cc-context@cc-context": [
            {"scope": "user", "installPath": str(older), "lastUpdated": "2026-11-12T10:00:00.000Z"},
            {"scope": "project", "projectPath": "/gone/worktree", "installPath": str(newer), "lastUpdated": "2026-11-13T20:15:00.000Z"},
        ],
        "long-running@skills": [{"scope": "user", "installPath": str(tmp_path / "long-running" / "1.0.893"), "lastUpdated": "2026-11-13T20:15:00.000Z"}],
    }
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": plugins}))
    monkeypatch.setenv("PATH", str(homebrew))
    path = REAL_PLUGIN_COPIES_FIRST()
    assert path.split(os.pathsep) == [str(newer / "bin"), str(homebrew)]
    assert shutil.which("ccx", path=path) == str(newer / "bin" / "ccx")


def test_long_running_runs_only_its_new_installs_drive_migrate_after_the_update(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.other_calls() == [["/cache/long-running/1.0.3/bin/drive", "migrate"]]


def test_a_failed_drive_migrate_fails_the_install_before_any_session_reloads_onto_the_new_code(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["unused"], set())

    def migrate_fails(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[1:] == ["migrate"]:
            return subprocess.CompletedProcess(cmd, 2, "", "drive: could not read his recorded answers: ccn timed out")
        return git.run(cmd, **options)

    monkeypatch.setattr(install.subprocess, "run", migrate_fails)
    assert install.main("long-running@skills", None, None, 900, None) == 1
    assert capsys.readouterr().err.strip() == (
        "drive install: /cache/long-running/1.0.3/bin/drive migrate exited 2: drive: could not read his recorded answers: ccn timed out"
    )
    assert git.orca_calls == []


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
    data = state.new_drive(name, first, f"{name}-{first}", "/work", terminal)
    for role, held in rest:
        data["roles"][role] = {**data["roles"][first], "session": f"{name}-{role}", "terminal": held}
    state.create(data)


@pytest.fixture
def running(live_sessions: dict[str, int | None]) -> Callable[..., None]:
    def recorded(*sessions: str, status: str = "idle", folder: str = "/work") -> None:
        records = install.config_dir() / "sessions"
        records.mkdir(exist_ok=True)
        for pid, session in zip((os.getpid(), os.getppid(), 1), sessions, strict=False):
            (records / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": session, "cwd": folder, "status": status}))
            live_sessions[session] = pid

    return recorded


def handed(drive: str, terminal: str) -> str:
    return f"pid 4242 types /reload-plugins into {terminal} once its turn ends, said in {state.drive_dir(drive) / install.RELOADS_LOG}"


def sent(terminal: str, text: str = "/reload-plugins") -> list[str]:
    return ["orca", "terminal", "send", "--terminal", terminal, "--text", text, "--enter", "--retry-request"]


def sends(git: FakeGit, terminal: str) -> list[list[str]]:
    return [call[:-2] for call in git.typed_into(terminal)]


def held(git: FakeGit, capsys: pytest.CaptureFixture[str]) -> list[str]:
    while git.detached:
        assert cli.main(git.detached.pop(0)) == 0
    return [line.partition(" ")[2] for line in capsys.readouterr().out.splitlines()]


def stamps(drive: str) -> dict[str, str]:
    return {seat: stamp["onto"] for seat, stamp in json.loads((state.drive_dir(drive) / install.RELOADS).read_text()).items()}


def test_an_install_hands_each_drive_session_to_one_process_that_types_reload_plugins_once_its_turn_ends(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    running("demo-communicator", "demo-coordinator")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.orca_calls == []
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        f"demo communicator: {handed('demo', 'term_comm')}",
        f"demo coordinator: {handed('demo', 'term_coord')}",
    ]
    assert git.detached == [["reload", "--seat", "communicator", "--drive", "demo"], ["reload", "--seat", "coordinator", "--drive", "demo"]]
    assert held(git, capsys) == [
        "demo communicator: reloaded onto long-running@skills 1.0.3 in term_comm",
        "demo coordinator: reloaded onto long-running@skills 1.0.3 in term_coord",
    ]
    assert sends(git, "term_comm") == [sent("term_comm")]
    assert sends(git, "term_coord") == [sent("term_coord")]
    assert stamps("demo") == {"communicator": UPDATED, "coordinator": UPDATED}


def test_an_install_reloads_the_sessions_of_a_drive_a_newer_install_saved_after_its_update(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    state.store("demo", state.stored("demo") | {"saved_by": "9.9.9"})
    running("demo-communicator", "demo-coordinator")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert capsys.readouterr().out.splitlines()[1:] == [f"demo communicator: {handed('demo', 'term_comm')}", f"demo coordinator: {handed('demo', 'term_coord')}"]
    assert held(git, capsys) == [
        "demo communicator: reloaded onto long-running@skills 1.0.3 in term_comm",
        "demo coordinator: reloaded onto long-running@skills 1.0.3 in term_coord",
    ]


def test_an_install_that_changed_nothing_a_session_loads_types_nothing(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    held(git, capsys)
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.detached == []
    assert capsys.readouterr().out.splitlines() == ["installed long-running@skills 1.0.3", "demo communicator: already reloaded onto long-running@skills 1.0.3"]
    assert sends(git, "term_comm") == [sent("term_comm")]


@pytest.mark.parametrize("status", ["busy", "waiting"])
def test_the_process_holding_a_terminal_types_reload_plugins_only_once_claude_code_reads_its_turn_over(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator", status=status)
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()

    def turn_ends_after_3_seconds(seconds: float) -> None:
        assert git.orca_calls == []
        git.sleep(seconds)
        if len(git.sleeps) == 3:
            running("demo-communicator")

    monkeypatch.setattr(install, "sleep", turn_ends_after_3_seconds)
    assert held(git, capsys) == ["demo communicator: reloaded onto long-running@skills 1.0.3 in term_comm"]
    assert git.sleeps == [1, 1, 1]
    assert sends(git, "term_comm") == [sent("term_comm")]


def test_an_install_finding_a_terminals_reload_lock_held_leaves_its_reload_to_that_process(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    with open(install.reload_lock("term_comm"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert install.main("long-running@skills", None, None, 900, None) == 0
        assert git.detached == []
        assert cli.main(["reload", "--seat", "communicator", "--drive", "demo"]) == 0
    assert git.orca_calls == []
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: pending, as the process holding its reload lock (no holder found) types /reload-plugins into term_comm once its turn ends",
    ]


def test_an_install_landing_while_a_reload_runs_is_reloaded_once_more_after_that_reload_shows(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    pending: list[str] = []

    def newer_install_lands() -> None:
        if pending:
            return
        plugins = json.loads((git.config / "plugins" / "installed_plugins.json").read_text())["plugins"]
        plugins["codex@skills"][1] |= {"version": "1.0.4", "lastUpdated": "2026-11-10T10:00:00.000Z"}
        git.write_installed(plugins)
        pending.append(install.reload_session("demo", "communicator", False))

    git.reloading = newer_install_lands
    assert held(git, capsys) == [
        "demo communicator: reloaded onto long-running@skills 1.0.3 in term_comm",
        "demo communicator: reloaded onto codex@skills 1.0.4 in term_comm",
    ]
    assert pending == ["demo communicator: pending, as the process holding its reload lock (no holder found) types /reload-plugins into term_comm once its turn ends"]
    assert sends(git, "term_comm") == [sent("term_comm"), sent("term_comm")]
    assert stamps("demo") == {"communicator": "2026-11-10T10:00:00.000Z"}


def test_an_install_finding_the_lock_held_while_its_holder_finds_nothing_left_is_reloaded_once_the_holder_lets_go(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    stamped, landed, pending = install.reloaded_onto, [], []

    def another_holder_reloaded_then_an_install_landed(drive: str, seat: str) -> str:
        with open(install.reload_lock("term_comm"), "a") as probe:
            if landed or state.taken(probe, 0):
                return stamped(drive, seat)
        landed.append(seat)
        (state.drive_dir(drive) / install.RELOADS).write_text(json.dumps({seat: {"onto": UPDATED, "at": state.stamp()}}))
        plugins = json.loads((git.config / "plugins" / "installed_plugins.json").read_text())["plugins"]
        plugins["codex@skills"][1] |= {"version": "1.0.4", "lastUpdated": "2026-11-10T10:00:00.000Z"}
        git.write_installed(plugins)
        pending.append(install.reload_session("demo", "communicator", False))
        return UPDATED

    monkeypatch.setattr(install, "reloaded_onto", another_holder_reloaded_then_an_install_landed)
    assert held(git, capsys) == [
        "demo communicator: already reloaded onto long-running@skills 1.0.3",
        "demo communicator: reloaded onto codex@skills 1.0.4 in term_comm",
    ]
    assert pending == ["demo communicator: pending, as the process holding its reload lock (no holder found) types /reload-plugins into term_comm once its turn ends"]
    assert sends(git, "term_comm") == [sent("term_comm")]
    assert stamps("demo") == {"communicator": "2026-11-10T10:00:00.000Z"}


def test_a_send_orca_never_answered_is_reissued_under_its_retry_request_id_so_it_is_typed_once(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    git.sends["term_comm"] = [UNANSWERED, UNANSWERED, SENT]
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == ["demo communicator: reloaded onto long-running@skills 1.0.3 in term_comm"]
    first, *again = git.typed_into("term_comm")
    assert again == [first, first]
    assert git.sleeps == [1, 1]


def test_a_send_orca_never_answers_names_the_retry_request_id_that_reissues_it_and_is_left_unstamped(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    git.sends["term_comm"] = [UNANSWERED]
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    lines = held(git, capsys)
    request_id = git.typed_into("term_comm")[0][-2]
    assert {call[-2] for call in git.typed_into("term_comm")} == {request_id}
    assert len(git.typed_into("term_comm")) == install.RELOAD_WAIT_SECONDS + 1
    assert lines == [
        "demo communicator: not reloaded (orca terminal send: runtime_busy: long-poll capacity reached; retry with backoff;"
        f" reissue it with: orca terminal send --terminal term_comm --text /reload-plugins --enter --retry-request {request_id})"
    ]
    assert not (state.drive_dir("demo") / install.RELOADS).exists()


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        ({"ok": False, "error": {"code": "terminal_not_found", "message": "no terminal term_comm"}}, "terminal_not_found: no terminal term_comm"),
        ({"ok": True, "result": {"send": {"accepted": False, "refusedReason": "agent_prompt_blocked"}}}, "refused: agent_prompt_blocked"),
    ],
    ids=["a terminal Orca cannot find", "a send Orca refuses"],
)
def test_a_send_orca_refuses_names_the_send_that_types_it_and_is_left_unstamped(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], reply: dict, why: str
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    git.sends["term_comm"] = [reply]
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == [f"demo communicator: not reloaded (orca terminal send: {why}; send it with: orca terminal send --terminal term_comm --text /reload-plugins --enter)"]
    assert len(git.typed_into("term_comm")) == 1
    assert not (state.drive_dir("demo") / install.RELOADS).exists()


def test_a_reload_whose_line_never_shows_while_its_session_sits_idle_is_left_unstamped_for_the_next_install(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    git = fake(["unused"], set())
    git.unreloaded = {"term_comm"}
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    transcript = watch.session_transcript(state.saved("demo")["roles"]["communicator"])
    assert held(git, capsys) == [f"demo communicator: /reload-plugins typed in term_comm, but no Reloaded: line showed in {transcript} while it sat idle 30s"]
    assert git.sleeps == [1] * install.RELOAD_WAIT_SECONDS
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert capsys.readouterr().out.splitlines()[1:] == [f"demo communicator: {handed('demo', 'term_comm')}"]


def test_a_session_whose_claude_code_process_exited_is_typed_nothing_and_left_unstamped(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == ["demo communicator: its Claude Code process exited, so it loads long-running@skills 1.0.3 once it starts again"]
    assert git.orca_calls == []
    assert not (state.drive_dir("demo") / install.RELOADS).exists()


@pytest.mark.parametrize("incident", [False, True])
def test_an_install_types_exit_into_a_session_cci_runs_outside_the_drives_checkout_once_registered_there_so_cci_resumes_it_there(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], registered: list[tuple[str, str, str]], tmux_shown: dict[str, str], incident: bool
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator", folder="/old/durable-agents")
    tmux_shown["list-panes"] = f"{os.getppid()} demo-communicator\n"
    if incident:
        with state.edit("demo") as data:
            data["orders"]["o1"] = state.new_order("the journal fails to open", "him", None, {}, state.stamp()) | {"incident": True}
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == ["demo communicator: /exit typed in term_comm, so cci session run resumes it in the drive's checkout /work"]
    assert sends(git, "term_comm") == [sent("term_comm", "/exit")]
    assert registered == [("demo-communicator", "/work", "demo-communicator")]
    assert stamps("demo") == {"communicator": UPDATED}


def shown_in_orca(alias: str) -> list[str]:
    return ["orca", "terminal", "create", "--worktree", "path:/work", "--title", alias, "--command", f"cci session attach {alias}", "--json"]


def test_an_idle_session_outside_the_drives_checkout_and_outside_cci_is_typed_exit_then_shown_in_the_orca_terminal_attaching_ccis_tmux_session(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], registered: list[tuple[str, str, str]], tmux_shown: dict[str, str]
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator", folder="/old/durable-agents")
    git = fake(["unused"], set())
    git.exited = lambda: tmux_shown.update({"list-panes": f"{os.getppid()} demo-communicator\n"})
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == [
        "demo communicator: /exit typed in term_comm, so cci session run resumes it in the drive's checkout /work under tmux session demo-communicator, shown in Orca terminal term_shown"
    ]
    assert sends(git, "term_comm") == [sent("term_comm", "/exit")]
    assert git.orca_calls[1:] == [shown_in_orca("demo-communicator")]
    assert registered == [("demo-communicator", "/work", "demo-communicator")]
    assert state.saved("demo")["roles"]["communicator"]["terminal"] == "term_shown"


def test_a_session_outside_cci_that_cci_has_not_resumed_in_tmux_after_the_wait_is_shown_by_the_attach_that_starts_its_tmux_session(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], tmux_shown: dict[str, str]
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator", folder="/old/durable-agents")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == [
        "demo communicator: /exit typed in term_comm, so cci session run resumes it in the drive's checkout /work under tmux session demo-communicator, shown in Orca terminal term_shown"
    ]
    assert git.sleeps == [1] * install.RELOAD_WAIT_SECONDS
    assert git.orca_calls[1:] == [shown_in_orca("demo-communicator")]
    assert state.saved("demo")["roles"]["communicator"]["terminal"] == "term_shown"


def test_a_session_already_in_the_drives_checkout_is_only_reloaded(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str], registered: list[tuple[str, str, str]], tmux_shown: dict[str, str]
) -> None:
    start_drive("demo", communicator="term_comm")
    running("demo-communicator")
    tmux_shown["list-panes"] = f"{os.getppid()} demo-communicator\n"
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    capsys.readouterr()
    assert held(git, capsys) == ["demo communicator: reloaded onto long-running@skills 1.0.3 in term_comm"]
    assert sends(git, "term_comm") == [sent("term_comm")]
    assert registered == []


def test_drive_reload_types_reload_plugins_into_every_drive_session_an_install_already_reloaded(fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    running("demo-communicator", "demo-coordinator")
    (state.drive_dir("demo") / install.RELOADS).write_text(json.dumps({seat: {"onto": SEEDED_UPDATE, "at": state.stamp()} for seat in ("communicator", "coordinator")}))
    git = fake(["unused"], set())
    assert cli.main(["reload"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"demo communicator: {handed('demo', 'term_comm')}", f"demo coordinator: {handed('demo', 'term_coord')}"]
    assert git.detached == [["reload", "--seat", "communicator", "--force", "--drive", "demo"], ["reload", "--seat", "coordinator", "--force", "--drive", "demo"]]
    assert held(git, capsys) == [
        "demo communicator: reloaded onto long-running@skills 1.0.2 in term_comm",
        "demo coordinator: reloaded onto long-running@skills 1.0.2 in term_coord",
    ]
    assert sends(git, "term_comm") == [sent("term_comm")]
    assert sends(git, "term_coord") == [sent("term_coord")]
    assert git.calls == []


def test_a_drive_session_with_no_terminal_recorded_is_skipped_with_a_line(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("solo", coordinator=None)
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert git.orca_calls == []
    assert git.detached == []
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "solo coordinator: no terminal recorded, skipped",
    ]


def test_a_session_already_reloaded_onto_the_newest_install_is_not_reloaded_again(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    running("demo-communicator", "demo-coordinator")
    reloads = state.drive_dir("demo") / install.RELOADS
    reloads.write_text(json.dumps({"communicator": {"onto": UPDATED, "at": state.stamp()}, "coordinator": {"onto": SEEDED_UPDATE, "at": state.stamp()}}))
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: already reloaded onto long-running@skills 1.0.3",
        f"demo coordinator: {handed('demo', 'term_coord')}",
    ]
    assert held(git, capsys) == ["demo coordinator: reloaded onto long-running@skills 1.0.3 in term_coord"]
    assert sends(git, "term_comm") == []
    assert stamps("demo") == {"communicator": UPDATED, "coordinator": UPDATED}


def test_an_install_reloads_each_sub_coordinator_beside_both_roles_stamping_each_session(
    fake: Callable[..., FakeGit], running: Callable[..., None], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    with state.edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "demo-iris", "terminal": "term_iris", "cwd": "/work", "started": state.stamp()}
    running("demo-communicator", "demo-coordinator", "demo-iris")
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900, None) == 0
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        f"demo communicator: {handed('demo', 'term_comm')}",
        f"demo coordinator: {handed('demo', 'term_coord')}",
        f"demo sub-coordinator Iris: {handed('demo', 'term_iris')}",
    ]
    assert held(git, capsys)[2] == "demo sub-coordinator Iris: reloaded onto long-running@skills 1.0.3 in term_iris"
    assert sends(git, "term_iris") == [sent("term_iris")]
    assert stamps("demo") == {"communicator": UPDATED, "coordinator": UPDATED, "sub-coordinator Iris": UPDATED}


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


UNBOUND = "Traceback (most recent call last):\nUnboundLocalError: cannot access local variable 'refused' where it is not associated with a value\n"


@pytest.mark.parametrize(("code", "stderr"), [(1, UNBOUND), (0, "")], ids=["a traceback and exit 1", "no refusal and exit 0"])
def test_a_long_running_release_whose_own_refusal_is_not_its_drive_line_and_exit_2_is_refused_before_any_scope_updates(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], code: int, stderr: str
) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.refusal = subprocess.CompletedProcess([], code, "", stderr)
    assert install.main("long-running@skills", SHA, None, 900, None) == 1
    assert git.claude_calls() == []
    assert git.orca_calls == []
    err = capsys.readouterr().err
    assert (
        f"refused long-running--v1.0.3, keeping the release installed now: its own drive status j0 on a copy of the demo drive exited {code}"
        " without printing its `drive:` refusal and exiting 2" in err
    )
    assert stderr.strip() in err


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
    assert git.refusals == [[*git.tried[0][0][:3], "status", "j0", "--drive", "demo"]]
    assert git.calls.index(git.refusals[0]) < git.calls.index(["claude", "plugin", "marketplace", "update", "skills"])
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
        f'drive install: WAIT holds {key} for his card "{HELPER_CARD}" (Install now (recommended), Installs never need me and Not now), asked with drive job ask:'
        " it needs captain-hook 12.121.70, and Captain Hook.app, which runs every hook, runs 12.121.68; run this again once his tap or `drive answer` records his pick"
    )
    with state.edit("demo") as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "ship", [], state.stamp()) | {"questions": [state.new_question("/asks/j1-helper.md", HELPER_CARD, state.stamp())]}
    assert install.main(key, None, None, 900, "demo") == 1
    assert capsys.readouterr().err.startswith(f"drive install: WAIT holds {key} for his card /asks/j1-helper.md: it needs captain-hook 12.121.70")
    assert (git.claude_calls(), RESTART in git.calls, git.helper) == ([], False, "12.121.68")


def test_his_install_now_tap_moves_captain_hook_app_onto_the_release_before_the_plugin_installs(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = needing_the_app(fake, monkeypatch, [{"title": HELPER_CARD, "body": "Install now\nOptions: Install now (recommended) | Installs never need me | Not now"}])
    assert install.main("long-running@skills", SHA, None, 900, "demo") == 0
    assert git.calls.index(RESTART) < git.calls.index(["claude", "plugin", "marketplace", "update", "skills"])
    assert git.helper == "12.121.70"
    assert capsys.readouterr().out.splitlines()[0] == "installed long-running@skills 1.0.3"


@pytest.mark.parametrize(("closed", "installs"), [(state.new_close("answered", "Install now (recommended)", "https://slack/p1"), True), (None, False)])
def test_his_tap_on_the_install_card_approves_the_restart_before_drive_answer_records_it(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, closed: dict | None, installs: bool
) -> None:
    git = needing_the_app(fake, monkeypatch, [])
    with state.edit("demo") as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "ask", [], state.stamp()) | {"questions": [state.new_question("/asks/j1-helper.md", HELPER_CARD, state.stamp(), closed=closed)]}
    assert install.main("long-running@skills", SHA, None, 900, "demo") == (0 if installs else 1)
    assert (RESTART in git.calls, git.helper) == (installs, "12.121.70" if installs else "12.121.68")


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
    assert git.homebrew_calls() == [BREW_TAP, TAP_PULL, upgrade("cc-slack"), BREW_PREFIX, ["/opt/homebrew/bin/cc-slack", "--version"]]
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


HIS_THREAD = "1794595277.364249"


def proving(config: Path, tmp_path: Path, git: FakeGit, exits: int) -> Path:
    (config / "m" / ".claude-plugin").mkdir(parents=True)
    listed = [{"name": "long-running", "source": FOLDER}, {"name": "cc-context", "source": "plugins/cc-context"}]
    (config / "m" / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": listed}))
    script = tmp_path / "proof.sh"
    script.write_text(f'#!/bin/sh\necho "ran on ${{PATH%%:*}}"\nexit {exits}\n')
    script.chmod(0o755)
    start_drive("demo", coordinator=None)
    with state.edit("demo") as data:
        part = "Drive tooling: prove each install"
        data["orders"]["o1"] = state.new_order(part, f"slack:D123/{HIS_THREAD}", ("D123", HIS_THREAD), {part: state.new_part()}, state.stamp()) | {"jobs": ["j1"]}
        data["jobs"]["j1"] = state.new_job("o1", "builder", "prove", [part], state.stamp()) | {"state": "waiting", "said": f"PUSHED {URL}", "prs": [URL], "repro": str(script)}
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(URL, "j1", "o1") | {"state": "merged", "merged_sha": SHA, "title": "Fix: prove each install", "checker": carrying("", {"o1": [part]})}
    return script


def proof_events() -> list[tuple[str, str]]:
    return [(event.get("job") or f"{event['repo']}#{event['number']}", event["change"]) for event, _ in state.read_events("demo") if event["kind"] == "proof"]


def repairing(config: Path, tmp_path: Path, git: FakeGit, exits: int, job: dict) -> Path:
    script = proving(config, tmp_path, git, exits)
    with state.edit("demo") as data:
        del data["prs"]["yasyf/cc-skills#7"]
        data["jobs"]["j1"].update(prs=[], **job)
    return script


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
        "attr": {"close": "o1", "thread": HIS_THREAD},
        "text": "POST Fix: prove each install is live in long-running@skills 1.0.3",
        "body": f"Fixed: Fix: prove each install, {URL}\nLive: long-running@skills 1.0.3\nProof: {checked}\n",
    }
    assert [call for call in git.other_calls() if call[:1] == ["cci"]] == []
    assert proof_events() == [("yasyf/cc-skills#7", proven)]


def test_a_repair_its_own_agent_proves_installs_nothing_takes_done_naming_no_pr_and_closes_its_order_under_the_orders_title(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    git = fake([BUILD], {BUILD})
    script = repairing(config, tmp_path, git, 0, {"state": "running", "said": None})
    kept = state.proof_path("demo", "j1")
    versions = f"long-running {state.VERSION}"
    checked = f"{install.PROVEN} on {versions}: {script} exits 0, its output {kept}"
    proven = f"{checked}; its close report of o1 waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
    assert install.prove("demo", "j1", 900) == f"j1: {proven}"
    assert not [call for call in git.calls if call[:3] == ["claude", "plugin", "update"]]
    data = state.load("demo")
    line = f"DONE j1's repair live: {script} exits 0 on {versions}"
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("done", line)
    assert state.returns("demo", "j1")[0]["body"].strip() == f"{line}\nCHECK: {kept}"
    title = view.order_title(data["orders"]["o1"])
    assert (data["orders"]["o1"]["close_report"]["text"], data["orders"]["o1"]["close_report"]["body"]) == (f"POST {title} is fixed", f"Fixed: {title}\nProof: {checked}\n")
    assert proof_events() == [("j1", proven)]


def handing(config: Path, tmp_path: Path, git: FakeGit, exits: int, job: dict) -> Path:
    script = repairing(config, tmp_path, git, exits, {"goal": "prove Forge-AI/monorepo#9 live", "brief_flags": {"handoff": "Forge-AI/monorepo#9 at fedcba merged: prove it live"}} | job)
    with state.edit("demo") as data:
        data["orders"]["o1"]["jobs"].insert(0, "j0")
        data["jobs"]["j0"] = state.new_job("o1", "builder", "fix", [], state.stamp()) | {"state": "done", "said": f"DONE {PRODUCT} merged", "prs": [PRODUCT]}
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr(PRODUCT, "j0", "o1") | {"state": "merged", "merged_sha": "fedcba", "title": "Fix: the product change", "checker": carrying("", {"o1": data["jobs"]["j1"]["parts"]})}
    return script


def test_a_prover_of_a_merge_outside_yasyf_its_own_agent_proves_installs_nothing_takes_done_naming_the_pr_and_its_release_and_keeps_the_orders_proof(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    git = fake([BUILD], {BUILD})
    script = handing(config, tmp_path, git, 0, {"state": "running", "said": None, "shipped": ["release:platy:Forge-AI/monorepo#9"]})
    kept = state.proof_path("demo", "j1")
    versions = "the platy release of Forge-AI/monorepo#9"
    checked = f"{install.PROVEN} on {versions}: {script} exits 0, its output {kept}"
    proven = f"{checked}; its close report of o1 waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
    assert install.prove("demo", "j1", 900) == f"j1: {proven}"
    assert not [call for call in git.calls if call[:3] == ["claude", "plugin", "update"]]
    data = state.load("demo")
    line = f"DONE {PRODUCT} live: {script} exits 0 on {versions}"
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("done", line)
    assert kept.read_text().startswith("ran on ")
    assert data["orders"]["o1"]["proof"] == str(kept)
    report = data["orders"]["o1"]["close_report"]
    assert (report["text"], report["body"]) == (f"POST Fix: the product change is live in {versions}", f"Fixed: Fix: the product change, {PRODUCT}\nLive: {versions}\nProof: {checked}\n")
    assert proof_events() == [("Forge-AI/monorepo#9", proven)]


def test_a_provers_proof_waiting_on_his_next_word_sets_it_waiting_and_the_watch_proves_it_again(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    script = handing(config, tmp_path, fake([BUILD], {BUILD}), 1, {"state": "running", "said": None})
    script.write_text("#!/bin/sh\necho checked the release\necho WAIT he opens the run page\nexit 1\n")
    install.prove("demo", "j1", 900)
    data = state.load("demo")
    said = f"WAITING {install.PROOF_WAITS}: WAIT he opens the run page"
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"], data["jobs"]["j1"]["proof"]["said"]) == ("waiting", said, said)
    assert install.watch_proves(data, "j1")


@pytest.mark.parametrize("job", [{"state": "running", "said": None}, {"state": "stopped", "said": "HANDOFF its proof waits on his next message"}])
def test_a_repairs_proof_waiting_on_his_next_word_sets_it_waiting_on_a_kept_waiting_line_its_proof_marked(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], job: dict
) -> None:
    git = fake([BUILD], {BUILD})
    script = repairing(config, tmp_path, git, 1, job)
    script.write_text("#!/bin/sh\necho checked the records\necho WAIT he writes in his thread\nexit 1\n")
    kept = state.proof_path("demo", "j1")
    change = f"{install.PROOF_WAITS}: j1's repair: {script} exits 1 on long-running {state.VERSION}, its output {kept}: WAIT he writes in his thread"
    assert install.prove("demo", "j1", 900) == f"j1: {change}"
    job = state.load("demo")["jobs"]["j1"]
    said = f"WAITING {install.PROOF_WAITS}: WAIT he writes in his thread"
    assert (job["state"], job["said"], job["proof"]["said"], job["proof"]["waits"]) == ("waiting", said, said, "WAIT he writes in his thread")
    assert state.kept_return("demo", "j1", job, said)
    assert proof_events() == [("j1", change)]


@pytest.mark.parametrize("source", ["cci:696660", "terminal"])
def test_a_proof_closes_drive_tooling_a_session_opened_done_on_its_proof_with_no_close_report_so_no_line_reaches_his_dm_root(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], source: str
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    with state.edit("demo") as data:
        data["orders"]["o1"].update({"from": source, "thread": None})
    kept = state.proof_path("demo", "j1")
    proven = (
        f"{install.PROVEN} on long-running@skills 1.0.3: {script} exits 0, its output {kept}; o1 closed done on it with no close report,"
        " since a session opened that drive tooling order, so no line reaches him and the dashboard shows it live"
    )
    assert install.prove("demo", "j1", 900) == f"j1: {proven}"
    order = state.load("demo")["orders"]["o1"]
    assert (order["state"], order["proof"], order["close_report"], order["closed_on"]) == ("done", str(kept), None, None)
    assert proof_events() == [("yasyf/cc-skills#7", proven)]


def test_a_proof_keeps_the_close_report_of_drive_tooling_a_session_opened_for_a_teammate_whose_thread_hears_the_fix(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    with state.edit("demo") as data:
        data["orders"]["o1"].update({"from": "cci:696660", "thread": None, "reporters": [state.reporter("slack:C0BUGS1/1794400100.000200")]})
    install.prove("demo", "j1", 900)
    order = state.load("demo")["orders"]["o1"]
    assert (order["state"], order["close_report"]["attr"]) == ("open", {"close": "o1"})


@pytest.mark.parametrize("session", [None, "other-coordinator"])
def test_a_proof_files_drive_calls_naming_no_drive_act_on_the_drive_it_proves_whatever_drives_and_session_this_mac_holds(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, session: str | None
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    start_drive("other", coordinator=None)
    if session:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    else:
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    resolving = 'import sys; sys.path.insert(0, sys.argv[1]); from drive import state; print("resolves", state.resolve())'
    script.write_text(f"#!/bin/sh\nexec python3 -I -c '{resolving}' {Path(state.__file__).parents[1]}\n")
    install.prove("demo", "j1", 900)
    assert state.proof_path("demo", "j1").read_text() == "resolves demo\n"


def test_a_proof_file_runs_on_the_path_it_inherits_behind_the_plugin_bins(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    script.write_text('#!/bin/sh\necho "$PATH"\n')
    install.prove("demo", "j1", 900)
    assert state.proof_path("demo", "j1").read_text() == f"/cache/long-running/1.0.3/bin{os.pathsep}{os.environ['PATH']}\n"


def test_a_proof_hands_its_result_to_the_newer_install_that_saved_meanwhile(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    handed = []
    newest, version = tmp_path / "newest" / "bin" / "drive", state.VERSION
    real, proof_run = subprocess.run, install.proof_run

    def saved_meanwhile(name: str, repro: str, path: str, timeout: int) -> subprocess.CompletedProcess[str]:
        monkeypatch.setattr(state, "VERSION", "0.0.1")
        return proof_run(name, repro, path, timeout)

    def newer(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] != str(newest):
            return real(argv, **kwargs)
        handed.append(argv[1:])
        monkeypatch.setattr(state, "VERSION", version)
        code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capsys.readouterr().out, "")

    monkeypatch.setattr(install, "upgraded", lambda: newest)
    monkeypatch.setattr(install, "proof_run", saved_meanwhile)
    monkeypatch.setattr(install.subprocess, "run", newer)
    assert install.prove("demo", "j1", 900).startswith(f"j1: {install.PROVEN} on long-running@skills 1.0.3: {script} exits 0")
    [argv] = handed
    assert (argv[0], argv[2:], Path(argv[1]).exists()) == ("finish", ["--drive", "demo", "--json"], False)
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


@pytest.mark.parametrize(
    ("proof", "last"),
    [
        ("#!/usr/bin/env -S python3 -I\nimport time\nprint('passed: one')\nprint('passed: two')\ntime.sleep(60)\n", "passed: two"),
        ("#!/bin/sh\necho 'passed: one'\npython3 -I -c \"import time; print('passed: a python -I child'); time.sleep(60)\"\n", "passed: a python -I child"),
    ],
)
def test_a_proof_file_past_its_deadline_keeps_every_line_it_printed_a_python_one_run_with_dash_i_included(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], proof: str, last: str
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {OLD_COMMIT}), 0)
    script.write_text(proof)
    started = time.monotonic()
    install.prove("demo", "j1", 3)
    assert time.monotonic() - started < reads.STOP_GRACE
    kept = state.proof_path("demo", "j1")
    assert state.load("demo")["jobs"]["j1"]["said"] == f"BLOCKED {URL}: {script} exits past drive job prove's deadline on long-running@skills 1.0.2, its output {kept}: it printed no FAIL line"
    assert kept.read_text().startswith(f"passed: one\n{last}\nTraceback")


def test_a_proof_file_cut_off_at_its_deadline_waiting_out_ccxs_rate_limit_floor_runs_again_once_the_floor_lifts(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    git = fake([BUILD], {OLD_COMMIT})
    script = proving(config, tmp_path, git, 0)
    reset = (datetime.now().astimezone() + timedelta(minutes=40)).isoformat(timespec="seconds")
    floor = f"ccx: GitHub rate limit floor: user core has 749 of 5000 left, under the floor of 1000, until later ({reset}); top callers: zsh 188"
    waited = tmp_path / "waited"
    script.write_text(f'#!/bin/sh\nif [ -e {waited} ]; then echo "ok opens this PR"; exit 0; fi\ntouch {waited}\necho "proof.sh: waiting 2400 s: {floor}" >&2\nexec sleep 30\n')
    install.prove("demo", "j1", 2)
    assert 2390 < git.sleeps[-1] <= 2401
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.2"


def test_a_proof_file_gets_its_whole_timeout_however_long_the_install_before_it_took_and_reads_the_unix_second_it_ends_at(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    script.write_text(f'#!/bin/sh\nsleep 2\necho "ends at ${install.PROOF_DEADLINE_ENV}"\n')
    merged_plugins = install.merged_plugins

    def slow(merges: dict[str, str], deadline: float, timeout: int, drive: str) -> list[str]:
        plugins = merged_plugins(merges, deadline, timeout, drive)
        git.now += timeout - 1
        return plugins

    monkeypatch.setattr(install, "merged_plugins", slow)
    started = time.time()
    install.prove("demo", "j1", 60)
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"
    assert started + 59 <= int(state.proof_path("demo", "j1").read_text().removeprefix("ends at ")) <= time.time() + 60


def test_a_proof_proves_the_prs_its_ready_names_while_the_job_also_holds_a_pr_closed_as_superseded(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    superseded = "https://github.com/yasyf/cc-skills/pull/8"
    with state.edit("demo") as data:
        data["jobs"]["j1"]["prs"].append(superseded)
        data["prs"]["yasyf/cc-skills#8"] = state.new_pr(superseded, "j1", "o1") | {"state": "closed"}
    assert install.prove("demo", "j1", 900).startswith(f"j1: {install.PROVEN} on long-running@skills 1.0.3: {script} exits 0")
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"
    assert [key for key, _ in proof_events()] == ["yasyf/cc-skills#7"]


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


def test_a_proof_held_for_his_install_card_takes_no_result_and_waits_on_his_next_word(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git, script = proving_hooks(config, tmp_path, fake, monkeypatch, "12.121.70")
    install.prove("demo", "j1", 900)
    held = f'WAIT holds captain-hook@captain-hook for his card "{install.HELPER_ASKED.format(release="12.121.71")}"'
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"], job["proof"]["said"]) == ("waiting", f"PUSHED {HOOKS_URL}", f"PUSHED {HOOKS_URL}")
    assert job["proof"]["waits"].startswith(held)
    [(key, change)] = proof_events()
    assert (key, change.startswith(f"{install.PROOF_WAITS}: {HOOKS_URL}: not installed, so {script} did not run: {held}")) == ("yasyf/captain-hook#709", True)
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


def test_a_product_merge_on_a_build_order_of_several_parts_with_no_reader_takes_done_and_adds_one_builder_starting_at_the_merge_on_no_release(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    product, page = "https://github.com/Forge-AI/monorepo/pull/9", "the dashboard's run page"
    shipped, carried = "readers call ssql-blobstore", "the blob store answers reads"
    with state.edit("demo") as data:
        data["orders"]["o1"] |= {"kind": "build", "parts": {shipped: state.new_part(), "writers call ssql-blobstore": state.new_part()}}
        data["orders"]["o2"] = state.new_order(carried, "slack", None, {carried: state.new_part()}, state.stamp()) | {"jobs": ["j1"]}
        flags = {"native": page, "interactions": "open a run's page", "done_when": "the PR is green on dev"}
        data["jobs"]["j1"].update(said=f"READY {product}", prs=[product], repro=None, parts=[shipped], carries={"o2": [carried]}, brief_flags=flags, session="s-1")
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr(product, "j1", "o1") | {"state": "merged", "merged_sha": "fedcba9876543210", "title": "Fix: the page", "checker": carrying("", {"o1": [shipped], "o2": [carried]})}
        reach = {"Forge-AI/monorepo#9": {"components": ["api", "sandsql", "web"], "touched": ["api", "sandsql"], "dev": [], "releases": ["api", "sandsql"]}, "Forge-AI/monorepo#10": {"components": ["web"], "touched": ["web"], "dev": [], "releases": ["web"]}}
        install.merged_done("demo", data, "j1")
        builder_id = install.add_prover(data, "j1", reach, {"o1": [shipped], "o2": [carried]}, set())
    merged = "Forge-AI/monorepo#9 at fedcba987654"
    data = state.load("demo")
    builder = data["jobs"][builder_id]
    assert (builder_id, builder["kind"], builder["state"], builder["goal"], builder["session"]) == ("j2", "builder", "running", "prove Forge-AI/monorepo#9 live", "s-1")
    assert (builder["parts"], builder["carries"], builder["builds_nothing"]) == ([shipped], {"o2": [carried]}, True)
    assert builder["waits"] == []
    assert builder["brief_flags"]["handoff"] == f"{merged} merged: install it and prove it live by the INSTALL: and PROVE: lines of j1's return before its DONE, in reports/j1.md"
    assert builder["brief_flags"]["done_when"].startswith(f"drive job prove {builder_id} ")
    assert (builder["brief_flags"]["native"], builder["brief_flags"]["interactions"]) == (page, "open a run's page")
    assert ("j2" in data["orders"]["o1"]["jobs"], "j2" in data["orders"]["o2"]["jobs"]) == (True, True)
    assert (data["jobs"]["j1"]["state"], data["jobs"]["j1"]["said"]) == ("done", f"DONE {product} merged: {merged}")
    assert (data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (None, None)


def test_a_product_merge_of_paths_on_go_cis_dev_list_waits_on_no_release_owes_no_staging_pass_on_a_target_only_those_paths_touch_and_hands_its_prover_those_paths(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    product = "https://github.com/Forge-AI/monorepo/pull/9"
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(said=f"READY {product}", prs=[product], repro=None)
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr(product, "j1", "o1") | {"state": "merged", "merged_sha": "fedcba9876543210", "title": "Fix: tools/ci fetch_dev"}
        reach = {"Forge-AI/monorepo#9": {"components": ["api", "devbox", "platy"], "touched": ["devbox", "platy"], "dev": ["tools/ci", "go/ci/internal/devbox/box.go"], "releases": ["devbox"]}}
        install.merged_done("demo", data, "j1")
        builder_id = install.add_prover(data, "j1", reach, {"o1": data["jobs"]["j1"]["parts"]}, {"platy"})
    builder = state.load("demo")["jobs"][builder_id]
    assert builder["waits"] == []
    assert builder["brief_flags"]["handoff"] == (
        "Forge-AI/monorepo#9 at fedcba987654 merged: install it and prove it live by the INSTALL: and PROVE: lines of j1's return before its DONE, in reports/j1.md;"
        " Forge-AI/monorepo#9 changes tools/ci and 1 more on go/ci's dev list, which a checkout of dev holding fedcba987654 runs"
    )


def test_a_second_hand_over_of_the_same_prs_goes_to_their_one_prover_widening_it_while_unstarted_and_giving_it_the_parts_alone_once_started(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    product, carried = "https://github.com/Forge-AI/monorepo/pull/9", "ship the dashboard"
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(said=f"READY {product}", prs=[product], repro=None)
        data["prs"]["Forge-AI/monorepo#9"] = state.new_pr(product, "j1", "o1") | {"state": "merged", "merged_sha": "fedcba9876543210", "title": "Fix: the page"}
        install.merged_done("demo", data, "j1")
        reach, parts = {"Forge-AI/monorepo#9": {"components": ["api"], "touched": ["api"], "dev": [], "releases": ["api"]}}, {"o1": data["jobs"]["j1"]["parts"]}
        unstarted = install.add_prover(data, "j1", reach, parts, set())
        widened = install.add_prover(data, "j1", reach, parts, set())
        data["jobs"][unstarted]["agent"] = "a-1"
        data["orders"]["o2"] = state.new_order(carried, "slack", None, {carried: state.new_part()}, state.stamp())
        started = install.add_prover(data, "j1", reach, {"o2": [carried]}, set())
    data = state.load("demo")
    assert (unstarted, widened, started, list(data["jobs"])) == ("j2", "j2", "j2", ["j1", "j2"])
    assert (data["jobs"]["j2"]["waits"], data["jobs"]["j2"]["carries"]) == ([], {"o2": [carried]})


def test_a_product_merge_whose_paths_reach_no_release_says_its_prover_in_one_line_briefing_it_to_start_now(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
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
        for job_id in ("j2", "j1"):
            install.merged_done("demo", data, job_id)
        asking = install.add_prover(data, "j2", {}, {"o1": data["jobs"]["j2"]["parts"]}, set())
        merging = install.add_prover(data, "j1", {}, {"o1": data["jobs"]["j1"]["parts"]}, set())
    asked, merged = install.merged_said("demo", asking, "added"), install.merged_said("demo", merging, "added")
    data = state.load("demo")
    assert (asking, merging, data["jobs"][asking]["waits"], data["jobs"][merging]["waits"]) == ("j3", "j4", [], [])
    started = "waits on no release: start its agent now, run_in_background, on the prompt"
    assert asked == f'JOB j3 o1 added, proves Forge-AI/monorepo#10, {started} "Read your brief first: {data["jobs"]["j3"]["brief"]}"'
    assert merged == f'JOB j4 o1 added, proves Forge-AI/monorepo#9, {started} "Read your brief first: {data["jobs"]["j4"]["brief"]}"'
    assert "Handoff: Forge-AI/monorepo#10 at 0123456789ab merged: install it and prove it live" in Path(data["jobs"]["j3"]["brief"]).read_text()
    assert [data["jobs"][job_id]["state"] for job_id in ("j1", "j2", "j3", "j4")] == ["done", "done", "running", "running"]
    assert (data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (None, None)


def test_a_merge_with_no_staging_test_reaching_a_target_staging_runs_gets_a_prover_starting_now_on_its_after_merge_pass_and_none_waits_on_a_release(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    tested, untested = "https://github.com/Forge-AI/monorepo/pull/9", "https://github.com/Forge-AI/monorepo/pull/10"
    with state.edit("demo") as data:
        order = data["orders"]["o1"]
        order["jobs"].append("j2")
        data["jobs"]["j2"] = state.new_job("o1", "builder", "show each step", [*order["parts"]], state.stamp()) | {"state": "waiting", "said": f"READY {untested}", "prs": [untested], "brief_flags": {"native": "none"}}
        staged = state.STAGING_TEST.format(number=9)
        order["parts"][staged] = state.new_part() | {"posted": "https://buildkite.com/forge/release/builds/16164", "posted_by": "j1"}
        data["jobs"]["j1"].update(said=f"READY {tested}", prs=[tested], repro=None, brief_flags={"native": "none"}, parts=[*data["jobs"]["j1"]["parts"], staged])
        for url, job_id, sha in ((tested, "j1", "fedcba9876543210"), (untested, "j2", "0123456789abcdef")):
            data["prs"][state.pr_key(url)] = state.new_pr(url, job_id, "o1") | {"state": "merged", "merged_sha": sha, "title": "Fix: the page"}
        reach = {key: {"components": ["api", "web"], "touched": ["api", "web"], "dev": [], "releases": ["api", "web"]} for key in ("Forge-AI/monorepo#9", "Forge-AI/monorepo#10")}
        for job_id in ("j1", "j2"):
            install.merged_done("demo", data, job_id)
        releasing = install.add_prover(data, "j1", reach, {"o1": [part for part in data["jobs"]["j1"]["parts"] if part != staged]}, {"api"})
        passing = install.add_prover(data, "j2", reach, {"o1": data["jobs"]["j2"]["parts"]}, {"api"})
    released, passed = install.merged_said("demo", releasing, "added"), install.merged_said("demo", passing, "added")
    data = state.load("demo")
    assert (releasing, passing, data["jobs"]["j3"]["waits"], data["jobs"]["j4"]["waits"]) == ("j3", "j4", [], [])
    assert data["jobs"]["j4"]["brief_flags"]["handoff"] == (
        f"Forge-AI/monorepo#10 at 0123456789ab merged with no staging test before its merge: run its after-merge staging pass first, by the merge row of agents/builder.md,"
        f" its cases from the bodies of {untested}, the PROVE: line and REPRO: of j2's return in reports/j2.md and the words of o1,"
        f" waiting on staging:api/plat-usw2-staging@0123456789abcdef while staging serves no build holding its merge;"
        f" then install it and prove it live by the INSTALL: and PROVE: lines of j2's return before its DONE, in reports/j2.md"
    )
    assert released == f'JOB j3 o1 added, proves Forge-AI/monorepo#9, waits on no release: start its agent now, run_in_background, on the prompt "Read your brief first: {data["jobs"]["j3"]["brief"]}"'
    assert passed == (
        "JOB j4 o1 added, proves Forge-AI/monorepo#10, owes its after-merge staging pass first:"
        f' start its agent now, run_in_background, on the prompt "Read your brief first: {data["jobs"]["j4"]["brief"]}"'
    )
    assert [data["jobs"][job_id]["state"] for job_id in ("j3", "j4")] == ["running", "running"]


def test_an_incidents_fix_proven_live_keeps_no_close_and_leaves_its_close_to_the_watchs_read(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    with state.edit("demo") as data:
        data["orders"]["o1"].update(incident=True, parts={"fix": state.new_part()})
        data["jobs"]["j1"]["parts"] = ["fix"]
    install.prove("demo", "j1", 900)
    order = state.load("demo")["orders"]["o1"]
    assert (order["close_report"], order["proof"], order["recovered"]) == (None, None, None)
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROVEN} on long-running@skills 1.0.3: {script} exits 0, its output {state.proof_path('demo', 'j1')}")]


def test_a_proof_file_that_fails_takes_blocked_and_closes_nothing(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 1)
    install.prove("demo", "j1", 900)
    blocked = f"BLOCKED {URL}: {script} exits 1 on long-running@skills 1.0.3, its output {state.proof_path('demo', 'j1')}: it printed no FAIL line"
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("failed", blocked)
    assert state.load("demo")["orders"]["o1"]["proof"] is None
    assert not [call for call in git.other_calls() if call[0] == "cci"]
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROOF_FAILED}: {blocked.removeprefix('BLOCKED ')}")]


@pytest.mark.parametrize(
    ("printed", "said"),
    [
        (
            "drive pr ci exits 1\\nFAIL long-running CI https://github.com/yasyf/cc-skills/actions/runs/1 completed failure\\ncodex release tag https://github.com/yasyf/cc-skills/actions/runs/2 completed success",
            "FAIL long-running CI https://github.com/yasyf/cc-skills/actions/runs/1 completed failure",
        ),
        ("FAIL: the live dashboard runs 1.0.2\\n[agent-browser] launched browser", "FAIL: the live dashboard runs 1.0.2"),
        ("FAIL: the live dashboard runs 1.0.2\\nWAIT S1 no exchange since the install: reply in a DM thread, then run this again", "FAIL: the live dashboard runs 1.0.2"),
        ("ok A1 the probe runs\\n/p.sh: line 3: 4242 Killed: 9 env ANTHROPIC_API_KEY=sk-ant-demo probe --live", "it printed no FAIL line"),
    ],
)
def test_a_failed_proof_is_said_by_its_last_fail_line_whatever_it_printed_after_it_and_never_by_a_line_of_the_shells(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], printed: str, said: str
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 1)
    script.write_text(f"#!/bin/sh\nprintf '{printed}\\n'\nexit 1\n")
    install.prove("demo", "j1", 900)
    blocked = f"BLOCKED {URL}: {script} exits 1 on long-running@skills 1.0.3, its output {state.proof_path('demo', 'j1')}: {said}"
    assert state.load("demo")["jobs"]["j1"]["said"] == blocked
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROOF_FAILED}: {blocked.removeprefix('BLOCKED ')}")]


def test_a_proof_ending_on_wait_with_no_fail_line_takes_no_result_and_marks_its_proof_waiting_on_his_next_word(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit]
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 2)
    wait = "WAIT S1 no exchange since the install: reply in a DM thread within 60s of a handler's post there, then run this again"
    script.write_text(f"#!/bin/sh\necho 'ok A1 cc-slack dm-status takes --drive'\necho \"{wait}\"\nexit 2\n")
    marker = {"pid": 4242, "at": state.stamp(), "said": f"PUSHED {URL}", "version": state.VERSION}
    with state.edit("demo") as data:
        data["jobs"]["j1"]["proof"] = marker
    kept = state.proof_path("demo", "j1")
    waits = f"{install.PROOF_WAITS}: {URL}: {script} exits 2 on long-running@skills 1.0.3, its output {kept}: {wait}"
    assert install.prove("demo", "j1", 900) == f"j1: {waits}"
    data = state.load("demo")
    job = data["jobs"]["j1"]
    assert (job["state"], job["said"], job["proof"]) == ("waiting", f"PUSHED {URL}", marker | {"waits": wait})
    assert (data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (None, None)
    assert kept.read_text() == f"ok A1 cc-slack dm-status takes --drive\n{wait}\n"
    assert proof_events() == [("yasyf/cc-skills#7", waits)]


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
            f"BLOCKED {URL}: {script} exits 0 but falls short of its surface on long-running@skills 1.0.3, its output {state.proof_path('demo', 'j1')}: its brief names 2 user stories"
            " and its proof shows 0 of them"
        )


J4506_OUTPUT = """install /Users/yasyf/.local/share/claude-plugins/cache/skills/long-running/1.0.978/lib
ok every open card's kept ASK file redraws (230 of 230)
ok durable-agents:j961-664395 is an open question with a kept ASK file
ok durable-agents:j961-664395 redraws from /Users/yasyf/.local/state/long-running/durable-agents/asks/j961-83c474b7.md: Where should a Sprite lane keep its node_modules store, Rust target and sccache cache, given its 12 GiB home volume?
ok the post hook passes durable-agents:j961-664395's card with no text beside it
"""
REDRAW = "drive cards redraw --waits durable-agents:j961-664395 --post"


@pytest.mark.parametrize(
    ("exits", "printed", "taken"),
    [
        (0, "durable-agents:j961-664395 redrawn at https://acme.slack.com/archives/D123/p1794300000000100\n", "done"),
        (2, "drive: these cards did not redraw:\n- durable-agents:j961-664395: not one of his open cards\n", "failed"),
    ],
)
def test_a_proof_whose_brief_names_a_step_after_the_install_takes_done_only_once_drive_job_prove_ran_that_step_and_it_exited_0(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, exits: int, printed: str, taken: str
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{J4506_OUTPUT}EOF\n")
    with state.edit("demo") as data:
        data["jobs"]["j1"]["brief_flags"] = {"after": [REDRAW, "drive job resume j9"]}
    ran: list[tuple[list[str], str]] = []

    def run(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] != "drive":
            return git.run(cmd, **options)
        ran.append((cmd, options["env"][state.DRIVE_ENV]))
        return subprocess.CompletedProcess(cmd, exits, printed, "")

    monkeypatch.setattr(install.subprocess, "run", run)
    install.prove("demo", "j1", 900)
    job = state.load("demo")["jobs"]["j1"]
    kept = state.proof_path("demo", "j1")
    assert job["state"] == taken
    assert kept.read_text() == f"{J4506_OUTPUT}$ {REDRAW}\n{printed}" + ("$ drive job resume j9\n" + printed if taken == "done" else "")
    if taken == "done":
        assert (ran, job["said"]) == ([(REDRAW.split(), "demo"), (["drive", "job", "resume", "j9"], "demo")], f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3")
    else:
        assert ran == [(REDRAW.split(), "demo")]
        assert job["said"] == (
            f"BLOCKED {URL}: {script} exits 0, then its After install: {REDRAW} exits 2 on long-running@skills 1.0.3, its output {kept}:"
            " - durable-agents:j961-664395: not one of his open cards"
        )


def test_a_proof_whose_after_install_step_runs_past_its_timeout_takes_blocked_and_runs_no_step_after_it(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    with state.edit("demo") as data:
        data["jobs"]["j1"]["brief_flags"] = {"after": [REDRAW, "drive job resume j9"]}
    ran: list[list[str]] = []

    def run(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] != "drive":
            return git.run(cmd, **options)
        ran.append(cmd)
        raise subprocess.TimeoutExpired(cmd, options["timeout"])

    monkeypatch.setattr(install.subprocess, "run", run)
    install.prove("demo", "j1", 900)
    assert ran == [REDRAW.split()]
    assert state.load("demo")["jobs"]["j1"]["said"] == (
        f"BLOCKED {URL}: {script} exits 0, then its After install: {REDRAW} runs past 900 s on long-running@skills 1.0.3, its output"
        f" {state.proof_path('demo', 'j1')}: it was stopped there"
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
        merged = data["prs"]["yasyf/cc-skills#7"]
        merged.update(state="merged", merged_sha=SHA, checker=carrying(merged["head"], {"o1": data["jobs"]["j1"]["parts"]}))
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
    script.write_text(f"#!/bin/sh\ngrep -qx /cache/long-running/1.0.3/lib/drive {shown} || {{ echo 'the dashboard does not run the installed long-running'; exit 1; }}\necho 'the dashboard runs the installed long-running'\n")
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
    assert state.load("demo")["jobs"]["j1"]["said"] == f"BLOCKED {URL}: {script} exits unrun on long-running@skills 1.0.3, its output {state.proof_path('demo', 'j1')}: {stale}"
    assert state.proof_path("demo", "j1").read_text() == stale
    assert state.load("demo")["orders"]["o1"]["proof"] is None


@pytest.mark.parametrize(("lands", "runs"), [("while the dashboard hands over", 1), ("while the proof file fails", 2), ("while the proof file fails at the end of its timeout", 2)])
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
    proof_run = install.proof_run

    def land() -> None:
        plugins = json.loads((config / "plugins" / "installed_plugins.json").read_text())["plugins"]
        newer = {"scope": "user", "version": "1.0.4", "lastUpdated": "2026-11-10T10:00:00.000Z", "installPath": "/cache/long-running/1.0.4", "gitCommitSha": BUILD}
        git.write_installed(plugins | {"long-running@skills": [newer]})

    def run(name: str, repro: str, path: str, timeout: int) -> subprocess.CompletedProcess[str]:
        done = proof_run(name, repro, path, timeout)
        ran.append(done.stdout)
        if lands.startswith("while the proof file fails"):
            land()
        if lands.endswith("at the end of its timeout"):
            git.now += timeout
        return done

    monkeypatch.setattr(install, "proof_run", run)
    monkeypatch.setattr(dashboard, "serving", lambda name, port: land() if lands == "while the dashboard hands over" else None)
    install.prove("demo", "j1", 900)
    assert ran[-1:] == ["ran on /cache/long-running/1.0.4/bin\n"] and len(ran) == runs
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.4"
    assert state.proof_path("demo", "j1").read_text() == "ran on /cache/long-running/1.0.4/bin\n"


MERGED_BEFORE = "291e838bc0ffee"
MERGED_AFTER = "e16c9ce5a1b2c3"
FIXED_AFTER = "38425f1b6d4e5f"


def ci(run_id: int, name: str, created: str, status: str = "in_progress", conclusion: str | None = None, head: str = SHA) -> dict:
    workflow = {"long-running CI": 366540480, "Guides": 310890487}[name]
    return {
        "id": run_id,
        "workflow_id": workflow,
        "name": name,
        "head_sha": head,
        "created_at": created,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def on_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, triggered: list[dict], polls: list[list[dict]]) -> Path:
    shown = tmp_path / "runs"
    answers = chain(polls, repeat(polls[-1]))

    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        if cmd[-1] == ".default_branch":
            return "main\n"
        if cmd[1:3] == ["pr", "view"]:
            return f"{SHA}\n"
        if cmd[2].endswith("/status"):
            return ""
        if f"/compare/{SHA}..." in cmd[2]:
            return "behind\n" if cmd[2].endswith(MERGED_BEFORE) else "ahead\n"
        runs = triggered if f"&head_sha={SHA}" in cmd[3] else [run for run in next(answers) if f"/actions/workflows/{run['workflow_id']}/runs?" in cmd[3]]
        shown.write_text("".join(f"{run['id']} {run['status']} {run['conclusion']}\n" for run in runs))
        return "".join(json.dumps(run) + "\n" for run in runs)

    monkeypatch.setattr(github, "read", read)
    return shown


def test_a_proof_failing_while_its_merges_ci_runs_follows_that_run_past_the_deadline_and_runs_again_once_it_completed(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    git.delivered_every = 3600
    script = proving(config, tmp_path, git, 0)
    guides = ci(43375546815, "Guides", "2026-11-13T05:59:05Z", "completed", "success")
    running = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z")
    shown = on_actions(monkeypatch, tmp_path, [guides, running], [[guides, running], [guides, running], [guides, running | {"status": "completed", "conclusion": "success"}]])
    script.write_text(f"#!/bin/sh\necho ran >> {tmp_path / 'ran'}\ngrep -x '43375546307 completed success' {shown}\n")
    install.prove("demo", "j1", 60)
    assert (git.heard, git.sleeps) == ([f"{REPO} ci", f"{REPO} ci"], [])
    assert (tmp_path / "ran").read_text() == "ran\nran\n"
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"


def test_a_merge_whose_own_run_its_concurrency_group_cancelled_waits_on_the_run_of_the_push_that_superseded_it(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    cancelled = ci(43370495715, "long-running CI", "2026-11-13T05:12:31Z", "completed", "cancelled")
    superseding = ci(43370536229, "long-running CI", "2026-11-13T05:12:53Z", head=MERGED_AFTER)
    shown = on_actions(monkeypatch, tmp_path, [cancelled], [[superseding, cancelled], [superseding | {"status": "completed", "conclusion": "success"}, cancelled]])
    script.write_text(f"#!/bin/sh\ngrep -x '43370536229 completed success' {shown}\n")
    install.prove("demo", "j1", 900)
    assert (git.heard, git.sleeps) == ([f"{REPO} ci"], [])
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"


def test_a_proof_failing_while_a_run_holding_its_merge_failed_on_another_merge_waits_through_red_main_until_a_later_run_passes(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    cancelled = ci(43464105662, "long-running CI", "2026-11-13T16:34:41Z", "completed", "cancelled")
    red = ci(43464306223, "long-running CI", "2026-11-13T16:35:50Z", "completed", "failure", head=MERGED_AFTER)
    green = ci(43465943058, "long-running CI", "2026-11-13T16:46:12Z", "completed", "success", head=FIXED_AFTER)
    shown = on_actions(monkeypatch, tmp_path, [cancelled], [[red, cancelled], [green, red, cancelled]])
    script.write_text(f"#!/bin/sh\ngrep -x '43465943058 completed success' {shown}\n")
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
        reads.append(cmd[2])
        if cmd[2].endswith("/status"):
            return ""
        return "".join(json.dumps(run) + "\n" for run in ([running] if f"&head_sha={SHA}" in cmd[3] else next(polls)))

    monkeypatch.setattr(github, "read", read)
    start = install.clock()
    assert install.await_ci({f"{REPO}#7": SHA})
    assert (git.now - start, len(git.heard), len(reads), git.sleeps) == (720, 4, 3 * 5, [])
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
        if cmd[2].endswith("/status"):
            return ""
        if isinstance(answer := next(answers), Exception):
            raise answer
        return "".join(json.dumps(run) + "\n" for run in answer)

    monkeypatch.setattr(github, "read", read)
    assert not install.await_ci({f"{REPO}#7": SHA})
    assert (git.sleeps, git.heard) == ([install.POLL_SECONDS], [])


def test_drive_pr_ci_passes_a_merge_by_a_later_run_holding_it_past_its_cancelled_own_and_a_red_one_never_by_a_run_of_a_push_merged_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    earlier = ci(43384583289, "long-running CI", "2026-11-13T07:16:37Z", "completed", "failure", head=MERGED_BEFORE)
    cancelled = ci(43384590795, "long-running CI", "2026-11-13T07:16:41Z", "completed", "cancelled")
    created_late = ci(43384590801, "long-running CI", "2026-11-13T07:16:41Z", "completed", "success", head=MERGED_BEFORE)
    red = ci(43384700412, "long-running CI", "2026-11-13T07:17:30Z", "completed", "failure", head=MERGED_AFTER)
    green = ci(43384815193, "long-running CI", "2026-11-13T07:18:31Z", "completed", "success", head=FIXED_AFTER)
    guides = ci(43384591254, "Guides", "2026-11-13T07:16:42Z", "completed", "success")
    on_actions(monkeypatch, tmp_path, [cancelled, guides], [[green, red, guides, created_late, cancelled, earlier]])
    assert cli.main(["pr", "ci", URL]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"long-running CI https://github.com/{REPO}/actions/runs/43384815193 completed success",
        f"Guides https://github.com/{REPO}/actions/runs/43384591254 completed success",
        f"yasyf/cc-skills#7 merged at {SHA[:12]}: passed",
    ]


def test_drive_pr_ci_exits_1_while_a_workflow_of_the_merge_has_no_passing_run_holding_it_naming_its_newest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = ci(43384583289, "long-running CI", "2026-11-13T07:16:37Z", "completed", "failure")
    running = ci(43384815193, "long-running CI", "2026-11-13T07:18:31Z", head=MERGED_AFTER)
    cancelled = ci(43384591254, "Guides", "2026-11-13T07:16:42Z", "completed", "cancelled")
    on_actions(monkeypatch, tmp_path, [failed, cancelled], [[running, cancelled, failed]])
    assert cli.main(["pr", "ci", URL]) == 1
    newest, unrun = f"long-running CI https://github.com/{REPO}/actions/runs/43384815193 in_progress", f"Guides https://github.com/{REPO}/actions/runs/43384591254 completed cancelled"
    assert capsys.readouterr().out.splitlines() == [newest, unrun, f"yasyf/cc-skills#7 merged at {SHA[:12]}: not passed: {newest}; {unrun}"]


STACK = {"56621": "6d44e8728ec14e65cb2c72daa980b46210acc4f4", "56685": "5f986a086f4d4520cda6dd92c1c97f646ba3c33e"}
PUSH_HEAD = "15a282a500228429a32023e661baf2ee35cd2c01"
NEXT_HEAD = "024e77d4156a4f36198b8a530ed7b5a93889066c"
DEV = [*STACK.values(), PUSH_HEAD, NEXT_HEAD]
REVIEWERS = [
    {"id": 43711036649, "workflow_id": 201, "name": "PR Reviewer Commands", "event": "issue_comment", "head_sha": PUSH_HEAD, "created_at": "2026-11-15T11:53:02Z", "status": "completed", "conclusion": "skipped"},
    {"id": 43711056525, "workflow_id": 202, "name": "PR Review Trigger", "event": "check_run", "head_sha": PUSH_HEAD, "created_at": "2026-11-15T11:53:10Z", "status": "completed", "conclusion": "success"},
    {"id": 43711056530, "workflow_id": 203, "name": "FDE Docs", "event": "push", "head_sha": PUSH_HEAD, "created_at": "2026-11-15T11:52:44Z", "status": "completed", "conclusion": "skipped"},
]


def buildkite(build: int, state: str) -> dict:
    return {"context": "buildkite/test", "state": state, "target_url": f"https://buildkite.com/forge/test/builds/{build}"}


def on_dev(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, list[dict]]) -> None:
    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        if cmd[1:3] == ["pr", "view"]:
            return f"{STACK[cmd[3]]}\n"
        if cmd[2].endswith("/status"):
            return "".join(json.dumps(status) + "\n" for status in statuses.get(cmd[2].split("/")[-2], []))
        if compared := re.search(r"/compare/(\w+)\.\.\.(\w+)$", cmd[2]):
            base, head = compared.groups()
            if cmd[-1] == ".status":
                return "ahead\n" if DEV.index(head) > DEV.index(base) else "behind\n"
            return "".join(f"{commit}\n" for commit in DEV[DEV.index(base) + 1 :])
        asked = {field: values[0] for field, values in parse_qs(urlsplit(cmd[3]).query).items() if field in ("head_sha", "event")}
        workflow = re.search(r"/workflows/(\d+)/runs", cmd[3])
        runs = [run for run in REVIEWERS if all(run[field] == value for field, value in asked.items()) and (not workflow or run["workflow_id"] == int(workflow[1]))]
        return "".join(json.dumps(run) + "\n" for run in runs)

    monkeypatch.setattr(github, "read", read)
    monkeypatch.setattr(github, "default_branch", lambda repo: "dev")


@pytest.mark.parametrize("number", list(STACK))
def test_drive_pr_ci_passes_each_pr_a_push_carried_below_its_head_on_the_buildkite_status_of_that_head(
    number: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    on_dev(monkeypatch, {PUSH_HEAD: [buildkite(132334, "success")], NEXT_HEAD: [buildkite(132341, "failure")]})
    assert cli.main(["pr", "ci", f"https://github.com/Forge-AI/monorepo/pull/{number}"]) == 0
    assert capsys.readouterr().out.splitlines() == ["buildkite/test https://buildkite.com/forge/test/builds/132334 success", f"Forge-AI/monorepo#{number} merged at {STACK[number][:12]}: passed"]


def test_drive_pr_ci_never_passes_a_merge_on_the_reviewer_and_skipped_runs_its_push_head_carries_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    on_dev(monkeypatch, {})
    assert cli.main(["pr", "ci", "https://github.com/Forge-AI/monorepo/pull/56621"]) == 1
    unreported = "no CI reported on 6d44e8728ec1 or any commit after it on dev"
    assert capsys.readouterr().out.splitlines() == [unreported, f"Forge-AI/monorepo#56621 merged at 6d44e8728ec1: not passed: {unreported}"]


@pytest.mark.parametrize(("later", "code", "verdict"), [("success", 0, "passed"), ("pending", 1, "not passed: buildkite/test https://buildkite.com/forge/test/builds/132341 pending")])
def test_drive_pr_ci_passes_a_merge_its_push_heads_build_failed_once_a_later_build_holding_it_passed_naming_that_one_else_the_newest(
    later: str, code: int, verdict: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    on_dev(monkeypatch, {PUSH_HEAD: [buildkite(132334, "failure")], NEXT_HEAD: [buildkite(132341, later)]})
    assert cli.main(["pr", "ci", "https://github.com/Forge-AI/monorepo/pull/56685"]) == code
    assert capsys.readouterr().out.splitlines() == [f"buildkite/test https://buildkite.com/forge/test/builds/132341 {later}", f"Forge-AI/monorepo#56685 merged at 5f986a086f4d: {verdict}"]


def test_a_proof_of_a_merge_its_push_carried_below_its_head_waits_on_the_ci_of_that_head(
    fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake([BUILD], {BUILD})
    running = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z", head=MERGED_AFTER)
    polls = iter([[running], [running | {"status": "completed", "conclusion": "success"}]])

    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str:
        if cmd[-1] == ".default_branch":
            return "main\n"
        if cmd[2].endswith("/status"):
            return ""
        if cmd[-1] == ".commits[].sha":
            return f"{MERGED_AFTER}\n"
        if cmd[-1] == ".status":
            return "ahead\n"
        if "/workflows/" in cmd[3]:
            return "".join(json.dumps(run) + "\n" for run in next(polls))
        return json.dumps(running) + "\n" if f"&head_sha={MERGED_AFTER}" in cmd[3] else ""

    monkeypatch.setattr(github, "read", read)
    assert install.await_ci({f"{REPO}#7": SHA})
    assert git.heard == [f"{REPO} ci"]
    assert capsys.readouterr().err.splitlines() == [f"drive job prove: waiting for {REPO}#7's CI at {SHA[:12]} to pass: long-running CI https://github.com/{REPO}/actions/runs/43375546307 in_progress"]


def test_a_proof_file_passing_while_its_merges_ci_still_runs_takes_done_right_after_the_install_without_waiting_on_that_ci(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    running = ci(43375546307, "long-running CI", "2026-11-13T05:59:04Z")
    on_actions(monkeypatch, tmp_path, [running], [[running]])
    install.prove("demo", "j1", 60)
    assert (git.heard, git.sleeps) == ([], [])
    assert state.load("demo")["jobs"]["j1"]["said"] == f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3"


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


def test_a_proof_file_exiting_0_that_prints_nothing_takes_blocked_and_keeps_no_close_report(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    script.write_text("#!/bin/sh\nexit 0\n")
    install.prove("demo", "j1", 900)
    blocked = f"BLOCKED {URL}: {script} exits 0 but prints nothing on long-running@skills 1.0.3, its output {state.proof_path('demo', 'j1')}: a proof file prints a line for each check it passed, so one printing nothing proves nothing"
    data = state.load("demo")
    assert (data["jobs"]["j1"]["said"], data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (blocked, None, None)
    assert proof_events() == [("yasyf/cc-skills#7", f"{install.PROOF_FAILED}: {blocked.removeprefix('BLOCKED ')}")]


def test_a_proof_file_exiting_0_that_prints_the_demos_placeholder_slack_link_takes_blocked_and_keeps_no_close_report(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], unopened_links: dict[str, str]
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    stand_in = "https://example.slack.com/archives/D0PROOF/p1794600000000100"
    unopened_links[stand_in] = "it is on example.slack.com, not his Slack workspace acme.slack.com"
    script.write_text(f"#!/bin/sh\necho 'ask 1 posts one card line {stand_in}'\n")
    install.prove("demo", "j1", 900)
    blocked = (
        f"BLOCKED {URL}: {script} exits 0 but names a link that does not open on long-running@skills 1.0.3, its output {state.proof_path('demo', 'j1')}:"
        f" {stand_in} does not open: it is on example.slack.com, not his Slack workspace acme.slack.com: a proof file prints the live post"
        " or picture it read back, never a stand-in"
    )
    data = state.load("demo")
    assert (data["jobs"]["j1"]["said"], data["orders"]["o1"]["proof"], data["orders"]["o1"]["close_report"]) == (blocked, None, None)


@pytest.mark.parametrize(
    ("printed", "replaced"),
    [
        (
            "story 2: read back https://acme.slack.com/archives/D123/p1794642114525849: *Replaced:* Asked again on a newer card · answer that one · 4:03am"
            " · <https://acme.slack.com/archives/D123/p1794608618968959|what replaced it>",
            "https://acme.slack.com/archives/D123/p1794608618968959",
        ),
        (
            "empty, his words, j4222: no release or merge settled it; live card https://acme.slack.com/archives/D123/p1794652688485409 draws '*Replaced:* Keep"
            " the hold: Macs get none of the fixes until you lift it · 2:39am · <https://acme.slack.com/archives/D123/p1794640366543949|what replaced it>', and"
            " its link opens his post",
            "https://acme.slack.com/archives/D123/p1794640366543949",
        ),
    ],
)
def test_a_proof_file_reading_a_card_back_word_for_word_names_the_post_its_slack_link_opens_never_that_link_joined_to_its_words_and_takes_done(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], unopened_links: dict[str, str], printed: str, replaced: str
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    unopened_links[f"{replaced}|what"] = "its thread is not in D123 (thread_not_found)"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{printed}\nEOF\n")
    install.prove("demo", "j1", 900)
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"]) == ("done", f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3")


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"repro": None}, "j1 keeps no proof file to run: keep one with drive job set j1 --repro <file>"),
        ({"said": "PUSHED https://github.com/Forge-AI/monorepo/pull/9", "prs": ["https://github.com/Forge-AI/monorepo/pull/9"]}, "Forge-AI/monorepo#9 merged outside yasyf/*, where a merge goes live only in its repository's release"),
        ({"said": "WAITING on the release"}, "j1 waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove"),
        (
            {"said": "WAITING on the release", "prs": [], "waits": ["release:sandsql:Forge-AI/monorepo#9"], "brief_flags": {"handoff": "Forge-AI/monorepo#9 at fedcba merged: prove it live"}},
            "j1 waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove: it is no repair",
        ),
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


def test_a_proof_file_opening_with_a_blank_line_is_refused_unrun_naming_its_first_bytes(config: Path, tmp_path: Path, fake: Callable[..., FakeGit]) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    script.write_text("\n#!/bin/sh\necho ok\n")
    refusal = (
        f"j1 keeps no proof file to run, as {script} opens '\\n#!/bin/sh\\necho ok\\n', with no #! at its first byte, so the kernel cannot exec it as a script:"
        " keep one with drive job set j1 --repro <file>"
    )
    with pytest.raises(state.DriveError, match=re.escape(refusal)):
        install.prove("demo", "j1", 900)
    assert (state.load("demo")["jobs"]["j1"]["state"], proof_events()) == ("waiting", [])


def test_a_proof_that_failed_runs_again_on_the_job_that_owns_it_once_its_cause_is_fixed_while_no_watch_starts_a_second(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = proving(config, tmp_path, fake([BUILD], {BUILD}), 1)
    state.keep_report("demo", "j1", f"PUSHED {URL}", f"PUSHED {URL}\nPR: {URL}\n")
    install.prove("demo", "j1", 900)
    assert state.load("demo")["jobs"]["j1"]["state"] == "failed"
    script.write_text("#!/bin/sh\necho 'ok the fixed tool runs'\n")
    run, due = install.proof_run, []
    monkeypatch.setattr(install, "proof_run", lambda *args: due.append(watch.proof_due(state.load("demo"), "j1")) or run(*args))
    install.prove("demo", "j1", 900)
    job = state.load("demo")["jobs"]["j1"]
    assert (job["state"], job["said"], job["proof"]["pid"], due) == ("done", f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3", os.getpid(), [False])
    assert [change.startswith(install.PROVEN) for _, change in proof_events()] == [False, True]


@pytest.mark.parametrize(
    ("taken", "kept", "repro", "refusal"),
    [
        ("stopped", f"PUSHED {URL}", True, "j1 waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove"),
        ("failed", "BLOCKED no PR opened", True, "j1 waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove"),
        ("failed", f"PUSHED {URL}", False, "j1 keeps no proof file to run: keep one with drive job set j1 --repro <file>, written from its PROVE: line, then drive job prove j1"),
    ],
)
def test_a_proof_refused_on_a_job_that_took_blocked_leaves_it_as_it_was(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], taken: str, kept: str, repro: bool, refusal: str
) -> None:
    proving(config, tmp_path, fake([BUILD], {BUILD}), 0)
    state.keep_report("demo", "j1", kept, kept)
    with state.edit("demo") as data:
        data["jobs"]["j1"].update(state=taken, said="BLOCKED no PR opened", **({} if repro else {"repro": None}))
    before = state.load("demo")["jobs"]["j1"]
    with pytest.raises(state.DriveError, match=re.escape(refusal)):
        install.prove("demo", "j1", 900)
    assert (state.load("demo")["jobs"]["j1"], proof_events()) == (before, [])


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


def refuse_first_fetch(git: FakeGit, monkeypatch: pytest.MonkeyPatch, refusal: str) -> None:
    refused = []

    def run(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[3:4] == ["fetch"] and not refused:
            refused.append(cmd)
            return subprocess.CompletedProcess(cmd, 128, "", f"{refusal}\n")
        return git.run(cmd, **options)

    monkeypatch.setattr(install.subprocess, "run", run)


def test_a_proof_whose_fetch_could_not_reach_github_fetches_again_and_proves(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    refused = "fatal: unable to access 'https://github.com/yasyf/cc-skills/': Failed to connect to github.com port 443 after 18662 ms: Couldn't connect to server"
    refuse_first_fetch(git, monkeypatch, refused)
    install.prove("demo", "j1", 900)
    assert (state.load("demo")["jobs"]["j1"]["said"], git.sleeps) == (f"DONE {URL} live: {script} exits 0 on long-running@skills 1.0.3", [install.POLL_SECONDS])
    [said] = [line for line in capsys.readouterr().err.splitlines() if "could not reach" in line]
    assert said.startswith(f"drive install: git could not reach the remote, running it again within {install.POLL_SECONDS}s: git -C ")
    assert said.endswith(f"{' '.join(install.FETCH)} {SHA} exited 128: {refused}")


def test_a_proof_whose_fetch_github_refused_for_its_commit_takes_blocked_at_once(
    config: Path, tmp_path: Path, fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch
) -> None:
    git = fake([BUILD], {BUILD})
    script = proving(config, tmp_path, git, 0)
    missing = f"fatal: remote error: upload-pack: not our ref {SHA}"
    refuse_first_fetch(git, monkeypatch, missing)
    install.prove("demo", "j1", 900)
    said = state.load("demo")["jobs"]["j1"]["said"]
    assert said.startswith(f"BLOCKED {URL}: not installed, so {script} did not run: git -C ")
    assert (said.endswith(f"exited 128: {missing}"), git.sleeps) == (True, [])


def test_a_runs_failures_are_the_tests_each_failed_jobs_pytest_summary_names(monkeypatch: pytest.MonkeyPatch) -> None:
    logs = {
        "61": "2026-11-13T13:05:55.1Z FAILED tests/test_watch.py::test_a - AssertionError: assert 1 == 2\r\n2026-11-13T13:05:55.2Z FAILED (errors=2)\n",
        "62": "2026-11-13T13:05:56.1Z ERROR tests/test_cli.py::test_b[x-y]\n2026-11-13T13:05:56.2Z ERROR tests/test_land.py\n2026-11-13T13:05:56.3Z ===== 1 failed, 2 errors in 9.1s =====\n",
    }
    monkeypatch.setattr(github, "read", lambda what, cmd, max_age="0s", text=True: "61\n62\n" if cmd[2].endswith("/jobs?filter=latest&per_page=100") else logs[cmd[2].split("/")[-2]])
    assert install.run_failures(REPO, 9) == ["tests/test_cli.py::test_b[x-y]", "tests/test_land.py", "tests/test_watch.py::test_a"]
