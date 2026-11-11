import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from drive import state

POLL_SECONDS = 20
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")
DEFAULT_BRANCH = "refs/heads/default"
FETCH = ("fetch", "--quiet", "--filter=blob:none", "--no-tags", "origin")
AFTER_UPDATE = {"cc-inbox": ("bin/cci", "serve")}


class InstallError(Exception):
    pass


class UnknownPlugin(InstallError):
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
        raise UnknownPlugin(f"the {marketplace['name']} marketplace names no plugin {plugin}")
    source = entry["source"]
    if isinstance(source, dict):
        if tag := source.get("ref"):
            git(clone, *FETCH, f"+refs/tags/{tag}:refs/tags/{tag}", deadline=deadline)
        return tag
    manifest = PurePosixPath(source) / ".claude-plugin" / "plugin.json"
    return git(clone, "log", "-1", "--first-parent", "--format=%H", DEFAULT_BRANCH, "--", str(manifest), deadline=deadline).strip()


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
            except UnknownPlugin:
                raise
            except InstallError as error:
                failure = f" (last read failed: {error})"
            left = deadline - clock()
            if left <= 0:
                raise InstallError(f"timed out after {timeout}s waiting for a {plugin} release containing {sha[:12]}; last ref {last}{failure}")
            sleep(min(POLL_SECONDS, left))


def installed(key: str) -> dict:
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not plugins.get(key):
        raise InstallError(f"{key} is not in installed_plugins.json after the update")
    return max(plugins[key], key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")


def install(key: str, sha: str | None, repo: str | None, deadline: float, timeout: int) -> str:
    plugin, _, marketplace = key.partition("@")
    if sha:
        await_release(repo or marketplace_repo(marketplace), plugin, sha, deadline, timeout)
    run(["claude", "plugin", "marketplace", "update", marketplace], deadline)
    run(["claude", "plugin", "update", key], deadline)
    entry = installed(key)
    if after := AFTER_UPDATE.get(plugin):
        run([str(Path(entry["installPath"]) / after[0]), *after[1:]], deadline)
    return entry["version"]


def reload_session(drive: str, role: str, deadline: float) -> str:
    """Type /reload-plugins into one drive session's Orca terminal once it is idle, which loads an install's skills, agents and new hooks with nothing restarted."""
    from drive import orca

    who = f"{drive} {role}"
    terminal = state.load(drive)["roles"].get(role, {}).get("terminal")
    if not terminal:
        return f"{who}: no terminal recorded, skipped"
    try:
        orca.reload_plugins(terminal, max(1, int((deadline - clock() - orca.SUBMIT_SECONDS) * 1000)))
    except orca.OrcaError as error:
        why = "still busy at the deadline" if error.code == orca.TIMEOUT else f"not reloaded ({error})"
        return f"{who}: {why}; send it with: {orca.reload_command(terminal)}"
    return f"{who}: reloaded in {terminal}"


def reload_sessions(deadline: float) -> list[str]:
    sessions = [(name, role) for name in state.names() for role in state.ROLES]
    with ThreadPoolExecutor() as pool:
        return list(pool.map(lambda session: reload_session(*session, deadline), sessions))


def main(plugin_at_marketplace: str, sha: str | None, repo: str | None, timeout: int) -> int:
    """Wait for a release containing sha, update the plugin, print the installed version, then reload every drive session on this Mac."""
    deadline = clock() + timeout
    try:
        version = install(plugin_at_marketplace, sha, repo, deadline, timeout)
    except InstallError as error:
        print(f"drive install: {error}", file=sys.stderr)
        return 1
    print(f"installed {plugin_at_marketplace} {version}")
    for line in reload_sessions(deadline):
        print(line)
    return 0
