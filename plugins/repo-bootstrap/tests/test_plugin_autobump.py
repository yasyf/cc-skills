"""plugin_autobump: baseline selection, drift signals, surgical bump edit, and
the guarded/exempt/non-semver verdicts — exercised against scratch git repos."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "plugin_autobump.py"

_spec = importlib.util.spec_from_file_location("plugin_autobump", SCRIPT)
autobump = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = autobump  # @dataclass resolves __module__ via sys.modules
_spec.loader.exec_module(autobump)


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _manifest_path(root: str) -> str:
    return ".claude-plugin/plugin.json" if root == "." else f"{root}/.claude-plugin/plugin.json"


def _manifest_text(name: str, version: str | None = None, **extra) -> str:
    data: dict = {"name": name}
    if version is not None:
        data["version"] = version
    data.update(extra)
    return json.dumps(data, indent=2) + "\n"


def _write(repo: Path, rel: str, content: str) -> None:
    full = repo / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _commit(repo: Path, msg: str) -> str:
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", msg)
    return _out(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _run(tmp_path, "init", "-b", "main")
    _run(tmp_path, "config", "user.name", "Test")
    _run(tmp_path, "config", "user.email", "test@example.com")
    _run(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


def _by_name(results, name):
    return next(r for r in results if r.name == name)


def _set_env(monkeypatch, repo, **kw):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)  # ambient CI must not leak in
    monkeypatch.delenv("AUTOBUMP_FORCE", raising=False)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("AUTOBUMP_DRY_RUN", kw.get("dry_run", "true"))
    monkeypatch.setenv("AUTOBUMP_GUARD", kw.get("guard", ""))
    monkeypatch.setenv("AUTOBUMP_EXCLUDES", kw.get("excludes", ""))
    monkeypatch.setenv("AUTOBUMP_STRICT", kw.get("strict", "false"))
    if "force" in kw:
        monkeypatch.setenv("AUTOBUMP_FORCE", kw["force"])
    if "ci" in kw:
        monkeypatch.setenv("GITHUB_ACTIONS", kw["ci"])


# 1 — content after the last version change ⇒ planned bump
def test_content_after_version_change_bumps(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add content")

    r = _by_name(autobump.analyze(repo, set(), []), "a")
    assert r.verdict == "bump"
    assert r.new_version == "0.2.1"


# 2 — last change was the version bump itself ⇒ clean
def test_clean_when_last_change_is_bump(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")

    assert _by_name(autobump.analyze(repo, set(), []), "a").verdict == "clean"


# 3 — a manifest edit that leaves the version value put is NOT a baseline
def test_manifest_touch_without_version_change_is_not_baseline(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    bump_sha = _commit(repo, "bump a")
    _write(repo, m, _manifest_text("a", "0.2.0", dependencies=[{"name": "x", "version": ">=2.0.0"}]))
    _commit(repo, "pin dependency, same version")

    assert autobump.find_baseline(repo, m) == bump_sha


# 4 — version null ⇒ exempt
def test_missing_version_is_exempt(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", version=None))
    _commit(repo, "create a without version")

    assert _by_name(autobump.analyze(repo, set(), []), "a").verdict == "exempt"


# 5 — discovery across shapes, independent baselines
def test_discovery_and_independent_baselines(repo):
    roots = {".": "root", "plugin": "nested", "plugins/a": "a", "plugins/b": "b"}
    for root, name in roots.items():
        _write(repo, _manifest_path(root), _manifest_text(name, "0.1.0"))
    create = _commit(repo, "create all")
    _write(repo, _manifest_path("plugins/a"), _manifest_text("a", "0.2.0"))
    bump_a = _commit(repo, "bump a")

    found = {autobump.plugin_root(m) for m in autobump.discover_manifests(repo)}
    assert found == set(roots)
    assert autobump.find_baseline(repo, _manifest_path("plugins/a")) == bump_a
    assert autobump.find_baseline(repo, _manifest_path("plugins/b")) == create


# 6 — .github/.claude/.gitignore/.cursor-plugin inside the root don't count
def test_infra_paths_do_not_count_as_drift(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")
    _write(repo, "plugins/a/.github/ci.yml", "on: push\n")
    _write(repo, "plugins/a/.claude/settings.json", "{}\n")
    _write(repo, "plugins/a/.gitignore", "dist/\n")
    _write(repo, "plugins/a/.cursor-plugin/plugin.json", _manifest_text("a", "0.2.0"))
    _commit(repo, "infra only")

    assert _by_name(autobump.analyze(repo, set(), []), "a").verdict == "clean"


# 7 — a manifest mcpServers edit is structural drift
def test_manifest_mcpservers_edit_counts(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")
    _write(repo, m, _manifest_text("a", "0.2.0", mcpServers={"srv": {"command": "run"}}))
    _commit(repo, "add mcp server, same version")

    r = _by_name(autobump.analyze(repo, set(), []), "a")
    assert r.verdict == "bump"
    assert r.new_version == "0.2.1"


# 8 — surgical bump: byte-identical except the top-level version; nested untouched
def test_bump_edit_is_surgical():
    raw = (
        "{\n"
        '  "name": "demo",\n'
        '  "version": "1.2.3",\n'
        '  "dependencies": [\n'
        '    { "name": "captain-hook", "version": ">=11.0.0" }\n'
        "  ]\n"
        "}\n"
    )
    out = autobump.bump_manifest_text(raw, "1.2.3", "1.2.4")
    assert out == raw.replace('"version": "1.2.3"', '"version": "1.2.4"', 1)
    parsed = json.loads(out)
    assert parsed["version"] == "1.2.4"
    assert parsed["dependencies"][0]["version"] == ">=11.0.0"


# 9 — guarded plugin: never edited; error/exit only under strict
def test_guarded_plugin_warns_and_never_edits(repo, monkeypatch):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add content")

    assert _by_name(autobump.analyze(repo, {"a"}, []), "a").verdict == "guarded"

    _set_env(monkeypatch, repo, guard="a", strict="false")
    assert autobump.main() == 0
    _set_env(monkeypatch, repo, guard="a", strict="true")
    assert autobump.main() == 1
    assert json.loads((repo / m).read_text())["version"] == "0.2.0"


# 10 — a real bump run is idempotent on a second pass
def test_bump_run_is_idempotent(repo, tmp_path, monkeypatch):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add content")

    _run(tmp_path, "init", "--bare", "remote.git")
    _run(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _run(repo, "push", "-u", "origin", "main")

    _set_env(monkeypatch, repo, dry_run="false", force="1")
    assert autobump.main() == 0
    assert json.loads((repo / m).read_text())["version"] == "0.2.1"

    assert autobump.main() == 0
    assert json.loads((repo / m).read_text())["version"] == "0.2.1"
    assert _by_name(autobump.analyze(repo, set(), []), "a").verdict == "clean"
    assert _out(tmp_path / "remote.git", "tag", "--list") == "a--v0.2.1"


# 10b — every pushed bump is tagged <name>--v<version> at the bump commit on the remote
def test_bump_tags_each_plugin_at_the_pushed_commit(repo, tmp_path, monkeypatch):
    for name in ("a", "b"):
        _write(repo, _manifest_path(f"plugins/{name}"), _manifest_text(name, "0.1.0"))
    _commit(repo, "create plugins")
    for name in ("a", "b"):
        _write(repo, f"plugins/{name}/src.py", "print('x')\n")
    _commit(repo, "add content")

    _run(tmp_path, "init", "--bare", "remote.git")
    _run(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _run(repo, "push", "-u", "origin", "main")

    _set_env(monkeypatch, repo, dry_run="false", force="1")
    assert autobump.main() == 0
    remote = tmp_path / "remote.git"
    bump = _out(remote, "rev-parse", "main")
    assert _out(remote, "log", "-1", "--format=%s", bump).startswith("chore(plugins): autobump 2 plugins")
    for name in ("a", "b"):
        assert _out(remote, "rev-parse", f"{name}--v0.1.1^{{commit}}") == bump


@pytest.mark.parametrize("tag_location", ["local", "remote"])
def test_a_version_tag_at_a_different_commit_fails_the_run(repo, tmp_path, monkeypatch, tag_location):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    taken = _commit(repo, "add content")

    _run(tmp_path, "init", "--bare", "remote.git")
    _run(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _run(repo, "push", "-u", "origin", "main")
    if tag_location == "remote":
        _run(repo, "push", "origin", f"{taken}:refs/tags/a--v0.1.1")
    else:
        _run(repo, "tag", "a--v0.1.1", taken)

    _set_env(monkeypatch, repo, dry_run="false", force="1")
    assert autobump.main() == 1
    assert _out(tmp_path / "remote.git" if tag_location == "remote" else repo, "rev-parse", "a--v0.1.1") == taken


# 11 — non-semver version is a hard error
def test_non_semver_is_hard_error(repo, monkeypatch):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1"))
    _commit(repo, "create a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add content")

    assert _by_name(autobump.analyze(repo, set(), []), "a").verdict == "error"

    _set_env(monkeypatch, repo, dry_run="true")
    assert autobump.main() == 1


# 12 — a non-dry local run never mutates without CI or AUTOBUMP_FORCE
def test_non_dry_local_run_is_report_only_without_force(repo, tmp_path, monkeypatch):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "bump a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add content")

    _run(tmp_path, "init", "--bare", "remote.git")
    _run(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _run(repo, "push", "-u", "origin", "main")
    before = _out(repo, "rev-parse", "HEAD")

    _set_env(monkeypatch, repo, dry_run="false")  # not CI, not forced
    assert autobump.main() == 0
    assert json.loads((repo / m).read_text())["version"] == "0.2.0"
    assert _out(repo, "rev-parse", "HEAD") == before

    _set_env(monkeypatch, repo, dry_run="false", force="1")
    assert autobump.main() == 0
    assert json.loads((repo / m).read_text())["version"] == "0.2.1"
    assert _out(repo, "rev-parse", "HEAD") != before


def test_a_bump_pins_its_marketplace_entry_to_the_tag_it_publishes(repo, tmp_path, monkeypatch):
    """Oct 31 (#257362): Claude Code copied long-running 0.7.729 from main at #1442, past its tag, since the entry named a path on the default branch."""
    _write(repo, _manifest_path("plugins/a"), _manifest_text("a", "0.1.0"))
    _write(repo, _manifest_path("plugins/b"), _manifest_text("b", "0.3.0"))
    marketplace = {"name": "m", "plugins": [{"name": "a", "source": "./plugins/a", "description": "é"}, {"name": "b", "source": "./plugins/b"}]}
    _write(repo, ".claude-plugin/marketplace.json", json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n")
    _commit(repo, "create a + b")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add a content")
    remote = tmp_path / "remote.git"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-u", "origin", "main")

    _set_env(monkeypatch, repo, dry_run="false", force="1")
    assert autobump.main() == 0

    pinned = json.loads(_out(remote, "show", "a--v0.1.1:.claude-plugin/marketplace.json"))
    assert pinned["plugins"] == [
        {"name": "a", "source": {"source": "git-subdir", "url": str(remote), "path": "plugins/a", "ref": "a--v0.1.1"}, "description": "é", "version": "0.1.1"},
        {"name": "b", "source": "./plugins/b"},
    ]
    assert _out(remote, "rev-parse", "main") == _out(remote, "rev-parse", "a--v0.1.1^{commit}")
    assert _by_name(autobump.analyze(repo, set(), []), "a").verdict == "clean"


def test_a_root_plugin_pins_the_whole_repository_at_its_tag():
    assert autobump.tagged_source("https://github.com/o/r", ".", "r--v1.0.1") == {"source": "url", "url": "https://github.com/o/r", "ref": "r--v1.0.1"}


# 13 — a nested plugin's drift never leaks up to a parent plugin's root
def test_nested_plugin_drift_does_not_leak_to_parent(repo):
    _write(repo, _manifest_path("."), _manifest_text("root", "0.1.0"))
    _write(repo, _manifest_path("plugins/a"), _manifest_text("a", "0.1.0"))
    _commit(repo, "create root + a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "edit only plugins/a content")

    results = autobump.analyze(repo, set(), [])
    assert _by_name(results, "root").verdict == "clean"
    assert _by_name(results, "a").verdict == "bump"


# 14 — a wrong-key bump edit is contained: that plugin errors, siblings still bump
def test_bump_error_is_contained(repo, monkeypatch):
    # "bad" carries a dependency version equal to (and textually before) its
    # top-level version, so the surgical regex would hit the wrong key.
    bad_text = (
        "{\n"
        '  "name": "bad",\n'
        '  "dependencies": [\n'
        '    { "name": "x", "version": "0.1.0" }\n'
        "  ],\n"
        '  "version": "0.1.0"\n'
        "}\n"
    )
    _write(repo, _manifest_path("plugins/bad"), bad_text)
    _write(repo, _manifest_path("plugins/good"), _manifest_text("good", "0.1.0"))
    _commit(repo, "create bad + good")
    _write(repo, "plugins/bad/src.py", "print('x')\n")
    _write(repo, "plugins/good/src.py", "print('y')\n")
    _commit(repo, "content in both")

    results = autobump.analyze(repo, set(), [])
    bad = _by_name(results, "bad")
    good = _by_name(results, "good")
    assert bad.verdict == "error"
    assert bad.message  # readable, non-empty diagnostic
    assert bad.new_text is None  # its write is skipped
    assert good.verdict == "bump"
    assert good.new_text is not None  # sibling still planned for a write

    _set_env(monkeypatch, repo, dry_run="true")
    assert autobump.main() == 1


# 15 — an unparseable manifest at HEAD is an error, not a silent exempt
def test_malformed_manifest_is_error(repo):
    _write(repo, _manifest_path("plugins/a"), "{ not valid json ")
    _commit(repo, "malformed manifest")

    (r,) = autobump.analyze(repo, set(), [])
    assert r.verdict == "error"
    assert "valid JSON" in r.message


# 16 — a release bumps .claude-plugin first and mirrors it into .cursor-plugin
# in a later commit. The mirror moving is not the plugin's content drifting, so
# a guarded plugin stays clean and the strict weekly run does not cry wolf.
def test_cursor_manifest_mirror_is_not_drift_for_a_guarded_plugin(repo):
    m = _manifest_path("plugins/a")
    _write(repo, m, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, m, _manifest_text("a", "0.2.0"))
    _commit(repo, "descriptor: sync manifest to 0.2.0")
    _write(repo, "plugins/a/.cursor-plugin/plugin.json", _manifest_text("a", "0.2.0"))
    _commit(repo, "cursor-bump: mirror manifest to 0.2.0")

    assert _by_name(autobump.analyze(repo, {"a"}, []), "a").verdict == "clean"


def test_a_stale_bump_dropped_by_rebase_accepts_the_published_canonical_tag(repo, tmp_path, monkeypatch):
    _write(repo, _manifest_path("plugins/a"), _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, "plugins/a/src.py", "print('x')\n")
    _commit(repo, "add content")
    remote, stale = tmp_path / "remote.git", tmp_path / "stale"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-u", "origin", "main")
    _run(tmp_path, "clone", "--branch", "main", str(remote), str(stale))
    _set_env(monkeypatch, repo, dry_run="false", force="1")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-30T10:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-30T10:00:00Z")
    assert autobump.main() == 0
    canonical = _out(remote, "rev-parse", "main")
    assert _out(stale, "rev-parse", "HEAD") != canonical
    assert _by_name(autobump.analyze(stale, set(), []), "a").verdict == "bump"
    calls = []
    git = autobump._git

    def recorded(repo, *args, **kwargs):
        calls.append(args)
        return git(repo, *args, **kwargs)

    monkeypatch.setattr(autobump, "_git", recorded)
    _set_env(monkeypatch, stale, dry_run="false", force="1")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-30T11:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-30T11:00:00Z")
    assert autobump.main() == 0
    assert _out(stale, "rev-parse", "HEAD") == canonical
    assert _out(remote, "rev-parse", "refs/tags/a--v0.1.1") == canonical
    assert not [args for args in calls if args[0] in {"tag", "push"}]


@pytest.mark.parametrize("published", [False, True])
def test_tag_bumps_pushes_only_missing_remote_tags_and_reuses_matching_local_tags(repo, tmp_path, monkeypatch, published):
    for name in ("a", "b"):
        _write(repo, _manifest_path(f"plugins/{name}"), _manifest_text(name, "0.1.0"))
    _commit(repo, "create plugins")
    for name in ("a", "b"):
        _write(repo, f"plugins/{name}/src.py", "print('x')\n")
    _commit(repo, "add content")
    remote = tmp_path / "remote.git"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-u", "origin", "main")
    bumps = [result for result in autobump.analyze(repo, set(), []) if result.verdict == "bump"]
    assert autobump.commit_and_push(repo, bumps, "main")
    canonical = _out(repo, "rev-parse", "HEAD")
    if published:
        _run(repo, "push", "origin", "HEAD:refs/tags/a--v0.1.1")
    else:
        _run(repo, "tag", "a--v0.1.1")
    calls = []
    git = autobump._git

    def recorded(repo, *args, **kwargs):
        calls.append(args)
        return git(repo, *args, **kwargs)

    monkeypatch.setattr(autobump, "_git", recorded)
    assert autobump.tag_bumps(repo, bumps)
    expected = ("refs/tags/b--v0.1.1",) if published else ("refs/tags/a--v0.1.1", "refs/tags/b--v0.1.1")
    assert [args for args in calls if args[0] == "push"] == [("push", "origin", *expected)]
    assert [args for args in calls if args[0] == "tag"] == [("tag", "b--v0.1.1", canonical)]
    assert all(_out(remote, "rev-parse", f"refs/tags/{name}--v0.1.1") == canonical for name in ("a", "b"))


def test_a_partially_overtaken_batch_tags_each_canonical_version_owner(repo, tmp_path, monkeypatch):
    for name in ("a", "b"):
        _write(repo, _manifest_path(f"plugins/{name}"), _manifest_text(name, "0.1.0"))
    _commit(repo, "create plugins")
    _write(repo, "plugins/a/src.py", "print('a')\n")
    _commit(repo, "add a content")
    remote, stale = tmp_path / "remote.git", tmp_path / "stale"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-u", "origin", "main")
    _run(tmp_path, "clone", "--branch", "main", str(remote), str(stale))
    _run(stale, "config", "user.name", "Test")
    _run(stale, "config", "user.email", "test@example.com")
    _run(stale, "config", "commit.gpgsign", "false")
    _write(stale, "plugins/b/src.py", "print('b')\n")
    _commit(stale, "add b content")
    _run(stale, "push", "origin", "main")
    assert [result.name for result in autobump.analyze(repo, set(), []) if result.verdict == "bump"] == ["a"]
    _set_env(monkeypatch, repo, dry_run="false", force="1")
    assert autobump.main() == 0
    first = _out(remote, "rev-parse", "refs/tags/a--v0.1.1")
    assert [result.name for result in autobump.analyze(stale, set(), []) if result.verdict == "bump"] == ["a", "b"]
    _set_env(monkeypatch, stale, dry_run="false", force="1")
    assert autobump.main() == 0
    second = _out(remote, "rev-parse", "main")
    assert first != second
    assert _out(remote, "rev-parse", "refs/tags/a--v0.1.1") == first
    assert _out(remote, "rev-parse", "refs/tags/b--v0.1.1") == second
    assert _out(stale, "diff", "--name-only", first, second) == _manifest_path("plugins/b")
    assert autobump.find_baseline(stale, _manifest_path("plugins/a")) == first
    assert autobump.find_baseline(stale, _manifest_path("plugins/b")) == second


def test_a_batch_overtaken_by_another_version_refuses_obsolete_tag_publication(repo, tmp_path):
    manifest = _manifest_path("plugins/a")
    _write(repo, manifest, _manifest_text("a", "0.1.0"))
    _commit(repo, "create a")
    _write(repo, "plugins/a/src.py", "print('a')\n")
    _commit(repo, "add a content")
    bumps = [result for result in autobump.analyze(repo, set(), []) if result.verdict == "bump"]
    _write(repo, manifest, _manifest_text("a", "0.1.2"))
    _commit(repo, "newer canonical version")
    remote = tmp_path / "remote.git"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-u", "origin", "main")
    assert not autobump.tag_bumps(repo, bumps)
    assert _out(repo, "tag", "--list") == ""
    assert _out(remote, "tag", "--list") == ""
