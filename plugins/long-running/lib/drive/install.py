import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

POLL_SECONDS = 20
CONTAINS = {"ahead", "identical"}
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")


class InstallError(Exception):
    pass


def run(cmd: list[str]) -> str:
    done = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if done.returncode != 0:
        raise InstallError(f"{' '.join(cmd)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def sleep(seconds: float) -> None:
    time.sleep(seconds)


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


def named_ref(repo: str, plugin: str) -> str | None:
    raw = run(["gh", "api", f"repos/{repo}/contents/.claude-plugin/marketplace.json", "-H", "Accept: application/vnd.github.raw"])
    entry = next((entry for entry in json.loads(raw)["plugins"] if entry["name"] == plugin), None)
    if entry is None:
        raise InstallError(f"{repo} marketplace names no plugin {plugin}")
    source = entry["source"]
    return source.get("ref") if isinstance(source, dict) else None


def contains(repo: str, ref: str, sha: str) -> bool:
    return run(["gh", "api", f"repos/{repo}/compare/{sha}...{ref}", "--jq", ".status"]).strip() in CONTAINS


def await_release(repo: str, plugin: str, sha: str, timeout: int) -> str:
    waited = 0
    last = "none read"
    failure = ""
    while True:
        try:
            ref = named_ref(repo, plugin)
            last = ref or "none named"
            failure = ""
            if ref and contains(repo, ref, sha):
                return ref
        except InstallError as error:
            failure = f" (last read failed: {error})"
        if waited + POLL_SECONDS > timeout:
            raise InstallError(f"timed out after {timeout}s waiting for a {plugin} release containing {sha[:12]}; last ref {last}{failure}")
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS


def installed_version(key: str) -> str:
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not plugins.get(key):
        raise InstallError(f"{key} is not in installed_plugins.json after the update")
    newest = max(plugins[key], key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
    return newest["version"]


def install(key: str, sha: str | None, repo: str | None, timeout: int) -> str:
    plugin, _, marketplace = key.partition("@")
    if sha:
        await_release(repo or marketplace_repo(marketplace), plugin, sha, timeout)
    run(["claude", "plugin", "marketplace", "update", marketplace])
    run(["claude", "plugin", "update", key])
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
