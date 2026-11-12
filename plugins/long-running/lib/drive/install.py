import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

from drive import github, state

POLL_SECONDS = 20
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")
DEFAULT_BRANCH = "refs/heads/default"
FETCH = ("fetch", "--quiet", "--filter=blob:none", "--no-tags", "origin")
AFTER_UPDATE = {"cc-inbox": ("bin/cci", "serve")}
HOMEBREW = {"cc-context": "ccx", "cc-inbox": "cci", "cc-notes": "cc-notes", "cc-present": "cc-present", "cc-review": "cc-review", "cc-slack": "cc-slack"}
TAP = "yasyf/tap"
RELEASE_VERSION = re.compile(r"\d+\.\d+\.\d+")
VERSION_TAG = re.compile(r"refs/tags/(v(\d+)\.(\d+)\.(\d+))")
WORKFLOWS = ".github/workflows/"
RELEASE_TAG_JOB = re.compile(r" *uses: *\S*/plugin-release-tag\.yml(?:@\S+)?\s*")
RELOADS = "reloads.json"
UPDATE_LOCK = ".update.lock"
RELOAD_WAIT_SECONDS = 30
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
MANIFEST = ".claude-plugin/plugin.json"
FLOOR = re.compile(r">=(\d+\.\d+\.\d+)")
HOOKS = "captain-hook"
HELPER = "capt-hook"
HELPER_REPO = "yasyf/captain-hook"
APP_PLIST = Path.home() / "Applications" / "Captain Hook.app" / "Contents" / "Info.plist"
HELPER_ASKED = "Captain-hook update, your Mac: install {release} now? It restarts the hook helper once"
INSTALL_NOW = "Install now"
EVERY_INSTALL = "Installs never need me"
HELPER_OPTIONS = f"{INSTALL_NOW} (recommended), {EVERY_INSTALL} and Not now"
PROVEN = "proven live by drive job prove"
PROOF_FAILED = "drive job prove failed"
SAID_CHARS = 300


class InstallError(Exception):
    pass


class UnknownPlugin(InstallError):
    pass


class NoRelease(Exception):
    pass


def clock() -> float:
    return time.monotonic()


def sleep(seconds: float) -> None:
    """Wait between reads holding no install's lock, so a newer install's first save never waits on an install waiting for a release."""
    state.let_go_install()
    time.sleep(seconds)


def run(cmd: list[str], deadline: float, cwd: str | None = None) -> str:
    left = deadline - clock()
    if left <= 0:
        raise InstallError(f"no time left to run {' '.join(cmd)}")
    try:
        done = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=left, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise InstallError(f"{' '.join(cmd)} ran past the deadline ({left:.0f}s left when it started)") from None
    if done.returncode != 0:
        raise InstallError(f"{' '.join(cmd)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


@contextmanager
def update_lock(deadline: float) -> Iterator[None]:
    """Take turns on Claude Code's plugin store and on Homebrew with every other install on this Mac, holding no install's lock while waiting, and failing at the deadline with who holds the turn."""
    state.let_go_install()
    path = config_dir() / "plugins" / UPDATE_LOCK
    with open(path, "a") as handle:
        if not state.taken(handle, max(deadline - clock(), 0)):
            raise InstallError(f"another install held {path} past the deadline: {state.holders(path)}")
        yield


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


def listed(clone: str, plugin: str, deadline: float) -> tuple[dict, bool]:
    """The plugin's entry in the marketplace on the default branch, read over git, which the GitHub API's rate limit does not reach, and whether it is the marketplace's only plugin."""
    git(clone, *FETCH, f"+HEAD:{DEFAULT_BRANCH}", deadline=deadline)
    marketplace = json.loads(git(clone, "show", f"{DEFAULT_BRANCH}:.claude-plugin/marketplace.json", deadline=deadline))
    entry = next((entry for entry in marketplace["plugins"] if entry["name"] == plugin), None)
    if entry is None:
        raise UnknownPlugin(f"the {marketplace['name']} marketplace names no plugin {plugin}")
    return entry, len(marketplace["plugins"]) == 1


def folder_of(entry: dict) -> str:
    source = entry["source"]
    return str(PurePosixPath(source.get("path", ".") if isinstance(source, dict) else source))


def newest_release(clone: str, entry: dict, deadline: float) -> tuple[str, str] | None:
    """The release the marketplace names for the plugin, its tag or the newest commit of the default branch changing its manifest, and the plugin's folder in it."""
    source = entry["source"]
    if isinstance(source, dict):
        if not (tag := source.get("ref")):
            return None
        fetch_tag(clone, tag, deadline)
        return tag, folder_of(entry)
    manifest = PurePosixPath(source) / ".claude-plugin" / "plugin.json"
    return git(clone, "log", "-1", "--first-parent", "--format=%H", DEFAULT_BRANCH, "--", str(manifest), deadline=deadline).strip(), folder_of(entry)


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


def release_lacking(clone: str, repo: str, named: tuple[str, str] | None, sha: str, deadline: float) -> str | None:
    """What of the newest release the marketplace names for the plugin lacks sha, its commit or a binary it pins, or None once it holds sha."""
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


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def yaml_content(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def nested(lines: list[str], at: int) -> range:
    """The indexes of the YAML lines nested under lines[at]: those after it indented deeper, blank and comment lines among them."""
    end = next((i for i in range(at + 1, len(lines)) if yaml_content(lines[i]) and indent(lines[i]) <= indent(lines[at])), len(lines))
    return range(at + 1, end)


def child(lines: list[str], at: int, key: str) -> int | None:
    """The index of the line holding key directly under lines[at], at the depth of its first nested key."""
    keys = [i for i in nested(lines, at) if yaml_content(lines[i])]
    return next((i for i in keys if indent(lines[i]) == indent(lines[keys[0]]) and lines[i].strip().startswith(f"{key}:")), None)


def release_tag_paths(workflow: str) -> list[list[str]]:
    """The pathspecs each job of a workflow calling plugin-release-tag.yml passes as its paths input, split on whitespace as release_tag.py splits them; an empty list for a job naming none, which releases on any change."""
    lines = workflow.splitlines()
    jobs = []
    for at, line in enumerate(lines):
        if not RELEASE_TAG_JOB.fullmatch(line):
            continue
        job = next(i for i in range(at - 1, -1, -1) if yaml_content(lines[i]) and indent(lines[i]) < indent(line))
        given = child(lines, job, "with")
        paths = None if given is None else child(lines, given, "paths")
        if paths is None:
            jobs.append([])
            continue
        value = lines[paths].split(":", 1)[1].strip()
        jobs.append(("\n".join(lines[i] for i in nested(lines, paths)) if value[:1] in ("|", ">") else value.strip("\"'")).split())
    return jobs


def release_tag_jobs(clone: str, sha: str, deadline: float) -> list[tuple[str, list[str]]]:
    """Each job of the repo's workflows at sha calling plugin-release-tag.yml, as its workflow's path and its pathspecs, the workflows' blobs fetched in one read since the clone holds none."""
    files = [(meta.split()[2], path) for meta, path in (line.split("\t", 1) for line in git(clone, "ls-tree", sha, "--", WORKFLOWS, deadline=deadline).splitlines()) if meta.split()[1] == "blob"]
    if not files:
        return []
    git(clone, *FETCH, *(blob for blob, _ in files), deadline=deadline)
    return [(path, paths) for blob, path in files for paths in release_tag_paths(git(clone, "cat-file", "blob", blob, deadline=deadline))]


def newest_tag(clone: str, deadline: float) -> str | None:
    """The highest v* release tag on the remote, as release_tag.py versions from it, fetched; none when the repo has none, which release_tag.py waits on a hand tag for."""
    tags = [(int(major), int(minor), int(patch), tag) for tag, major, minor, patch in VERSION_TAG.findall(git(clone, "ls-remote", "--tags", "--refs", "origin", "refs/tags/v*", deadline=deadline))]
    if not tags:
        return None
    tag = max(tags)[3]
    fetch_tag(clone, tag, deadline)
    return tag


def unreleased(clone: str, plugin: str, entry: dict, alone: bool, sha: str, deadline: float) -> str | None:
    """Why no release of the plugin will hold sha, or None when one will, as release_tag.py plans at sha for each plugin-release-tag.yml job of the repo at sha that releases it, one that names a path in its folder or the marketplace's only plugin: a job naming no paths tags on any change; else it tags when a commit on sha's first-parent line since the newest v* tag changes one of its paths, unless that tag already holds sha. A plugin no such job releases, such as one Autobump bumps, is owed its release as before."""
    folder = PurePosixPath(folder_of(entry))
    jobs = [(workflow, paths) for workflow, paths in release_tag_jobs(clone, sha, deadline) if alone or any(PurePosixPath(path).is_relative_to(folder) for path in paths)]
    if not jobs or any(not paths for _, paths in jobs):
        return None
    workflows = ", ".join(sorted({workflow for workflow, _ in jobs}))
    if (tag := newest_tag(clone, deadline)) is None:
        return f"no release: the repo has no v* tag for {workflows} to version a {plugin} release from, so its first release is tagged by hand"
    if holds(clone, tag, sha, deadline):
        return None
    if any(git(clone, "log", "--first-parent", "--format=%H", f"{tag}..{sha}", "--", *paths, deadline=deadline).strip() for _, paths in jobs):
        return None
    return f"no release: no commit from {tag} to {sha[:12]} changes a path {workflows} tags a {plugin} release from, so the next release that does carries it"


def bare_clone(clone: str, repo: str, deadline: float) -> None:
    run(["git", "init", "--bare", "--quiet", clone], deadline)
    git(clone, "remote", "add", "origin", f"https://github.com/{repo}", deadline=deadline)


def await_release(clone: str, repo: str, plugin: str, sha: str, deadline: float, timeout: int) -> tuple[str, str]:
    """The newest release the marketplace names for the plugin once it holds sha, its ref and the plugin's folder in it; NoRelease at the first read where it lacks sha and the repo's release-tag workflow cuts no release holding it. Each read that fails is said as it fails, since a wait whose reads all fail looks like one for a release not out yet."""
    last = "no release read"
    failure = ""
    owed = False
    bare_clone(clone, repo, deadline)
    while True:
        try:
            entry, alone = listed(clone, plugin, deadline)
            named = newest_release(clone, entry, deadline)
            lacking = release_lacking(clone, repo, named, sha, deadline)
            failure = ""
            if named and lacking is None:
                return named
            if not owed:
                if reason := unreleased(clone, plugin, entry, alone, sha, deadline):
                    raise NoRelease(reason)
                owed = True
            if lacking != last:
                print(f"drive install: waiting for a {plugin} release holding {sha[:12]}: {lacking}", file=sys.stderr, flush=True)
            last = lacking
        except UnknownPlugin:
            raise
        except InstallError as error:
            failure = f" (last read failed: {error})"
            print(f"drive install: a read of the {plugin} release failed, reading again within {POLL_SECONDS}s: {error}", file=sys.stderr, flush=True)
        left = deadline - clock()
        if left <= 0:
            raise InstallError(f"timed out after {timeout}s waiting for a {plugin} release holding {sha[:12]}; {last}{failure}")
        sleep(min(POLL_SECONDS, left))


def check_holds(clone: str, repo: str, key: str, entry: dict, sha: str, deadline: float) -> None:
    """Refuse an update that left one scope's install of the plugin at a commit without the awaited sha, as a marketplace clone holding moved tags does, or running a binary from a release without it."""
    commit = entry["gitCommitSha"]
    scope = f"the {entry['scope']} scope" + (f" of {entry['projectPath']}" if entry.get("projectPath") else "")
    git(clone, *FETCH, commit, deadline=deadline)
    if not holds(clone, commit, sha, deadline):
        raise InstallError(f"{key} in {scope} is still at {entry['version']} ({commit[:12]}) after the update, which does not hold {sha[:12]}, the sha awaited")
    binruns = [path.read_text() for path in sorted(Path(entry["installPath"]).rglob("*.binrun"))]
    if binary := binary_lacking(clone, repo, binruns, sha, deadline):
        raise InstallError(f"{key} {entry['version']} in {scope} runs {binary} after the update, which does not hold {sha[:12]}, the sha awaited")


def await_homebrew(clone: str, binary: str, sha: str | None, deadline: float, timeout: int) -> None:
    """Upgrade the yasyf/tap formula or cask installing binary, the copy sessions run bare since Claude Code appends plugin bin directories after /opt/homebrew/bin on PATH, until the release bare binary prints with --version holds sha; each pass fast-forwards the tap's own clone first and upgrades with Homebrew's auto-update off, since brew update fetches every tap on the Mac and fails when any one of them cannot be fetched."""
    tap = run(["brew", "--repository", TAP], deadline).strip()
    last = ""
    while True:
        with update_lock(deadline):
            git(tap, "pull", "--ff-only", "--quiet", deadline=deadline)
            run(["env", "HOMEBREW_NO_AUTO_UPDATE=1", "brew", "upgrade", f"{TAP}/{binary}"], deadline)
        if sha is None:
            return
        tag = f"v{bare_release(binary, deadline)}"
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


def bare_release(binary: str, deadline: float) -> str:
    printed = run([binary, "--version"], deadline).strip()
    if not (version := RELEASE_VERSION.search(printed)):
        raise InstallError(f"bare {binary} --version printed {printed!r}, which names no release")
    return version.group()


def at_least(version: str, floor: str) -> bool:
    return tuple(map(int, RELEASE_VERSION.search(version).group().split("."))) >= tuple(map(int, floor.split(".")))


def app_release() -> str | None:
    """The captain-hook release Captain Hook.app holds, its Info.plist's CFBundleShortVersionString, the key captain-hook's hook.binrun reads to pick the release every session's hooks run in; None while no app is installed."""
    if not APP_PLIST.exists():
        return None
    return plistlib.loads(APP_PLIST.read_bytes())["CFBundleShortVersionString"]


def helper_release() -> str:
    """captain-hook's newest release, the one `capt-hook helper install` installs."""
    return github.read("captain-hook's newest release", ["gh", "release", "view", "-R", HELPER_REPO, "--json", "tagName", "-q", ".tagName"], max_age="5m").strip().removeprefix("v")


def helper_approved(drives: list[str], release: str) -> bool:
    """Whether an answer of his recorded for one of the drives picks Installs never need me, or Install now on the card asking to install release."""
    from drive import settled

    for drive in drives:
        cwd = state.load(drive)["roles"]["coordinator"]["cwd"]
        for answer in json.loads(settled.ccn(["ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"], "", cwd, "could not read his recorded answers")):
            picked = answer["body"].partition("\n")[0]
            if EVERY_INSTALL in picked or (INSTALL_NOW in picked and re.search(rf"\binstall {re.escape(release)} now\b", answer["title"])):
                return True
    return False


def helper_card(drives: list[str], release: str) -> str:
    """His card asking to install release: the ASK file a job of one of the drives keeps for it, or the words `drive job ask` asks it with."""
    title = HELPER_ASKED.format(release=release)
    asked = (question for drive in drives for job in state.load(drive)["jobs"].values() for question in job["questions"])
    if kept := next((question["file"] for question in asked if question["file"] and state.same_question(question["title"], title)), None):
        return f"his card {kept}"
    return f'his card "{title}" ({HELPER_OPTIONS}), asked with drive job ask'


def meet_helper(key: str, floor: str, deadline: float, drive: str | None) -> None:
    """Move Captain Hook.app onto captain-hook's newest release with `capt-hook helper install` when its Info.plist names a release below the floor the plugin needs, once he tapped Install now on the card asking it, since the restart reaches every session on this Mac; held with that card named until then."""
    with update_lock(deadline):
        if (runs := app_release()) and at_least(runs, floor):
            return
        try:
            release = helper_release()
            drives = [state.resolve(drive)]
            approved = helper_approved(drives, release)
            card = helper_card(drives, release)
        except (github.GithubError, state.DriveError, state.NewerDrive) as error:
            raise InstallError(f"{key} needs captain-hook {floor}, above the release Captain Hook.app runs, and whether he approved its restart did not read: {error}") from None
        if not at_least(release, floor):
            raise InstallError(f"{key} needs captain-hook {floor}, and its newest release is {release}")
        if not approved:
            raise InstallError(
                f"holds {key} for {card}: it needs captain-hook {floor}, and Captain Hook.app, which runs every hook, runs"
                f" {runs or 'no release'}; run this again once `drive answer` records his pick"
            )
        run([HELPER, "helper", "install"], deadline)
        if not ((runs := app_release()) and at_least(runs, floor)):
            raise InstallError(f"{HELPER} helper install left Captain Hook.app on {runs or 'no release'}, below captain-hook {floor}, which {key} needs")


def sibling_lacking(key: str, floor: str, deadline: float) -> str | None:
    """What of the sibling plugin runs a release below floor: one scope's install, or its Homebrew twin's bare binary, which sessions run before the plugin's own."""
    for entry in installs(key):
        if not at_least(entry["version"], floor):
            return f"{key} in the {entry['scope']} scope is at {entry['version']}"
    if (binary := HOMEBREW.get(key.partition("@")[0])) and not at_least(bare := bare_release(binary, deadline), floor):
        return f"bare {binary} runs {bare}"
    return None


def meet_sibling(key: str, floor: str, deadline: float, timeout: int, drive: str | None) -> None:
    """Install the sibling plugin until every install of it and its Homebrew twin run floor or above, waiting while its release is not out."""
    lacking = sibling_lacking(key, floor, deadline)
    last = None
    while lacking:
        install(key, None, None, deadline, timeout, drive)
        if not (lacking := sibling_lacking(key, floor, deadline)):
            return
        if lacking != last:
            print(f"drive install: waiting for {key} {floor}: {lacking}", file=sys.stderr, flush=True)
        last = lacking
        if deadline - clock() <= POLL_SECONDS:
            raise InstallError(f"timed out after {timeout}s waiting for {key} {floor}; {lacking}")
        sleep(POLL_SECONDS)


def newest_drive() -> Path:
    """The drive of the newest long-running installed, which each drive process running an older one hands its place to."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    return Path(newest([entry for key, entries in plugins.items() if key.partition("@")[0] == "long-running" for entry in entries])["installPath"]) / "bin" / "drive"


def upgraded() -> Path | None:
    """The drive of the newest installed long-running, when this code runs from the plugin cache and a newer one is installed."""
    if not DRIVE.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        return None
    drive = newest_drive()
    return None if drive.resolve() == DRIVE else drive


def reopen(argv: list[str]) -> None:
    """Hold this install's lock, then run `drive <argv>` from the newest install in place of this process when a newer one is installed, so what this process reads and writes of a drive from here on runs on the newest install, and an install landing later waits for it before its first save: a verb at its start, and a process that outlives its verb before each step, argv saying what it already did."""
    state.hold_install()
    if newer := upgraded():
        os.execv(newer, [str(newer), *argv])


def newest(entries: list[dict]) -> dict:
    return max(entries, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")


def installs(key: str) -> list[dict]:
    """Each install of the plugin a session can load: the user scope's, and each project or local scope's whose folder is still on this Mac. Claude Code loads the one its folder's scope names, and `claude plugin update` moves one scope, so each is updated and checked on its own."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not (found := [entry for entry in plugins.get(key, []) if "projectPath" not in entry or Path(entry["projectPath"]).is_dir()]):
        raise InstallError(f"{key} is not in installed_plugins.json in any scope whose folder is on this Mac")
    return found


def installed(key: str) -> dict:
    return newest(installs(key))


def install(key: str, sha: str | None, repo: str | None, deadline: float, timeout: int, drive: str | None) -> str:
    plugin, _, marketplace = key.partition("@")
    repo = repo or marketplace_repo(marketplace)
    with tempfile.TemporaryDirectory(prefix="drive-install-") as clone:
        if sha:
            named = await_release(clone, repo, plugin, sha, deadline, timeout)
        else:
            bare_clone(clone, repo, deadline)
            named = newest_release(clone, listed(clone, plugin, deadline)[0], deadline)
        if named is None:
            raise InstallError(f"the {marketplace} marketplace names no release of {plugin}, so what it needs of its siblings does not read")
        manifest = json.loads(git(clone, "show", f"{named[0]}:{PurePosixPath(named[1]) / MANIFEST}", deadline=deadline))
        if plugin == HOOKS:
            meet_helper(key, manifest["version"], deadline, drive)
        for each in manifest.get("dependencies", []):
            if not (floor := FLOOR.search(each["version"])):
                continue
            if each["name"] == HOOKS:
                meet_helper(key, floor[1], deadline, drive)
            meet_sibling(f"{each['name']}@{each['marketplace']}", floor[1], deadline, timeout, drive)
        with update_lock(deadline):
            state.hold_install()
            run(["claude", "plugin", "marketplace", "update", marketplace], deadline)
            for each in installs(key):
                run(["claude", "plugin", "update", key, "--scope", each["scope"]], deadline, each.get("projectPath"))
            entries = installs(key)
        if sha:
            for each in entries:
                check_holds(clone, repo, key, each, sha, deadline)
        entry = newest(entries)
        if after := AFTER_UPDATE.get(plugin):
            run([str(Path(entry["installPath"]) / after[0]), *after[1:]], deadline)
        if binary := HOMEBREW.get(plugin):
            await_homebrew(clone, binary, sha, deadline, timeout)
    return entry["version"]


def merged_ready(data: dict, job_id: str) -> bool:
    """Whether the job is a builder still waiting on its taken READY or PUSHED, every PR of which merged."""
    job = data["jobs"][job_id]
    return (
        job["kind"] == "builder"
        and job["state"] == "waiting"
        and bool(state.REPORTED_LINE.match(job["said"] or ""))
        and bool(job["prs"])
        and all(data["prs"][state.pr_key(url)]["merged_sha"] for url in job["prs"])
    )


def proof_gap(data: dict, job_id: str) -> str | None:
    """What keeps `drive job prove` from proving a builder whose PRs merged, naming who proves it instead, or None when it runs: a merge outside yasyf/* goes live only in its repository's release, and a job keeping no executable proof file leaves nothing to run."""
    job = data["jobs"][job_id]
    if released := [key for key in map(state.pr_key, job["prs"]) if not state.merges_untapped(key)]:
        return (
            f"{', '.join(released)} merged outside yasyf/*, where a merge goes live only in its repository's release, which no drive install puts on this Mac:"
            " a fresh builder with a merge handoff proves it from its INSTALL: and PROVE: lines once that release is out"
        )
    if not (job.get("repro") and os.access(job["repro"], os.X_OK)):
        return (
            f"{job_id} keeps no proof file to run: keep one with drive job set {job_id} --repro <file>, which the watch runs on the installed plugins,"
            " or start a fresh builder with a merge handoff that proves it from its INSTALL: and PROVE: lines"
        )
    return None


def live_builds(merges: dict[str, str], deadline: float, timeout: int, drive: str) -> dict[str, dict]:
    """The newest install of each plugin the merges change, holding them: a plugin whose newest install lacks one is installed from it first, as drive install --sha does."""
    builds = {}
    for repo in dict.fromkeys(key.partition("#")[0] for key in merges):
        shas = [sha for key, sha in merges.items() if key.partition("#")[0] == repo]
        with tempfile.TemporaryDirectory(prefix="drive-prove-") as clone:
            bare_clone(clone, repo, deadline)
            git(clone, *FETCH, *shas, deadline=deadline)
            for sha in shas:
                for plugin in touched(repo, git(clone, "diff-tree", "-r", "--name-only", f"{sha}^", sha, deadline=deadline).splitlines()):
                    build = installed(plugin)
                    git(clone, *FETCH, build["gitCommitSha"], deadline=deadline)
                    if not holds(clone, build["gitCommitSha"], sha, deadline):
                        install(plugin, sha, repo, deadline, timeout, drive)
                        build = installed(plugin)
                    builds[plugin] = build
    return builds


def prove(name: str, job_id: str, timeout: int) -> str:
    """Prove live a builder's READY or PUSHED whose PRs all merged, the step the watch starts: install each plugin a merge changes whose newest install lacks it, wait until the drive's dashboard runs the newest long-running installed, then run the job's proof file with those plugins' bin folders first on PATH, keep its output beside the job's report, take DONE on exit 0 and BLOCKED otherwise, an install that fails, a merge no release will carry or a dashboard still on an older install at the deadline taking BLOCKED naming it, and keep the close report on its order for the watch to post when DONE leaves the order nothing to do, but an open incident's, which the watch closes once DONE records its fix live, each said on its PRs' lines; refused for a job waiting on no such READY or PUSHED, or one proof_gap names; nothing after another proof took the job's result first. It holds its install's lock through none of it, as drive install holds none through its waits, so a drive a newer install saved meanwhile ends it with no result, for that install's watch to prove again."""
    from drive import brief, cli, dashboard

    deadline = clock() + timeout
    state.let_go_install()
    data = state.load(name)
    if not merged_ready(data, job_id):
        raise state.DriveError(f"{job_id} waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove")
    if why := proof_gap(data, job_id):
        raise state.DriveError(why)
    job = data["jobs"][job_id]
    merges = {state.pr_key(url): data["prs"][state.pr_key(url)]["merged_sha"] for url in job["prs"]}
    urls = " ".join(job["prs"])
    kept = state.report_path(name, job_id).with_suffix(".proof.txt")
    try:
        builds = live_builds(merges, deadline, timeout, name)
    except (InstallError, NoRelease) as error:
        code, output, versions = "unrun", str(error), "no install holding it"
        line = f"BLOCKED {urls}: not installed, so {job['repro']} did not run: {str(error)[:SAID_CHARS]}"
    else:
        versions = ", ".join(f"{plugin} {build['version']}" for plugin, build in builds.items()) or "no plugin of this Mac"
        path = os.pathsep.join([*(str(Path(build["installPath"]) / "bin") for build in builds.values()), os.environ.get("PATH", "")])
        try:
            dashboard.await_newest(name, deadline)
            done = subprocess.run([job["repro"]], check=False, capture_output=True, text=True, timeout=max(deadline - clock(), 0.001), env=os.environ | {"PATH": path})
            code, output = done.returncode, done.stdout + done.stderr
        except state.DriveError as error:
            code, output = "unrun", str(error)
        except subprocess.TimeoutExpired as expired:
            code, output = "past drive job prove's deadline", f"{expired.stdout or ''}{expired.stderr or ''}"
        except OSError as error:
            code, output = f"unrun ({error})", ""
        tail = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "no output")
        line = f"DONE {urls} live: {job['repro']} exits 0 on {versions}" if code == 0 else f"BLOCKED {urls}: {job['repro']} exits {code} on {versions}: {tail[:SAID_CHARS]}"
    state.let_go_install()
    proven = f"{PROVEN} on {versions}: {job['repro']} exits 0, its output {kept}"
    with state.edit(name) as data:
        job, order = data["jobs"][job_id], data["orders"][data["jobs"][job_id]["order"]]
        if job["state"] != "waiting" or not state.REPORTED_LINE.match(job["said"] or ""):
            return f"{job_id}: another proof took its result first"
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(output)
        seen = [found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)]
        whole = f"{line}\nPR: {urls}\nCHECK: {' '.join([str(kept), *seen])}\n"
        state.keep_report(name, job_id, line, whole, own=False)
        state.take_result(data, job_id, line, whole)
        unseen = cli.surface(data, order) if not seen else None
        closes = code == 0 and not unseen and all(state.delivered(data, job["order"], part) for part in order["parts"])
        closes = closes and all(data["jobs"][other]["state"] in state.FINISHED_JOB_STATES for other in order["jobs"]) and not state.open_incidents(data, [job["order"]])
        order_id, lane = job["order"], state.heard_lane(data, job["order"])
        if closes:
            title = "; ".join(data["prs"][pr]["title"] for pr in merges)
            order["proof"] = " ".join([str(kept), *seen])
            order["close_report"] = {
                "id": f"install-proved-{job_id}",
                "kind": "report",
                "to": [f"communicator@{name}"],
                "topic": f"install-proved-{order_id}",
                "attr": {"close": order_id} | ({"thread": order["thread"][1]} if order["thread"] else {}),
                "text": f"POST {title} is live in {versions}",
                "body": f"Fixed: {title}, {urls}\nLive: {versions}\nProof: {proven}\n",
            }
    change = proven
    if code:
        change = f"{PROOF_FAILED}: {line.removeprefix('BLOCKED ')}"
    elif unseen:
        change = f"its proof passed on {versions}, but he sees this on {unseen}: prove it there with a recording or the live post, then send the close report"
    elif closes:
        change = f"{proven}; its close report of {order_id} waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
    for pr in merges:
        merged_in, number = pr.split("#")
        state.append_event(name, "proof", repo=merged_in, number=int(number), change=change, lane=lane)
    return f"{job_id}: {change}"


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


def main(plugin_at_marketplace: str, sha: str | None, repo: str | None, timeout: int, drive: str | None) -> int:
    """Wait for a release holding sha, its commit and each binary it pins, bring each sibling its manifest there needs up to that release first, Captain Hook.app only once he tapped Install now on the drive's card for it, update the plugin and its Homebrew twin until the bare binary holds sha, print the installed version, then reload every drive session on this Mac. It holds its install's lock only from an update to its end, never while it waits on a release, Homebrew or another install's turn, so a newer install's first save of a drive waits on no wait of this one. A sha no release will hold prints why and installs nothing."""
    state.let_go_install()
    deadline = clock() + timeout
    try:
        version = install(plugin_at_marketplace, sha, repo, deadline, timeout, drive)
    except NoRelease as reason:
        print(reason)
        return 0
    except InstallError as error:
        print(f"drive install: {error}", file=sys.stderr)
        return 1
    updated = state.now()
    print(f"installed {plugin_at_marketplace} {version}")
    state.let_go_install()
    for line in reload_sessions(version, updated, deadline):
        print(line)
    return 0
