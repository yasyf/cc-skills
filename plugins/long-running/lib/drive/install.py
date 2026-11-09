import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

POLL_SECONDS = 20
CONTAINS = {"ahead", "identical"}
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")


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


def release_ref(repo: str, plugin: str, deadline: float) -> str | None:
    raw = run(
        ["gh", "api", f"repos/{repo}/contents/.claude-plugin/marketplace.json", "-H", "Accept: application/vnd.github.raw"],
        deadline,
    )
    entry = next((entry for entry in json.loads(raw)["plugins"] if entry["name"] == plugin), None)
    if entry is None:
        raise InstallError(f"{repo} marketplace names no plugin {plugin}")
    source = entry["source"]
    if isinstance(source, dict):
        return source.get("ref")
    manifest = PurePosixPath(source) / ".claude-plugin" / "plugin.json"
    return run(["gh", "api", f"repos/{repo}/commits?path={manifest}&per_page=1", "--jq", ".[0].sha"], deadline).strip()


def contains(repo: str, ref: str, sha: str, deadline: float) -> bool:
    return run(["gh", "api", f"repos/{repo}/compare/{sha}...{ref}", "--jq", ".status"], deadline).strip() in CONTAINS


def await_release(repo: str, plugin: str, sha: str, deadline: float, timeout: int) -> str:
    last = "none read"
    failure = ""
    while True:
        try:
            ref = release_ref(repo, plugin, deadline)
            last = ref or "none named"
            failure = ""
            if ref and contains(repo, ref, sha, deadline):
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
