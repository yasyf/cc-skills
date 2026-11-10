import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

POLL_SECONDS = 20
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")
DEFAULT_BRANCH = "refs/heads/default"
FETCH = ("fetch", "--quiet", "--filter=blob:none", "--no-tags", "origin")


class InstallError(Exception):
    pass


def clock() -> float:
    return time.monotonic()


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def run(cmd: list[str], deadline: float) -> str:
    left = deadline - clock()
    if left <= 0:
        raise InstallError(f"no time left to run {' '.join(cmd)}")
    try:
        done = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=left)
    except subprocess.TimeoutExpired:
        raise InstallError(f"{' '.join(cmd)} ran past the deadline ({left:.0f}s left when it started)") from None
    if done.returncode != 0:
        raise InstallError(f"{' '.join(cmd)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def marketplace_repo(marketplace: str) -> str:
    known = json.loads((config_dir() / "plugins" / "known_marketplaces.json").read_text())
    if marketplace not in known:
        raise InstallError(f"marketplace {marketplace} is not known; pass --repo")
    source = known[marketplace]["source"]
    if source.get("repo"):
        return source["repo"]
    if match := GITHUB_REPO.search(source.get("url") or ""):
        return match.group(1)
    raise InstallError(f"marketplace {marketplace} has no GitHub repo ({source.get('source')}); pass --repo")


def git(clone: str, *args: str, deadline: float) -> str:
    return run(["git", "-C", clone, *args], deadline)


def release_ref(clone: str, plugin: str, deadline: float) -> str | None:
    """The release the marketplace's default branch names for the plugin, read over git, which the GitHub API's rate limit does not reach."""
    git(clone, *FETCH, f"+HEAD:{DEFAULT_BRANCH}", deadline=deadline)
    marketplace = json.loads(git(clone, "show", f"{DEFAULT_BRANCH}:.claude-plugin/marketplace.json", deadline=deadline))
    entry = next((entry for entry in marketplace["plugins"] if entry["name"] == plugin), None)
    if entry is None:
        raise InstallError(f"the {marketplace['name']} marketplace names no plugin {plugin}")
    source = entry["source"]
    if isinstance(source, dict):
        if tag := source.get("ref"):
            git(clone, *FETCH, f"+refs/tags/{tag}:refs/tags/{tag}", deadline=deadline)
        return tag
    manifest = PurePosixPath(source) / ".claude-plugin" / "plugin.json"
    return git(clone, "log", "-1", "--format=%H", DEFAULT_BRANCH, "--", str(manifest), deadline=deadline).strip()


def contains(clone: str, ref: str, sha: str, deadline: float) -> bool:
    return git(clone, "rev-list", "--count", sha, f"^{ref}", deadline=deadline).strip() == "0"


def await_release(repo: str, plugin: str, sha: str, deadline: float, timeout: int) -> str:
    last = "none read"
    failure = ""
    with tempfile.TemporaryDirectory(prefix="drive-install-") as clone:
        run(["git", "init", "--bare", "--quiet", clone], deadline)
        git(clone, "remote", "add", "origin", f"https://github.com/{repo}", deadline=deadline)
        while True:
            try:
                ref = release_ref(clone, plugin, deadline)
                last = ref or "none named"
                failure = ""
                if ref and contains(clone, ref, sha, deadline):
                    return ref
            except InstallError as error:
                failure = f" (last read failed: {error})"
            left = deadline - clock()
            if left <= 0:
                raise InstallError(f"timed out after {timeout}s waiting for a {plugin} release containing {sha[:12]}; last ref {last}{failure}")
            sleep(min(POLL_SECONDS, left))


def installed_version(key: str) -> str:
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not plugins.get(key):
        raise InstallError(f"{key} is not in installed_plugins.json after the update")
    newest = max(plugins[key], key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
    return newest["version"]


def install(key: str, sha: str | None, repo: str | None, timeout: int) -> str:
    deadline = clock() + timeout
    plugin, _, marketplace = key.partition("@")
    if sha:
        await_release(repo or marketplace_repo(marketplace), plugin, sha, deadline, timeout)
    run(["claude", "plugin", "marketplace", "update", marketplace], deadline)
    run(["claude", "plugin", "update", key], deadline)
    return installed_version(key)


def main(plugin_at_marketplace: str, sha: str | None, repo: str | None, timeout: int) -> int:
    """Wait for a release containing sha, update the plugin, and print the installed version."""
    try:
        version = install(plugin_at_marketplace, sha, repo, timeout)
    except InstallError as error:
        print(f"drive install: {error}", file=sys.stderr)
        return 1
    print(f"installed {plugin_at_marketplace} {version}")
    return 0
