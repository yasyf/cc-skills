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
HOMEBREW = {"cc-context": "ccx", "cc-inbox": "cci", "cc-notes": "cc-notes", "cc-present": "cc-present", "cc-review": "cc-review", "cc-slack": "cc-slack"}
TAP = "yasyf/tap"
RELEASE_VERSION = re.compile(r"\d+\.\d+\.\d+")
RELOADS = "reloads.json"
UPDATE_LOCK = ".update.lock"
RELOAD_WAIT_SECONDS = 30
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"


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


def known_marketplaces() -> dict:
    return json.loads((config_dir() / "plugins" / "known_marketplaces.json").read_text())


def source_repo(source: dict) -> str | None:
    if source.get("repo"):
        return source["repo"]
    match = GITHUB_REPO.search(source.get("url") or "")
    return match and match.group(1)


def marketplace_repo(marketplace: str) -> str:
    known = known_marketplaces()
    if marketplace not in known:
        raise InstallError(f"marketplace {marketplace} is not known; pass --repo")
    source = known[marketplace]["source"]
    if repo := source_repo(source):
        return repo
    raise InstallError(f"marketplace {marketplace} has no GitHub repo ({source.get('source')}); pass --repo")


def touched(repo: str, files: list[str]) -> list[str]:
    """The installed plugins, as plugin@marketplace, that a merge of files into repo changes: each of a marketplace on repo whose folder holds one of the files, or that is its marketplace's only plugin, whose binary any file of the repo may build."""
    known = known_marketplaces()
    plugins = []
    for key in json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]:
        plugin, _, marketplace = key.partition("@")
        if marketplace not in known or (source_repo(known[marketplace]["source"]) or "").lower() != repo.lower():
            continue
        listed = json.loads((Path(known[marketplace]["installLocation"]) / ".claude-plugin" / "marketplace.json").read_text())["plugins"]
        source = next((entry["source"] for entry in listed if entry["name"] == plugin), None)
        if source is None:
            continue
        folder = source.get("path") if isinstance(source, dict) else source
        if len(listed) == 1 or folder and any(PurePosixPath(file).is_relative_to(PurePosixPath(folder)) for file in files):
            plugins.append(key)
    return plugins


def git(clone: str, *args: str, deadline: float) -> str:
    return run(["git", "-C", clone, *args], deadline)


def newest_release(clone: str, plugin: str, deadline: float) -> tuple[str, str] | None:
    """The release the marketplace's default branch names for the plugin and the plugin's folder in it, read over git, which the GitHub API's rate limit does not reach."""
    git(clone, *FETCH, f"+HEAD:{DEFAULT_BRANCH}", deadline=deadline)
    marketplace = json.loads(git(clone, "show", f"{DEFAULT_BRANCH}:.claude-plugin/marketplace.json", deadline=deadline))
    entry = next((entry for entry in marketplace["plugins"] if entry["name"] == plugin), None)
    if entry is None:
        raise UnknownPlugin(f"the {marketplace['name']} marketplace names no plugin {plugin}")
    source = entry["source"]
    if isinstance(source, dict):
        if not (tag := source.get("ref")):
            return None
        fetch_tag(clone, tag, deadline)
        return tag, str(PurePosixPath(source.get("path", ".")))
    manifest = PurePosixPath(source) / ".claude-plugin" / "plugin.json"
    ref = git(clone, "log", "-1", "--first-parent", "--format=%H", DEFAULT_BRANCH, "--", str(manifest), deadline=deadline).strip()
    return ref, str(PurePosixPath(source))


def fetch_tag(clone: str, tag: str, deadline: float) -> None:
    git(clone, *FETCH, f"+refs/tags/{tag}:refs/tags/{tag}", deadline=deadline)


def holds(clone: str, build: str, sha: str, deadline: float) -> bool:
    """Whether build holds every commit on sha's first-parent line but its merges, so a release's version sync merged into the default branch is held once the line below the merge is."""
    return git(clone, "rev-list", "--count", "--first-parent", "--no-merges", sha, f"^{build}", deadline=deadline).strip() == "0"


def binary_lacking(clone: str, repo: str, binruns: list[str], sha: str, deadline: float) -> str | None:
    """The first binary a plugin runs through a binrun file from a GitHub release of repo whose tag lacks sha, as `cci from release v0.54.27`: the release is built at its tag, cut before the version sync carrying it merges into the default branch, so a commit landing in between is in the plugin's files but not in its binary."""
    for text in binruns:
        spec = json.loads(text[text.index("{") :])
        providers = [provider for platform in spec.get("platforms", {}).values() for provider in platform["providers"]]
        for tag in sorted({provider["tag"] for provider in providers if provider["type"] == "github-release" and provider["repo"].lower() == repo.lower()}):
            fetch_tag(clone, tag, deadline)
            if not holds(clone, tag, sha, deadline):
                return f"{spec['name']} from release {tag}"
    return None


def release_lacking(clone: str, repo: str, plugin: str, sha: str, deadline: float) -> str | None:
    """What of the newest release the marketplace names for the plugin lacks sha, its commit or a binary it pins, or None once it holds sha."""
    named = newest_release(clone, plugin, deadline)
    if named is None:
        return "the marketplace names no release of it"
    ref, folder = named
    if not holds(clone, ref, sha, deadline):
        return f"the newest, {ref}, lacks it"
    paths = git(clone, "ls-tree", "-r", "--name-only", ref, "--", folder, deadline=deadline).splitlines()
    binruns = [git(clone, "show", f"{ref}:{path}", deadline=deadline) for path in paths if path.endswith(".binrun")]
    if binary := binary_lacking(clone, repo, binruns, sha, deadline):
        return f"the newest, {ref}, runs {binary}, which lacks it"
    return None


def await_release(clone: str, repo: str, plugin: str, sha: str, deadline: float, timeout: int) -> None:
    last = "no release read"
    failure = ""
    run(["git", "init", "--bare", "--quiet", clone], deadline)
    git(clone, "remote", "add", "origin", f"https://github.com/{repo}", deadline=deadline)
    while True:
        try:
            lacking = release_lacking(clone, repo, plugin, sha, deadline)
            failure = ""
            if lacking is None:
                return
            if lacking != last:
                print(f"drive install: waiting for a {plugin} release holding {sha[:12]}: {lacking}", file=sys.stderr, flush=True)
            last = lacking
        except UnknownPlugin:
            raise
        except InstallError as error:
            failure = f" (last read failed: {error})"
        left = deadline - clock()
        if left <= 0:
            raise InstallError(f"timed out after {timeout}s waiting for a {plugin} release holding {sha[:12]}; {last}{failure}")
        sleep(min(POLL_SECONDS, left))


def check_holds(clone: str, repo: str, key: str, entry: dict, sha: str, deadline: float) -> None:
    """Refuse an update that left the plugin at a commit without the awaited sha, as a marketplace clone holding moved tags does, or running a binary from a release without it."""
    commit = entry["gitCommitSha"]
    git(clone, *FETCH, commit, deadline=deadline)
    if not holds(clone, commit, sha, deadline):
        raise InstallError(f"{key} is still at {entry['version']} ({commit[:12]}) after the update, which does not hold {sha[:12]}, the sha awaited")
    binruns = [path.read_text() for path in sorted(Path(entry["installPath"]).rglob("*.binrun"))]
    if binary := binary_lacking(clone, repo, binruns, sha, deadline):
        raise InstallError(f"{key} {entry['version']} runs {binary} after the update, which does not hold {sha[:12]}, the sha awaited")


def await_homebrew(clone: str, binary: str, sha: str | None, deadline: float, timeout: int) -> None:
    """Upgrade the yasyf/tap formula or cask installing binary, the copy sessions run bare since Claude Code appends plugin bin directories after /opt/homebrew/bin on PATH, until the release bare binary prints with --version holds sha; each pass fast-forwards the tap's own clone first and upgrades with Homebrew's auto-update off, since brew update fetches every tap on the Mac and fails when any one of them cannot be fetched."""
    tap = run(["brew", "--repository", TAP], deadline).strip()
    last = ""
    while True:
        with state.flocked(config_dir() / "plugins" / UPDATE_LOCK):
            git(tap, "pull", "--ff-only", "--quiet", deadline=deadline)
            run(["env", "HOMEBREW_NO_AUTO_UPDATE=1", "brew", "upgrade", f"{TAP}/{binary}"], deadline)
        if sha is None:
            return
        printed = run([binary, "--version"], deadline).strip()
        if not (version := RELEASE_VERSION.search(printed)):
            raise InstallError(f"bare {binary} --version printed {printed!r}, which names no release")
        tag = f"v{version.group()}"
        fetch_tag(clone, tag, deadline)
        if holds(clone, tag, sha, deadline):
            return
        lacking = f"bare {binary} runs release {tag}, which lacks it"
        if lacking != last:
            print(f"drive install: waiting for {TAP}/{binary} to hold {sha[:12]}: {lacking}", file=sys.stderr, flush=True)
        last = lacking
        if deadline - clock() <= POLL_SECONDS:
            raise InstallError(f"timed out after {timeout}s waiting for bare {binary} to hold {sha[:12]}; {lacking}")
        sleep(POLL_SECONDS)


def upgraded() -> Path | None:
    """The drive of the newest installed long-running, when this code runs from the plugin cache and a newer one is installed."""
    if not DRIVE.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        return None
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    installs = [entry for key, entries in plugins.items() if key.partition("@")[0] == "long-running" for entry in entries]
    newest = Path(max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")["installPath"]) / "bin" / "drive"
    return None if newest.resolve() == DRIVE else newest


def installed(key: str) -> dict:
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not plugins.get(key):
        raise InstallError(f"{key} is not in installed_plugins.json after the update")
    return max(plugins[key], key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")


def install(key: str, sha: str | None, repo: str | None, deadline: float, timeout: int) -> str:
    plugin, _, marketplace = key.partition("@")
    with tempfile.TemporaryDirectory(prefix="drive-install-") as clone:
        if sha:
            repo = repo or marketplace_repo(marketplace)
            await_release(clone, repo, plugin, sha, deadline, timeout)
        with state.flocked(config_dir() / "plugins" / UPDATE_LOCK):
            run(["claude", "plugin", "marketplace", "update", marketplace], deadline)
            run(["claude", "plugin", "update", key], deadline)
            entry = installed(key)
        if sha:
            check_holds(clone, repo, key, entry, sha, deadline)
        if after := AFTER_UPDATE.get(plugin):
            run([str(Path(entry["installPath"]) / after[0]), *after[1:]], deadline)
        if binary := HOMEBREW.get(plugin):
            await_homebrew(clone, binary, sha, deadline, timeout)
    return entry["version"]


def reload_session(drive: str, seat: str, version: str, updated: datetime, deadline: float) -> str:
    """Type /reload-plugins into one drive session's Orca terminal once it is idle, or after RELOAD_WAIT_SECONDS into its busy prompt, which loads an install's skills, agents and new hooks with nothing restarted, unless another install sent it one after this install's update, which loaded this one too. A seat is a role, or `sub-coordinator <project>`."""
    from drive import orca

    who = f"{drive} {seat}"
    terminal = state.bound_sessions(state.saved(drive)).get(seat, {}).get("terminal")
    if not terminal:
        return f"{who}: no terminal recorded, skipped"
    if sent := claim_reload(drive, seat, version, updated):
        return f"{who}: already reloaded onto {sent['version']}"
    wait = max(0.001, min(RELOAD_WAIT_SECONDS, deadline - clock() - orca.SUBMIT_SECONDS))
    try:
        idle = orca.reload_plugins(terminal, int(wait * 1000))
    except orca.OrcaError as error:
        unclaim_reload(drive, seat, version)
        return f"{who}: not reloaded ({error}); send it with: {orca.reload_command(terminal)}"
    if idle:
        return f"{who}: reloaded in {terminal}"
    return f"{who}: busy for {wait:.0f}s, so /reload-plugins is queued in {terminal} and runs when its turn ends"


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
    """Reload every session bound to every drive: the communicator, the top coordinator, the meta session and each sub-coordinator, read from each drive's saved bindings alone, since a newer install than this code may have saved the drive since its update."""
    sessions = [(name, seat) for name in state.names() for seat in state.bound_sessions(state.saved(name))]
    with ThreadPoolExecutor() as pool:
        return list(pool.map(lambda session: reload_session(*session, version, updated, deadline), sessions))


def reload_main() -> int:
    """Reload every drive session on this Mac now, as an install does after its update, whatever installs reloaded them before."""
    from drive import orca

    for line in reload_sessions(state.VERSION, state.now(), clock() + RELOAD_WAIT_SECONDS + orca.SUBMIT_SECONDS):
        print(line)
    return 0


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
    """Wait for a release holding sha, its commit and each binary it pins, update the plugin and its Homebrew twin until the bare binary holds sha, print the installed version, then restart each drive dashboard serving other code and reload every drive session on this Mac."""
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
