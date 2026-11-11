import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PurePosixPath

from drive import state

POLL_SECONDS = 20
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")
DEFAULT_BRANCH = "refs/heads/default"
FETCH = ("fetch", "--quiet", "--filter=blob:none", "--no-tags", "origin")
AFTER_UPDATE = {"cc-inbox": ("bin/cci", "serve")}
RELOADS = "reloads.json"


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


def await_release(clone: str, repo: str, plugin: str, sha: str, deadline: float, timeout: int) -> str:
    last = "none read"
    failure = ""
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


def check_holds(clone: str, key: str, entry: dict, sha: str, deadline: float) -> None:
    """Refuse an update that left the plugin at a commit without the awaited sha, as a marketplace clone holding moved tags does."""
    commit = entry["gitCommitSha"]
    git(clone, *FETCH, commit, deadline=deadline)
    if not contains(clone, commit, sha, deadline):
        raise InstallError(f"{key} is still at {entry['version']} ({commit[:12]}) after the update, which does not hold {sha[:12]}, the sha awaited")


def installed(key: str) -> dict:
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not plugins.get(key):
        raise InstallError(f"{key} is not in installed_plugins.json after the update")
    return max(plugins[key], key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")


def install(key: str, sha: str | None, repo: str | None, deadline: float, timeout: int) -> str:
    plugin, _, marketplace = key.partition("@")
    with tempfile.TemporaryDirectory(prefix="drive-install-") as clone:
        if sha:
            await_release(clone, repo or marketplace_repo(marketplace), plugin, sha, deadline, timeout)
        run(["claude", "plugin", "marketplace", "update", marketplace], deadline)
        run(["claude", "plugin", "update", key], deadline)
        entry = installed(key)
        if sha:
            check_holds(clone, key, entry, sha, deadline)
    if after := AFTER_UPDATE.get(plugin):
        run([str(Path(entry["installPath"]) / after[0]), *after[1:]], deadline)
    return entry["version"]


def reload_session(drive: str, role: str, version: str, updated: datetime, deadline: float) -> str:
    """Type /reload-plugins into one drive session's Orca terminal once it is idle, which loads an install's skills, agents and new hooks with nothing restarted, unless another install sent it one after this install's update, which loaded this one too."""
    from drive import orca

    who = f"{drive} {role}"
    terminal = state.load(drive)["roles"].get(role, {}).get("terminal")
    if not terminal:
        return f"{who}: no terminal recorded, skipped"
    if sent := claim_reload(drive, role, version, updated):
        return f"{who}: already reloaded onto {sent['version']}"
    try:
        orca.reload_plugins(terminal, max(1, int((deadline - clock() - orca.SUBMIT_SECONDS) * 1000)))
    except orca.OrcaError as error:
        unclaim_reload(drive, role, version)
        why = "still busy at the deadline" if error.code == orca.TIMEOUT else f"not reloaded ({error})"
        return f"{who}: {why}; send it with: {orca.reload_command(terminal)}"
    return f"{who}: reloaded in {terminal}"


def claim_reload(drive: str, role: str, version: str, updated: datetime) -> dict | None:
    """Under the drive's lock, the reload another install already sent the session at or after this install's update, or none, this install's then stamped as sent."""
    path = state.drive_dir(drive) / RELOADS
    with state.locked(drive):
        sent = json.loads(path.read_text()) if path.exists() else {}
        if (last := sent.get(role)) and state.parse_time(last["at"]) >= updated:
            return last
        path.write_text(json.dumps(sent | {role: {"at": state.stamp(), "version": version}}))
    return None


def unclaim_reload(drive: str, role: str, version: str) -> None:
    """Drop this install's stamp for a reload that was never sent, so the next install sends one."""
    path = state.drive_dir(drive) / RELOADS
    with state.locked(drive):
        sent = json.loads(path.read_text())
        if sent.get(role, {}).get("version") == version:
            path.write_text(json.dumps({other: last for other, last in sent.items() if other != role}))


def reload_sessions(version: str, updated: datetime, deadline: float) -> list[str]:
    sessions = [(name, role) for name in state.names() for role in state.ROLES]
    with ThreadPoolExecutor() as pool:
        return list(pool.map(lambda session: reload_session(*session, version, updated, deadline), sessions))


def restart_dashboards(key: str, deadline: float) -> list[str]:
    """Restart each drive's running dashboard that serves other code than the long-running just installed, with that install's `drive dashboard`, which replaces a server serving other code."""
    from drive import dashboard

    if key.partition("@")[0] != "long-running":
        return []
    plugin = Path(installed(key)["installPath"])
    code = str((plugin / "lib" / "drive").resolve())
    lines = []
    for name in state.names():
        drawn = dashboard.serving(name, dashboard.port_of(name))
        if not drawn or drawn["code"] == code:
            continue
        try:
            run([str(plugin / "bin" / "drive"), "dashboard", "--drive", name], deadline)
        except InstallError as error:
            lines.append(f"{name} dashboard: still serves {drawn['code']} ({error}); restart it with: drive dashboard --drive {name}")
            continue
        lines.append(f"{name} dashboard: restarted onto {plugin.name}")
    return lines


def main(plugin_at_marketplace: str, sha: str | None, repo: str | None, timeout: int) -> int:
    """Wait for a release containing sha, update the plugin, print the installed version, then restart each drive dashboard serving other code and reload every drive session on this Mac."""
    deadline = clock() + timeout
    try:
        version = install(plugin_at_marketplace, sha, repo, deadline, timeout)
    except InstallError as error:
        print(f"drive install: {error}", file=sys.stderr)
        return 1
    updated = state.now()
    print(f"installed {plugin_at_marketplace} {version}")
    for line in [*restart_dashboards(plugin_at_marketplace, deadline), *reload_sessions(version, updated, deadline)]:
        print(line)
    return 0
