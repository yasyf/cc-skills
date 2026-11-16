import concurrent.futures
import hashlib
import json
import os
import plistlib
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from functools import cache, partial
from pathlib import Path, PurePosixPath

from drive import github, providers, state, train

POLL_SECONDS = 20
READ_EVERY_SECONDS = 60
CI_HEARD = "ci-heard.sqlite3"
CI_HEARD_SECONDS = 600
WATCH_MOVES_EVERY = 15 * 60
TRY_SECONDS = 180
TRIED = "tried"
STARTED = time.monotonic()
FAILED_TEST = re.compile(r"^\S+ (?:FAILED|ERROR) (?P<test>\S+\.py(?:::[^\r\n]+?)?)(?: - [^\r\n]*)?\r?$", re.MULTILINE)
GITHUB_REPO = re.compile(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$")
DEFAULT_BRANCH = "refs/heads/default"
INSTALLED_PROOF = " merged and installed in "
FETCH = ("fetch", "--quiet", "--filter=blob:none", "--no-tags", "origin")
UNREACHED = re.compile(r"unable to access '|RPC failed|early EOF|remote end hung up")
AFTER_UPDATE = {"cc-inbox": ("bin/cci", "serve"), "long-running": ("bin/drive", "migrate")}
HOMEBREW = {"cc-context": "ccx", "cc-inbox": "cci", "cc-notes": "cc-notes", "cc-orchestrate": "cco", "cc-present": "cc-present", "cc-review": "cc-review", "cc-slack": "cc-slack", "codex": "codex-ask"}
CASKS = {"cco": "cc-orchestrate"}
TAP = "yasyf/tap"
RELEASE_VERSION = re.compile(r"\d+\.\d+\.\d+")
VERSION_TAG = re.compile(r"refs/tags/(v(\d+)\.(\d+)\.(\d+))")
RELEASE_SUBJECT = re.compile(r"^chore\((release|plugins)\)")
WORKFLOWS = ".github/workflows/"
RELEASE_TAG_JOB = re.compile(r" *uses: *\S*/plugin-release-tag\.yml(?:@\S+)?\s*")
RELOADS = "reloads.json"
RELOAD_LOCKS = "reloads"
RELOADS_LOG = "reloads.log"
RELOADED = "<local-command-stdout>Reloaded:"
UPDATE_LOCK = ".update.lock"
RELOAD_WAIT_SECONDS = 30
RELOAD_EVERY_SECONDS = 3600
RELOAD_RETRY_SECONDS = 30
TURN_ENDS = "turn-ends"
TURN_END_SECONDS = 5
RELOAD_TRIES = 4
RELOAD_DEFECT_AFTER = 2
RETRIED = ("not reloaded", "/reload-plugins typed in")
LANE_RELOAD = "watch"
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
MANIFEST = ".claude-plugin/plugin.json"
FLOOR = re.compile(r">=(\d+\.\d+\.\d+)")
HOOKS = "captain-hook"
LONG_RUNNING = "long-running"
HELPER = "capt-hook"
HELPER_REPO = "yasyf/captain-hook"
APP_PLIST = Path.home() / "Applications" / "Captain Hook.app" / "Contents" / "Info.plist"
HELPER_ASKED = "Captain-hook update, your Mac: install {release} now? It restarts the hook helper once"
INSTALL_NOW = "Install now"
EVERY_INSTALL = "Installs never need me"
HELPER_OPTIONS = f"{INSTALL_NOW} (recommended), {EVERY_INSTALL} and Not now"
PROVEN = "proven live by drive job prove"
PROOF_FAILED = "drive job prove failed"
PROOF_WAITS = "drive job prove waits on his next word"
PROVER_WAIT = "or on its WAIT line keeps you waiting and runs it again once he is next heard, never a process of your own waiting for the change to show itself"
PROOF_DEADLINE_ENV = "CLAUDE_LONG_RUNNING_PROOF_DEADLINE"
SAID_CHARS = 300
NO_JOB = "j0"


class InstallError(Exception):
    pass


class UnknownPlugin(InstallError):
    pass


class NoRelease(Exception):
    pass


def clock() -> float:
    return time.monotonic()


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def ran(cmd: list[str], deadline: float, cwd: str | None = None) -> subprocess.CompletedProcess:
    left = deadline - clock()
    if left <= 0:
        raise InstallError(f"no time left to run {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=left, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise InstallError(f"{' '.join(cmd)} ran past the deadline ({left:.0f}s left when it started)") from None


def run(cmd: list[str], deadline: float, cwd: str | None = None) -> str:
    done = ran(cmd, deadline, cwd)
    if done.returncode != 0:
        raise InstallError(f"{' '.join(cmd)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


@contextmanager
def update_lock(deadline: float) -> Iterator[None]:
    """Take turns on Claude Code's plugin store and on Homebrew with every other install on this Mac, failing at the deadline with who holds the turn."""
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
    """Run git in clone, again within POLL_SECONDS until the deadline while git says it could not reach the remote, a refused connection or a transfer cut short, which says nothing of what the command reads; each such failure said as it fails."""
    while True:
        try:
            return run(["git", "-C", clone, *args], deadline)
        except InstallError as error:
            if not UNREACHED.search(str(error)) or deadline - clock() <= POLL_SECONDS:
                raise
            print(f"drive install: git could not reach the remote, running it again within {POLL_SECONDS}s: {error}", file=sys.stderr, flush=True)
            sleep(POLL_SECONDS)


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
    """Why no release of the plugin will hold sha, or None when one will, as release_tag.py plans at sha for each plugin-release-tag.yml job of the repo at sha that releases it, one that names a path in its folder or the marketplace's only plugin: a job naming no paths tags on any change; else it tags when a commit on sha's first-parent line since the newest v* tag changes one of its paths, unless that tag already holds sha or the commit is a release's own, whose subject RELEASE_SUBJECT matches as release_tag.py skips it. A plugin no such job releases, such as one Autobump bumps, is owed its release as before."""
    folder = PurePosixPath(folder_of(entry))
    jobs = [(workflow, paths) for workflow, paths in release_tag_jobs(clone, sha, deadline) if alone or any(PurePosixPath(path).is_relative_to(folder) for path in paths)]
    if not jobs or any(not paths for _, paths in jobs):
        return None
    workflows = ", ".join(sorted({workflow for workflow, _ in jobs}))
    if (tag := newest_tag(clone, deadline)) is None:
        return f"no release: the repo has no v* tag for {workflows} to version a {plugin} release from, so its first release is tagged by hand"
    if holds(clone, tag, sha, deadline):
        return None
    if any(not RELEASE_SUBJECT.match(subject) for _, paths in jobs for subject in git(clone, "log", "--first-parent", "--format=%s", f"{tag}..{sha}", "--", *paths, deadline=deadline).splitlines()):
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


def install_lacking(clone: str, repo: str, key: str, entry: dict, sha: str, deadline: float) -> str | None:
    """What of one scope's install of the plugin lacks sha: its commit, as a marketplace clone holding moved tags leaves it, or a binary it runs from a release without it."""
    commit = entry["gitCommitSha"]
    scope = f"the {entry['scope']} scope" + (f" of {entry['projectPath']}" if entry.get("projectPath") else "")
    git(clone, *FETCH, commit, deadline=deadline)
    if not holds(clone, commit, sha, deadline):
        return f"{key} in {scope} is still at {entry['version']} ({commit[:12]})"
    binruns = [path.read_text() for path in sorted(Path(entry["installPath"]).rglob("*.binrun"))]
    if binary := binary_lacking(clone, repo, binruns, sha, deadline):
        return f"{key} {entry['version']} in {scope} runs {binary}"
    return None


def runs_lacking(clone: str, repo: str, key: str, sha: str, deadline: float) -> bool:
    """Whether a build sessions on this Mac run of the plugin lacks sha: one scope's install, its Homebrew twin's bare binary, which sessions run before the plugin's own, or for captain-hook Captain Hook.app, whose release runs every hook whatever release the plugin's files are at, as when Claude Code updates the plugin on its own."""
    plugin = key.partition("@")[0]
    if any(install_lacking(clone, repo, key, entry, sha, deadline) for entry in installs(key)):
        return True
    if (binary := HOMEBREW.get(plugin)) and bare_lacking(clone, binary, sha, deadline):
        return True
    if plugin != HOOKS:
        return False
    if not (app := app_release()):
        return True
    fetch_tag(clone, f"v{app}", deadline)
    return not holds(clone, f"v{app}", sha, deadline)


def await_homebrew(clone: str, binary: str, sha: str | None, deadline: float, timeout: int) -> None:
    """Upgrade the yasyf/tap formula or cask installing binary, the copy sessions run bare since Claude Code appends plugin bin directories after /opt/homebrew/bin on PATH, until the release bare binary prints with --version holds sha; each pass fast-forwards the tap's own clone first and upgrades with Homebrew's auto-update off, since brew update fetches every tap on the Mac and fails when any one of them cannot be fetched."""
    tap = run(["brew", "--repository", TAP], deadline).strip()
    token = f"{TAP}/{CASKS.get(binary, binary)}"
    last = ""
    while True:
        with update_lock(deadline):
            git(tap, "pull", "--ff-only", "--quiet", deadline=deadline)
            run(["env", "HOMEBREW_NO_AUTO_UPDATE=1", "brew", "upgrade", token], deadline)
        if sha is None or not (lacking := bare_lacking(clone, binary, sha, deadline)):
            return
        if lacking != last:
            print(f"drive install: waiting for {token} to hold {sha[:12]}: {lacking}", file=sys.stderr, flush=True)
        last = lacking
        if deadline - clock() <= POLL_SECONDS:
            raise InstallError(f"timed out after {timeout}s waiting for bare {binary} to hold {sha[:12]}; {lacking}")
        sleep(POLL_SECONDS)


def bare_lacking(clone: str, binary: str, sha: str, deadline: float) -> str | None:
    """The release bare binary runs when it lacks sha, the tag of the release it prints with --version."""
    tag = f"v{bare_release(binary, deadline)}"
    fetch_tag(clone, tag, deadline)
    return None if holds(clone, tag, sha, deadline) else f"bare {binary} runs release {tag}, which lacks it"


def bare_release(binary: str, deadline: float) -> str:
    """The release the Homebrew copy of binary prints with --version, the copy a session runs bare, read from Homebrew's own bin folder, since a process the drive starts runs the plugin's copy first."""
    printed = run([str(Path(run(["brew", "--prefix"], deadline).strip()) / "bin" / binary), "--version"], deadline).strip()
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
    """Whether he picked Installs never need me, or Install now on the card asking to install release: by his tap, which ends the card's question on one of the drives seconds after it, or by an answer of his recorded for one of them, which `drive answer` keeps a minute or two later."""
    from drive import settled

    for drive in drives:
        data = state.load(drive)
        tapped = [(question["title"] or "", question["closed"]["words"] or "") for job in data["jobs"].values() for question in job["questions"] if (question["closed"] or {}).get("how") == "answered"]
        recorded = json.loads(settled.ccn(["ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"], "", data["roles"]["coordinator"]["cwd"], "could not read his recorded answers"))
        for title, picked in tapped + [(answer["title"], settled.his(answer["body"]).partition("\n")[0]) for answer in recorded]:
            if EVERY_INSTALL in picked or (INSTALL_NOW in picked and re.search(rf"\binstall {re.escape(release)} now\b", title)):
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
        except (github.GithubError, state.DriveError) as error:
            raise InstallError(f"{key} needs captain-hook {floor}, above the release Captain Hook.app runs, and whether he approved its restart did not read: {error}") from None
        if not at_least(release, floor):
            raise InstallError(f"{key} needs captain-hook {floor}, and its newest release is {release}")
        if not approved:
            raise InstallError(
                f"WAIT holds {key} for {card}: it needs captain-hook {floor}, and Captain Hook.app, which runs every hook, runs"
                f" {runs or 'no release'}; run this again once his tap or `drive answer` records his pick"
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
    return Path(newest([entry for key, entries in plugins.items() if key.partition("@")[0] == LONG_RUNNING for entry in entries])["installPath"]) / "bin" / "drive"


def upgraded() -> Path | None:
    """The drive of the newest installed long-running, when this code runs from the plugin cache and a newer one is installed."""
    if not DRIVE.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        return None
    drive = newest_drive()
    return None if drive.resolve() == DRIVE else drive


def watch_upgraded() -> Path | None:
    """The drive of the newest installed long-running when it runs other code than this one's, so the watch moves onto an install only when that install changes what the watch runs: a newer install whose lib and bin/drive are byte for byte this one's is not one, unless this install's folder is gone, as a pruned cache leaves it. A watch process moves at most once per WATCH_MOVES_EVERY of its own run, onto the newest install then, so a burst of releases restarts it once rather than once per release, each restart owing GitHub a read of every PR it holds."""
    if time.monotonic() - STARTED < WATCH_MOVES_EVERY or not (newer := upgraded()):
        return None
    if not DRIVE.exists():
        return newer
    if code_digest(newer.resolve().parents[1]) == code_digest(DRIVE.parents[1]):
        return None
    return newer if watch_tried(newer.resolve().parents[1]) else None


def tried_path(plugin: Path) -> Path:
    return state.root() / TRIED / f"{code_digest(plugin)}.json"


def watch_tried(plugin: Path) -> bool:
    """Whether the install at plugin passed one relay and one watch pass of its own on a copy of every drive here, by try_watch, which runs once per install's code in a process of its own, `drive try-install`, its verdict kept by the code's digest, so no watch moves onto an install whose passes crash, a hand `claude plugin update` included, and no watch pass waits on the try; False while the try runs or after it failed. A watch whose own install was removed moves without it, since it has no older code left to stay on."""
    if (verdict := tried_path(plugin)).exists():
        return json.loads(verdict.read_text())["passed"]
    running = verdict.with_suffix(".running")
    if running.exists() and time.time() - running.stat().st_mtime < TRY_SECONDS * 2:
        return False
    running.parent.mkdir(parents=True, exist_ok=True)
    running.touch()
    with (state.root() / TRIED / "tries.log").open("a") as out:
        subprocess.Popen([str(plugin / "bin" / "drive"), "try-install", str(plugin)], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return False


def try_install(plugin: Path) -> bool:
    """Run try_watch on the install at plugin and keep its verdict for watch_tried; a crash sends the meta session of each drive one defect naming the install, the stage and its traceback, once per install's code."""
    from drive import tools

    names = state.names()
    try:
        try_watch(plugin, names, clock() + TRY_SECONDS)
        said = ""
    except InstallError as error:
        said = str(error)
        for name in names:
            tools.sent(name, "watch", [{"kind": "defect", "to": [f"meta@{name}"], "text": f"no watch moves onto {plugin.name}: {said}", "id": f"try-install-{code_digest(plugin)[:12]}"}])
    tried_path(plugin).parent.mkdir(parents=True, exist_ok=True)
    tried_path(plugin).write_text(json.dumps({"passed": not said, "said": said, "at": state.stamp()}))
    tried_path(plugin).with_suffix(".running").unlink(missing_ok=True)
    return not said


def try_root(deadline: float) -> str | None:
    """The folder try_watch copies the drives into: the one global git config's long-running.scratch names, else the system's temporary folder."""
    return ran(["git", "config", "--global", "--get", "long-running.scratch"], deadline).stdout.strip() or None


def try_watch(plugin: Path, names: list[str], deadline: float) -> None:
    """Run the install at plugin's tools/try-watch on a copy of each drive named, deleted once it ends, raising InstallError with what it printed when a pass of its own crashes."""
    if not names:
        return
    if root := try_root(deadline):
        Path(root).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="drive-try-watch-", dir=root) as copies:
        for name in names:
            state.clone(name, Path(copies))
        done = ran(["env", f"LONG_RUNNING_STATE={copies}", str(plugin / "tools" / "try-watch"), *names], deadline)
    if done.returncode:
        raise InstallError(f"one relay and one watch pass of its own crashed on a copy of the drives:\n{done.stderr.strip() or done.stdout.strip()}")


@cache
def code_digest(plugin: Path) -> str:
    """One digest of the Python the drive runs from an install's folder: bin/drive and each module under lib, by path and bytes."""
    digest = hashlib.sha256()
    for path in sorted([plugin / "bin" / "drive", *(plugin / "lib").rglob("*.py")]):
        digest.update(str(path.relative_to(plugin)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def reopen(argv: list[str]) -> None:
    """Run `drive <argv>` from the newest install in place of this process when a newer one is installed, so what this process reads and writes of a drive from here on runs on the newest install: a verb at its start, and a process that outlives its verb before each step, argv saying what it already did."""
    if newer := upgraded():
        os.execv(newer, [str(newer), *argv])


def newest(entries: list[dict]) -> dict:
    return max(entries, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")


def installs(key: str) -> list[dict]:
    """Each install of the plugin a session can load: the user scope's, and for each git repository the first project or local scope's whose folder is still on this Mac. Claude Code takes a repository's checkout and its worktrees for one project: a session started in any of them loads, and `claude plugin update` run in any of them moves, that repository's first entry in installed_plugins.json, so a later entry, such as the one an Orca workspace of the home repository records when its first session starts, is never loaded or updated. Each install listed is updated and checked on its own, since `claude plugin update` moves one."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    found, projects = [], set()
    for entry in plugins.get(key, []):
        if "projectPath" in entry:
            if not Path(entry["projectPath"]).is_dir() or (project := (entry["scope"], repository(entry["projectPath"]))) in projects:
                continue
            projects.add(project)
        found.append(entry)
    if not found:
        raise InstallError(f"{key} is not in installed_plugins.json in any scope whose folder is on this Mac")
    return found


def repository(folder: str) -> str:
    common = subprocess.run(["git", "-C", folder, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=False)
    return common.stdout.strip() if common.returncode == 0 else folder


def installed(key: str) -> dict:
    return newest(installs(key))


def prefetch(clone: str, ref: str, folder: str, deadline: float) -> None:
    """Fetch every blob of the folder at ref into the blobless clone in one fetch, as git's own promisor fetch asks for them, since `git checkout <ref> -- <folder>` fetches each missing blob in a process of its own: 440 of them for long-running, 3 minutes 8 seconds on this Mac against 2 seconds for one fetch, and refused when any one of them times out."""
    blobs = git(clone, "ls-tree", "-r", "--object-only", ref, "--", folder, deadline=deadline).split()
    git(clone, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "origin", *blobs, deadline=deadline)


def try_drives(clone: str, ref: str, folder: str, deadline: float) -> None:
    """Run the release's own drive status on a copy of each drive on this Mac before any scope switches to it, then one relay and one watch pass of its own on those copies with no process, signal or network, by tools/try-watch, so a stage that would crash on a live drive's rows never reaches its watch, then its drive status of a job no drive holds, refusing a release that cannot read a drive or that answers that refusal with anything but its `drive:` line and exit 2, how every caller of drive reads a refusal, since every drive verb, tool, watch and hook runs the newest install."""
    if not (names := state.names()):
        return
    with tempfile.TemporaryDirectory(prefix="drive-try-") as scratch:
        tree, copies = Path(scratch) / "release", Path(scratch) / "state"
        tree.mkdir()
        prefetch(clone, ref, folder, deadline)
        git(clone, f"--work-tree={tree}", "checkout", ref, "--", folder, deadline=deadline)
        for name in names:
            state.clone(name, copies)
        drive = ["env", f"LONG_RUNNING_STATE={copies}", str(tree / folder / "bin" / "drive")]
        for name in names:
            try:
                run([*drive, "status", "--drive", name], deadline)
            except InstallError as error:
                raise InstallError(f"refused {ref}, keeping the release installed now: its own drive status failed on a copy of the {name} drive: {error}") from None
        try:
            try_watch(tree / folder, names, deadline)
        except InstallError as error:
            raise InstallError(f"refused {ref}, keeping the release installed now: {error}") from None
        refusal = ran([*drive, "status", NO_JOB, "--drive", names[0]], deadline)
        if refusal.returncode != 2 or not refusal.stderr.startswith("drive: "):
            raise InstallError(
                f"refused {ref}, keeping the release installed now: its own drive status {NO_JOB} on a copy of the {names[0]} drive exited {refusal.returncode}"
                f" without printing its `drive:` refusal and exiting 2: {refusal.stderr.strip()}"
            )


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
        if plugin == LONG_RUNNING:
            try_drives(clone, *named, deadline)
        with update_lock(deadline):
            run(["claude", "plugin", "marketplace", "update", marketplace], deadline)
            for each in installs(key):
                run(["claude", "plugin", "update", key, "--scope", each["scope"]], deadline, each.get("projectPath"))
            entries = installs(key)
        if sha:
            for each in entries:
                if lacking := install_lacking(clone, repo, key, each, sha, deadline):
                    raise InstallError(f"{lacking} after the update, which does not hold {sha[:12]}, the sha awaited")
        entry = newest(entries)
        if sha:
            for line in close_installed(clone, repo, key, entry["gitCommitSha"], entry["version"], deadline):
                print(line)
        if after := AFTER_UPDATE.get(plugin):
            run([str(Path(entry["installPath"]) / after[0]), *after[1:]], deadline)
        if binary := HOMEBREW.get(plugin):
            await_homebrew(clone, binary, sha, deadline, timeout)
    return entry["version"]


def reported_prs(data: dict, job_id: str) -> list[str]:
    """The drive PRs a builder's said line names, its READY or PUSHED's PRs or those the DONE the watch took at their merge copies, the ones its proof proves: a PR registered on the job that the line leaves out, such as one closed as superseded, owes the proof nothing; on any other line, such as a WAITING, which names what it waits on, such as a release, or a DONE of its own, whose one-line proof may cite PRs other jobs opened, as a port-gap pass names the PRs holding each gap, each PR registered on the job but one closed unmerged, so a PR the line only cites is never its to prove."""
    job = data["jobs"][job_id]
    if state.REPORTED_LINE.match(said := job["said"] or "") or state.MERGED_LINE.match(said):
        return state.named_prs(data, job["order"], said)
    return [key for key in dict.fromkeys(map(state.pr_key, job["prs"])) if key in data["prs"] and data["prs"][key]["state"] != "closed"]


def merged_ready(data: dict, job_id: str) -> bool:
    """Whether the job is a builder still waiting on its taken READY or PUSHED, or on the DONE state.hold_done held until its PRs merge, every PR of which merged."""
    job = data["jobs"][job_id]
    return (
        job["kind"] == "builder"
        and job["state"] == "waiting"
        and bool(state.REPORTED_LINE.match(job["said"] or "") or state.HELD_DONE.match(job["said"] or ""))
        and bool(keys := reported_prs(data, job_id))
        and all(data["prs"][key]["merged_sha"] for key in keys)
    )


def repair(data: dict, job_id: str) -> bool:
    """Whether the job is a builder that opened no PR, its said line naming none of its own, as reported_prs reads it, and proving none, as proves reads its goal, such as a hand repair of the drive's records, a staging tester whose brief cites the PR it tests or a pass whose DONE only cites PRs: drive job prove installs nothing for it and runs its proof file alone, on its own agent's call while it runs, on one take_back reopens, or again on the proof it marked waiting."""
    job = data["jobs"][job_id]
    return job["kind"] == "builder" and not job["prs"] and not reported_prs(data, job_id) and not proves(data, job_id)


def handed_prs(data: dict, job_id: str) -> list[str]:
    """The PRs a prover proves live, those its goal names, as proves reads them, on a builder that opened no PR and waits on nothing, such as the release holding a merge, but its own after-merge staging waits, which its agent met by running that pass and the watch drops only at its next release live read, and the his-word wait its own proof's WAITING keeps, whose proof the watch runs again once he is heard, once each merged; none for any other job."""
    job = data["jobs"][job_id]
    keys = proves(data, job_id)
    if job["prs"] or any(not (state.STAGING_WAIT.fullmatch(wait) or state.HIS_WORD_WAIT.fullmatch(wait)) for wait in job["waits"]) or not all(data["prs"][key]["merged_sha"] for key in keys):
        return []
    return keys


def proved_prs(data: dict, job_id: str) -> list[str]:
    """The merged PRs drive job prove proves on the job: a prover's, as handed_prs reads them, else its said line's, as reported_prs reads them, but of a line naming a yasyf/* PR only its yasyf/* ones, since a drive install puts none of the others live and the prover add_prover hands them to proves them on their release; never one still open, such as the PR a staging tester's DONE names, which no proof proves live."""
    reported = reported_prs(data, job_id)
    keys = handed_prs(data, job_id) or [key for key in reported if state.merges_untapped(key)] or reported
    return [key for key in keys if data["prs"][key]["merged_sha"]]


def own_proof(data: dict, job_id: str) -> bool:
    """Whether the job's own agent starts its first proof: a repair, or a prover, which handed_prs reads."""
    return repair(data, job_id) or bool(handed_prs(data, job_id))


def done_unproven(data: dict, job_id: str) -> bool:
    """Whether the job's own DONE, as own_done reads it, is one drive job prove proves by running its proof file: one the drive's own_done_proofs_since, when the drive was made before such a proof became the one way a DONE delivers work, never names as taken before it, so no older DONE gets a first proof on an install."""
    return own_done(data, job_id) and not before_own_done_proofs(data, job_id)


def done_before_proofs(data: dict, job_id: str) -> bool:
    """Whether the job's own DONE was taken before the drive's own_done_proofs_since, so no proof of it is to come and no gap is said on the parts it holds: its said line, as own_done reads it, or, on a done builder or codex job owing work whose said line an older install kept without its DONE, such as `#53689 live: …`, its newest return opening DONE."""
    if not before_own_done_proofs(data, job_id):
        return False
    job = data["jobs"][job_id]
    if own_done(data, job_id):
        return True
    if job["kind"] not in state.CHANGE_KINDS or job["state"] != "done" or (job["said"] or "").startswith("DONE") or not state.owed_work(data, job_id):
        return False
    return next((state.opening(reply["body"]) for reply in state.returns(data["name"], job_id)), "").startswith("DONE")


def own_done(data: dict, job_id: str) -> bool:
    """Whether a builder or codex job returned DONE of its own, never the DONE the watch takes at a merge nor one drive job prove took, on work he ordered that no proof delivered yet or on a staging test whose user stories its proof file proves, by brief.staged_stories."""
    from drive import brief

    job = data["jobs"][job_id]
    said = job["said"] or ""
    owed = bool(state.owed_work(data, job_id)) or brief.staged_stories(job)
    return job["kind"] in state.CHANGE_KINDS and job["state"] == "done" and said.startswith("DONE") and not state.MERGED_LINE.match(said) and not state.PROVEN_LINE.match(said) and owed


def before_own_done_proofs(data: dict, job_id: str) -> bool:
    job = data["jobs"][job_id]
    return (since := data["own_done_proofs_since"]) is not None and state.parse_time(job["returned"] or job["updated"]) < state.parse_time(since)


def provable(data: dict, job_id: str) -> bool:
    """Whether drive job prove runs on the job: a builder waiting on a READY or PUSHED whose PRs all merged, a repair or prover at work or waiting, or a DONE of a job's own on work no proof delivered yet."""
    return merged_ready(data, job_id) or own_proof(data, job_id) and data["jobs"][job_id]["state"] in ("running", "waiting") or done_unproven(data, job_id)


def watch_proves(data: dict, job_id: str) -> bool:
    """Whether the watch proves the job: a builder waiting on a READY or PUSHED whose PRs all merged, a DONE of a job's own on work no proof delivered yet, or a repair or prover waiting on the proof drive job prove marked on its said line, since only its own drive job prove starts its first proof."""
    job = data["jobs"][job_id]
    return merged_ready(data, job_id) or done_unproven(data, job_id) or own_proof(data, job_id) and job["state"] == "waiting" and bool(job.get("proof")) and job["proof"]["said"] == job["said"]


def take_back(name: str, data: dict, job_id: str) -> None:
    """Put a job that took BLOCKED, such as the BLOCKED its own proof took, back on the newest READY, PUSHED or DONE of its own its history keeps, a DONE as done, never the DONE the watch takes at a merge, and a stopped or failed repair or prover back on its said line, waiting, its proof marked as this process's as the watch marks one it starts, so drive job prove proves that line again on the job that owns it: prove puts the job back as it found it while the file runs, which no watch proves while it stays failed or stopped, and proof_kept takes it back again for the result it takes."""
    job = data["jobs"][job_id]
    lines = (state.opening(reply["body"]) for reply in state.returns(name, job_id))
    reported = job["state"] == "failed" and next((line for line in lines if state.REPORTED_LINE.match(line) or line.startswith("DONE") and not state.MERGED_LINE.match(line)), None)
    if (said := reported or job["said"]) and (reported or job["state"] in ("failed", "stopped") and own_proof(data, job_id)):
        taken = "done" if said.startswith("DONE") else "waiting"
        job.update(state=taken, said=said, updated=state.stamp(), proof={"pid": os.getpid(), "at": state.stamp(), "said": said, "version": state.VERSION})


def released_prs(data: dict, job_id: str) -> list[str]:
    """The PRs the job's said line names, as reported_prs reads them, that merge outside yasyf/*, which go live only in their repository's release, never on a drive install, so the builder add_prover hands them to proves them."""
    return [key for key in reported_prs(data, job_id) if not state.merges_untapped(key)]


def product(data: dict, job_id: str) -> bool:
    """Whether the job's READY or PUSHED names PRs that all merge outside yasyf/*, a product change, which no drive install puts on this Mac."""
    return bool(keys := reported_prs(data, job_id)) and released_prs(data, job_id) == keys


def proof_gap(data: dict, job_id: str) -> str | None:
    """What keeps `drive job prove` from proving a builder whose PRs merged, or a DONE of a job's own, naming who proves it instead, or None when it runs on its yasyf/* merges, the prover add_prover hands its others to proving those: a job whose every merge is outside yasyf/* goes live only in its repository's release, but a DONE of a job's own, such as the builder proving that release, runs on no merge, and a prover, as handed_prs reads it, proves those merges on that release, its said line a failed proof's BLOCKED naming them included, and a job keeping no proof file the kernel can exec, as state.unrunnable reads it, leaves nothing to run."""
    job = data["jobs"][job_id]
    if product(data, job_id) and not done_unproven(data, job_id) and not handed_prs(data, job_id):
        return (
            f"{', '.join(reported_prs(data, job_id))} merged outside yasyf/*, where a merge goes live only in its repository's release, which no drive install"
            " puts on this Mac: a fresh builder with a merge handoff, added with drive job add builder --builds-nothing --goal \"prove <PRs> live\", keeps a proof file written from its INSTALL: and PROVE: lines"
            " once that release is out and runs drive job prove on itself"
        )
    repro = job.get("repro") if not job.get("repros") else next((file for file in job["repros"].values() if state.unrunnable(file)), next(iter(job["repros"].values())))
    why = state.unrunnable(repro) if repro else None
    if not repro or why:
        return f"{job_id} keeps no proof file to run{f', as {why}' if why else ''}: keep one with drive job set {job_id} --repro <file>, written from its PROVE: line, then drive job prove {job_id} runs it on the installed plugins"
    return None


def merged_plugins(merges: dict[str, str], deadline: float, timeout: int, drive: str) -> list[str]:
    """Each plugin the merges change, installed holding them: a plugin a build sessions run of which lacks one, as runs_lacking reads it, is installed from it first, as drive install --sha does."""
    plugins = []
    for repo in dict.fromkeys(key.partition("#")[0] for key in merges):
        shas = [sha for key, sha in merges.items() if key.partition("#")[0] == repo]
        with tempfile.TemporaryDirectory(prefix="drive-prove-") as clone:
            bare_clone(clone, repo, deadline)
            git(clone, *FETCH, *shas, deadline=deadline)
            for sha in shas:
                for plugin in touched(repo, git(clone, "diff-tree", "-r", "--name-only", f"{sha}^", sha, deadline=deadline).splitlines()):
                    if runs_lacking(clone, repo, plugin, sha, deadline):
                        install(plugin, sha, repo, deadline, timeout, drive)
                    plugins.append(plugin)
    return list(dict.fromkeys(plugins))


def newest_builds(plugins: list[str]) -> tuple[str, str]:
    """The versions line and the PATH, their bin folders first, of the newest install of each plugin, read afresh before each run of a proof file, since landers install newer ones, each holding what the one before it held, while a proof waits on CI or runs; the inherited PATH follows them, since the proof file names its own interpreter, as state.pin_interpreter pinned it when drive job set kept it."""
    builds = {plugin: installed(plugin) for plugin in plugins}
    versions = ", ".join(f"{plugin} {build['version']}" for plugin, build in builds.items()) or "no plugin of this Mac"
    return versions, os.pathsep.join([*(str(Path(build["installPath"]) / "bin") for build in builds.values()), os.environ.get("PATH", "")])


def plugin_copies_first() -> str:
    """The PATH with the bin folder of the newest install of each plugin with a Homebrew twin ahead of it, so a process the drive starts runs that plugin's versioned copy, which no upgrade removes, rather than the Homebrew copy brew upgrade unlinks until it links the new keg."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    return os.pathsep.join([*(str(Path(newest(entries)["installPath"]) / "bin") for key, entries in plugins.items() if key.partition("@")[0] in HOMEBREW), os.environ.get("PATH", "")])


def await_ci(merges: dict[str, str]) -> bool:
    """Follow the CI of the push that carried each merge onto its repository's default branch until it passed the merge, as pushed_ci judges it and drive pr ci prints it, however many runs or statuses failed or were cancelled before it, since each one on that branch tests the merge again beside every merge after it; a merge no CI reported on, on its commit or any commit after it, waits on nothing. The verdict is read again each time the receiver hears a workflow run complete on that branch, at most once every READ_EVERY_SECONDS, since a busy branch completes runs of other merges far more often than this one's CI changes, the repository hooked first when no hook of the receiver's sends those, once a proof, and a read that fails is said and read again within POLL_SECONDS; no deadline cuts the wait, since the next push's CI tests the merge again, so a proof waits through a red default branch until it passes. Whether a read found CI that had not passed the merge yet."""
    from drive import receiver

    waited = False
    for key, sha in merges.items():
        repo, last, hooked = key.partition("#")[0], "", False
        while True:
            heard, failed, read_at = receiver.heard(repo, "ci"), False, clock()
            try:
                if not hooked and receiver.unhooked({repo}):
                    receiver.hook(repo)
                hooked = True
                if not (unpassed := [line for line, passed in heard_ci(repo, sha, heard) if not passed]):
                    break
                waiting, waited = ", ".join(unpassed), True
            except github.GithubError as error:
                waiting, failed = f"no run read: {error}", True
            if waiting != last:
                print(f"drive job prove: waiting for {key}'s CI at {sha[:12]} to pass: {waiting}", file=sys.stderr, flush=True)
            last = waiting
            if failed:
                sleep(POLL_SECONDS)
            else:
                receiver.hear(repo, "ci", heard)
                if (rest := read_at + READ_EVERY_SECONDS - clock()) > 0:
                    sleep(rest)
    return waited


def ci_runs(repo: str, listed: str, query: str) -> list[dict]:
    """The newest hundred workflow runs a listing names, newest first, as one read: a run on the default branch tests every merge its commit holds, so the newest runs judge a merge as well as all of them, and the pages past the first cost a read each on every wake of a proof."""
    jq = ".workflow_runs[] | {id, workflow_id, name, head_sha, created_at, status, conclusion, html_url}"
    return [json.loads(line) for line in github.read(f"{repo}'s workflow runs", ["gh", "api", f"repos/{repo}/actions/{listed}?per_page=100&{query}", "--jq", jq]).splitlines()]


def ci_passed(run: dict) -> bool:
    """Whether a workflow run completed with a conclusion GitHub counts as passed, but skipped, since a run whose every job skipped tested nothing."""
    return run["status"] == "completed" and (run["conclusion"] or "").upper() in github.PASSED - {"SKIPPED"}


def ci_statuses(repo: str, commit: str) -> list[dict]:
    """The newest commit status of each context on commit, read through GraphQL, whose own pool every drive job prove pass then spends instead of the owner's REST core one."""
    owner, name = repo.split("/")
    query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ object(oid: "{commit}") {{ ... on Commit {{ status {{ contexts {{ context state targetUrl }} }} }} }} }} }}'
    jq = ".data.repository.object.status.contexts // [] | .[] | {context, state: (.state | ascii_downcase), target_url: .targetUrl}"
    return [json.loads(line) for line in github.read(f"{repo}'s commit statuses on {commit[:12]}", ["gh", "api", "graphql", "-f", f"query={query}", "--jq", jq]).splitlines()]


def pushed(repo: str, sha: str, branch: str) -> Iterator[str]:
    """The merge's commit at sha, then each commit GitHub's compare of it with branch lists after it, oldest first, that holds it: the commits its push and each push after it put on branch, the compare read only once one past the merge's is asked for."""
    yield sha
    later = github.read(f"the commits after {sha[:12]} on {repo}'s {branch}", ["gh", "api", f"repos/{repo}/compare/{sha}...{branch}", "--jq", ".commits[].sha"]).split()
    yield from (commit for commit in later if github.holds(repo, commit, sha))


def ci_judged(repo: str, sha: str, branch: str, triggered: list[dict]) -> list[tuple[str, bool]]:
    """A line naming each workflow in triggered, the push-event runs on the commit the CI of the merge's push reported on, beside whether it passed the merge: once its run there passed, or else any of its push-event runs on the branch from that one on, by run id, passed on a commit holding the merge, the line naming the first, since a run on the default branch tests every merge its commit holds, so a run before it that failed on another merge's change or was cancelled never decides it; else not yet, the line naming its newest run. Each workflow's runs are read on their own, since GitHub lists at most 1000 runs for a filtered read, which a busy default branch's runs of every workflow together pass within a day."""
    owns: dict[int, dict] = {}
    for run in sorted(triggered, key=lambda run: run["id"]):
        owns.setdefault(run["workflow_id"], run)
    judged = []
    for workflow, own in owns.items():
        listed = [own] if ci_passed(own) else ci_runs(repo, f"workflows/{workflow}/runs", f"branch={branch}&event=push&created=>={own['created_at']}")
        runs = sorted((run for run in listed if run["id"] >= own["id"]), key=lambda run: run["id"])
        passing = next((run for run in runs if ci_passed(run) and github.holds(repo, run["head_sha"], sha)), None)
        run = passing or runs[-1]
        judged.append((f"{run['name']} {run['html_url']} {run['status']} {run['conclusion'] or ''}".rstrip(), passing is not None))
    return judged


def statuses_judged(repo: str, statuses: list[dict], later: Iterator[str]) -> list[tuple[str, bool]]:
    """A line naming each commit status in statuses, those on the commit the CI of the merge's push reported on, beside whether it passed the merge: once its state there is success, or else once a status of its context on a commit later yields after it, each holding the merge, is, the line naming the first, since each push's build tests every merge its commit holds; else not yet, the line naming its newest. A later commit is read only while a context has not passed."""
    newest = {status["context"]: status for status in statuses}
    passing = {context for context, status in newest.items() if status["state"] == "success"}
    while len(passing) < len(newest) and (commit := next(later, None)):
        for status in ci_statuses(repo, commit):
            if status["context"] in newest.keys() - passing:
                newest[status["context"]] = status
                if status["state"] == "success":
                    passing.add(status["context"])
    return [(" ".join(filter(None, [context, status["target_url"], status["state"]])), context in passing) for context, status in newest.items()]


def heard_ci(repo: str, sha: str, mark: str | None) -> list[tuple[str, bool]]:
    """Share a merge's CI verdict across processes until its webhook mark changes or expires, polling once per READ_EVERY_SECONDS before the first mark. Reads of the same merge share one in-flight request; unrelated merges retain independent entries and locks."""
    folder = state.root()
    key = f"{repo}@{sha}"
    lock = folder / "ci-heard-locks" / hashlib.sha256(key.encode()).hexdigest()
    with state.flocked(lock), closing(state.connect(folder / CI_HEARD)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS verdicts (key TEXT PRIMARY KEY, mark TEXT, at REAL NOT NULL, verdict TEXT NOT NULL) WITHOUT ROWID")
        db.execute("CREATE INDEX IF NOT EXISTS verdicts_at ON verdicts (at)")
        was = db.execute("SELECT mark, at, verdict FROM verdicts WHERE key = ?", (key,)).fetchone()
        age = CI_HEARD_SECONDS if mark else READ_EVERY_SECONDS
        if was and was[0] == mark and time.time() - was[1] < age:
            return [tuple(line) for line in json.loads(was[2])]
        verdict = pushed_ci(repo, sha)
        now = time.time()
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?)", (key, mark, now, json.dumps(verdict)))
            db.execute("DELETE FROM verdicts WHERE at <= ?", (now - CI_HEARD_SECONDS,))
        return verdict


def pushed_ci(repo: str, sha: str) -> list[tuple[str, bool]]:
    """How the CI of the push that carried a merge at sha onto repo's default branch judges it, on the first commit from the merge's on, as pushed lists them, that any CI reported on: the merge's own when its push ended there, else that push's head, the one commit CI builds for a push. Its push-event workflow runs, as ci_judged judges them, and its commit statuses, such as Buildkite's buildkite/test, as statuses_judged judges them, each by a line beside whether it passed; a run of another event, such as a reviewer workflow's on issue_comment or check_run, which GitHub stamps with the branch's head whatever pushed it, is no CI, nor is a run that skipped. None before any CI reported."""
    branch = github.default_branch(repo)
    line = pushed(repo, sha, branch)
    for commit in line:
        runs = [run for run in ci_runs(repo, "runs", f"branch={branch}&event=push&head_sha={commit}") if run["conclusion"] != "skipped"]
        statuses = ci_statuses(repo, commit)
        if runs or statuses:
            return [*ci_judged(repo, sha, branch, runs), *statuses_judged(repo, statuses, line)]
    return []


def ci_verdict(repo: str, sha: str) -> list[tuple[str, bool]]:
    """pushed_ci's lines for a merge at sha on repo's default branch, or, while no CI reported on its commit or any commit after it, one saying so that has not passed."""
    return pushed_ci(repo, sha) or [(f"no CI reported on {sha[:12]} or any commit after it on {github.default_branch(repo)}", False)]


def red_default_runs(repo: str) -> list[dict]:
    """The newest completed run of each workflow on repo's default branch, among the branch's hundred newest and past those cancelled or skipped, that failed. A commit no workflow runs on, such as an autobump's [skip ci], keeps the verdict of the run before it."""
    branch = github.default_branch(repo)
    jq = ".workflow_runs[] | {id, workflow_id, name, conclusion, html_url}"
    runs = github.read(f"{repo}'s completed runs on {branch}", ["gh", "api", f"repos/{repo}/actions/runs?branch={branch}&status=completed&per_page=100", "--jq", jq])
    newest: dict[int, dict] = {}
    for run in map(json.loads, runs.splitlines()):
        if run["conclusion"] not in ("cancelled", "skipped"):
            newest.setdefault(run["workflow_id"], run)
    return [run for run in newest.values() if run["conclusion"].upper() not in github.PASSED]


def red_on_default(repo: str) -> list[str]:
    """Each workflow whose newest completed run on repo's default branch failed, by red_default_runs, by its name and link: what a main fix fixes forward."""
    return [f"{run['name']} {run['html_url']}" for run in red_default_runs(repo)]


def default_failures(repo: str) -> dict[str, dict]:
    """Each test the newest completed run of each workflow on repo's default branch failed, by red_default_runs, with that run's link and its workflow's id, as head_failures names a PR's: the tests a main fix claims by name, so one job alone fixes each."""
    return {test: {"run": run["html_url"], "workflow": run["workflow_id"]} for run in red_default_runs(repo) for test in run_failures(repo, run["id"])}


def head_failures(repo: str, head: str) -> dict[str, dict]:
    """Each test the newest pull request run of each workflow on a PR's head failed, with that run's link and its workflow's id."""
    jq = ".workflow_runs[] | {id, workflow_id, conclusion, html_url}"
    runs = [json.loads(line) for line in github.read(f"{repo}'s runs on {head[:12]}", ["gh", "api", f"repos/{repo}/actions/runs?head_sha={head}&event=pull_request&per_page=100", "--jq", jq]).splitlines()]
    newest = {run["workflow_id"]: run for run in sorted(runs, key=lambda run: run["id"])}
    return {test: {"run": run["html_url"], "workflow": run["workflow_id"]} for run in newest.values() if run["conclusion"] == "failure" for test in run_failures(repo, run["id"])}


def main_failures(repo: str, workflow: int, created: str) -> list[str] | None:
    """The tests failed by the newest completed run not cancelled of a workflow on repo's default branch among those created in a GitHub date range, such as <2026-11-13T13:05:55Z: none when it passed, and None when no such run finished or its failure names no test, as a timeout's does, so it says nothing of any test. The filter ends in tojson because gh's --jq prints a null result as an empty line, never as null."""
    jq = '[.workflow_runs[] | select(.conclusion != "cancelled") | {id, conclusion}] | first | tojson'
    query = f"branch={github.default_branch(repo)}&status=completed&created={created}&per_page=100"
    run = json.loads(github.read(f"{repo}'s runs of workflow {workflow} created {created}", ["gh", "api", f"repos/{repo}/actions/workflows/{workflow}/runs?{query}", "--jq", jq]))
    if run is None:
        return None
    if run["conclusion"].upper() in github.PASSED:
        return []
    return run_failures(repo, run["id"]) or None


@cache
def run_failures(repo: str, run: int) -> list[str]:
    """Each test a completed workflow run failed, as pytest's short test summary names it in the log of each of its jobs that failed; read once a process, since a finished run never changes."""
    jobs = github.read(f"{repo}'s run {run} jobs", ["gh", "api", f"repos/{repo}/actions/runs/{run}/jobs?filter=latest&per_page=100", "--jq", '.jobs[] | select(.conclusion == "failure") | .id']).split()
    return sorted({found["test"] for job in jobs for found in FAILED_TEST.finditer(github.read(f"{repo}'s job {job} log", ["gh", "api", f"repos/{repo}/actions/jobs/{job}/logs"]))})


def merged_at(repo: str, sha: str) -> str:
    """When a merge commit landed, as its committer date reads, which GitHub sets as it merges."""
    return github.read(f"{repo}'s commit {sha[:12]}", ["gh", "api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.committer.date"], max_age="1h").strip()


def merges_of(data: dict, job_id: str) -> dict[str, str]:
    return {key: data["prs"][key]["merged_sha"] for key in proved_prs(data, job_id)}


def finish(name: str, data: dict, job_id: str, line: str, check: list[str], post: str, facts: str) -> bool:
    """Take the line drive job prove sets on its builder, its report naming its PRs, if any, and CHECK:, a DONE proving a DONE of the job's own keeping that DONE whole under its line, such as a staging test's CASE lines, the PR it tested and its release build, so take_result delivers its staging test part by them, a DONE delivering by its proof each part of work he ordered the job owes, by state.deliver_proven, and on a DONE that leaves its open order nothing to do, an open incident's aside, which the watch closes once every merged fix PR of it runs on each production stack, keep on the order its proof and the close report the watch posts to the communicator, whose live line the watch posts itself on an order state.watch_posts_itself names, opening POST with the PRs' titles, a repair's order title, and post, but close done on that proof a drive tooling order a session opened, which posts him no line; held while he sees any of the order's results on a surface no CHECK: came from. Whether the order closes."""
    job, keys = data["jobs"][job_id], proved_prs(data, job_id)
    order_id, urls = job["order"], " ".join(map(state.pr_url, keys))
    own = next((reply["body"].strip() for reply in state.returns(name, job_id) if state.opening(reply["body"]) == job["said"]), "") if line.startswith("DONE") and done_unproven(data, job_id) else ""
    whole = f"{line}\n{f'{own}\n' if own else ''}{f'PR: {urls}\n' if urls else ''}CHECK: {' '.join(check)}\n"
    if line.startswith("DONE"):
        state.deliver_proven(data, job_id, line.removeprefix("DONE "))
    state.after(lambda: state.keep_report(name, job_id, line, whole) or [])
    state.take_result(data, job_id, line, whole)
    return line.startswith("DONE") and closed(name, data, job_id, order_id, keys, check, post, facts)


def closed(name: str, data: dict, job_id: str, order_id: str, keys: list[str], check: list[str], post: str, facts: str) -> bool:
    """Close an order a passing proof left nothing to do, every part delivered, every job on it finished or holding a passed unit for it, by unit_done, and no open incident on it, while he sees none of its results on a surface no CHECK: came from: a drive tooling order a session opened closes done on that proof, posting him no line; any other keeps its proof and the close report the watch posts to the communicator, opening POST with the PRs' titles, else the order's title, and post. Whether it closes."""
    from drive import brief

    order = data["orders"][order_id]
    closes = not brief.unseen(data, order, " ".join(check)) and order["state"] not in state.CLOSED_ORDER_STATES and all(state.delivered(data, order_id, part) for part in order["parts"])
    closes = closes and all(unit_done(data, other, order_id) for other in order["jobs"]) and not state.open_incidents(data, [order_id])
    if closes:
        close_proven(name, data, job_id, order_id, keys, " ".join(check), post, facts)
    return closes


def close_proven(name: str, data: dict, job_id: str, order_id: str, keys: list[str], proof: str, post: str, facts: str) -> None:
    """Close an order on its proof: a drive tooling order a session opened closes done, posting him no line; any other keeps its proof and the close report the watch posts to the communicator, opening POST with the PRs' titles, else the order's title, and post."""
    from drive import view

    order = data["orders"][order_id]
    if state.unasked_tooling(order):
        order.update(proof=proof, state="done", updated=state.stamp())
    else:
        title = "; ".join(data["prs"][key]["title"] for key in keys) or view.order_title(order)
        order["proof"] = proof
        order["close_report"] = {
            "id": f"{state.PROVED_REPORT}{job_id}" if order_id == data["jobs"][job_id]["order"] else f"{state.PROVED_REPORT}{job_id}-{order_id}",
            "kind": "report",
            "to": [f"communicator@{name}"],
            "topic": f"install-proved-{order_id}",
            "attr": {"close": order_id} | state.thread_attr(data, order),
            "text": f"POST {title} {post}",
            "body": f"Fixed: {', '.join(filter(None, [title, ' '.join(map(state.pr_url, keys))]))}\n{facts}",
        }


def installed_fixes(data: dict, repo: str) -> dict[str, list[str]]:
    """Each open drive tooling order whose fix is merged in repo, with no proof of its own to wait for, by his decision (b) "A drive-tooling order closes on a passing scripted proof, or on its fix being on main and installed": every job on it finished, at least one PR among them, each merged in repo, no standing part and no open incident on it, with those PRs."""
    fixes = {}
    for order_id, order in data["orders"].items():
        if not state.tooling(order) or order["state"] in state.CLOSED_ORDER_STATES or order["close_report"] or not order["jobs"]:
            continue
        if any(data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES for job_id in order["jobs"]) or any(part["standing"] for part in order["parts"].values()):
            continue
        keys = list(dict.fromkeys(key for job_id in order["jobs"] for key in map(state.pr_key, data["jobs"][job_id]["prs"]) if key in data["prs"] and data["prs"][key]["state"] != "closed"))
        if keys and all(key.startswith(f"{repo}#") and data["prs"][key]["merged_sha"] for key in keys) and not state.open_incidents(data, [order_id]):
            fixes[order_id] = keys
    return fixes


def close_installed(clone: str, repo: str, key: str, build: str, version: str, deadline: float) -> list[str]:
    """Close done on the install each open drive tooling order of every drive whose fix, by installed_fixes, the installed build holds, its proof the PRs merged and the version installed, no recording asked of it, fetching the default branch's commits once only when some order has such a fix; the orders of his words a drive closes so get one close report between them, live_report, so he reads one line an install, never one an order. A line for each order closed."""
    lines = [line for name in state.names() if unbatched(state.load(name)) for line in clear_unbatched(name)]
    if not (owed := {name: fixes for name in state.names() if (fixes := installed_fixes(state.load(name), repo))}):
        return lines
    git(clone, "fetch", "--quiet", "--filter=tree:0", "--no-tags", "origin", f"+HEAD:{DEFAULT_BRANCH}", deadline=deadline)
    contained: dict[str, bool] = {}

    def holds(sha: str) -> bool:
        if sha not in contained:
            contained[sha] = ran(["git", "-C", clone, "merge-base", "--is-ancestor", sha, build], deadline).returncode == 0
        return contained[sha]

    def installed_closes(name: str, data: dict) -> list[str]:
        his, closes = [], []
        for order_id, keys in installed_fixes(data, repo).items():
            if not all(holds(data["prs"][each]["merged_sha"]) for each in keys):
                continue
            proof = f"{' '.join(map(state.pr_url, keys))}{INSTALLED_PROOF}{key} {version} at {build[:12]}"
            data["orders"][order_id].update(proof=proof, state="done", updated=state.stamp())
            if not state.unasked_tooling(data["orders"][order_id]):
                his.append(order_id)
            closes.append(f"{name} {order_id}: closed, {proof}")
        if his:
            data["orders"][his[0]].update(state="open", close_report=live_report(name, his, key.partition("@")[0], version, build))
        return closes

    return lines + [line for name in owed for line in state.write(name, partial(installed_closes, name), patient=True)]


def unbatched(data: dict) -> list[str]:
    """The open orders holding an unsent close report of their own that an install before live_report kept on its close, one report an order."""
    return [
        order_id
        for order_id, order in state.open_orders(data).items()
        if (report := order["close_report"]) and "seq" not in report and INSTALLED_PROOF in (order["proof"] or "") and not report["id"].startswith(f"{state.PROVED_REPORT}install-")
    ]


def clear_unbatched(name: str) -> list[str]:
    """Close done each order unbatched reads, dropping its close report unsent, since the line of an install's fixes posts him once for them all. A line for each order closed."""

    def cleared_reports(data: dict) -> list[str]:
        cleared = unbatched(data)
        for order_id in cleared:
            data["orders"][order_id].update(close_report=None, state="done", updated=state.stamp())
        return cleared

    return [f"{name} {order_id}: closed, its close report dropped unsent for the one line of its install's fixes" for order_id in state.write(name, cleared_reports)]


def live_report(name: str, order_ids: list[str], plugin: str, version: str, build: str) -> dict:
    """The one close report of an install's closes of orders of his words, kept on the first, which stays open until the watch posts it and closes it: a POST line telling him how many of his drive fixes went live in the version, each order's title linked."""
    return {
        "id": f"{state.PROVED_REPORT}install-{version}",
        "kind": "report",
        "to": [f"communicator@{name}"],
        "topic": f"install-live-{version}",
        "attr": {"close": order_ids[0]},
        "text": f"POST Live in {plugin} {version}: {len(order_ids)} of your drive fixes: {', '.join(f'{{order:{order_id}}}' for order_id in order_ids)}",
        "body": f"Live in {plugin} {version} at {build[:12]}: {' '.join(order_ids)}",
    }


def unit_done(data: dict, job_id: str, order_id: str) -> bool:
    """Whether a job on an order holds it open no longer: it finished, or the proof unit drive job prove ran for that order on it passed, so a slow unit of a cut's prover never holds another order's close."""
    job = data["jobs"][job_id]
    return job["state"] in state.FINISHED_JOB_STATES or state.unit_passed(job, order_id)


def said_on_prs(name: str, data: dict, job_id: str, keys: list[str], change: str) -> None:
    order_id = data["jobs"][job_id]["order"]
    lane = state.heard_lane(data, order_id)
    events = [{"repo": merged_in, "number": int(number)} for merged_in, number in (key.split("#") for key in keys)] or [{"job": job_id, "order": order_id}]
    state.after(lambda: [state.append_event(name, "proof", **event, change=change, lane=lane) for event in events] and [])


def merged_done(name: str, data: dict, job_id: str) -> None:
    """Take DONE at its merge on a builder whose READY, PUSHED or WAITING line's PRs all merged outside yasyf/*, a product change, its merge commits its CHECK:, keeping no proof and no close report on its order, since a merge goes live only once a release holding it is out, which the builder add_prover hands it to proves, setting its own release wait once it reads release live; the release waits it held end with it."""
    job = data["jobs"][job_id]
    merges = merges_of(data, job_id)
    urls = " ".join(map(state.pr_url, merges))
    merged = ", ".join(f"{key} at {sha[:12]}" for key, sha in merges.items())
    commits = " ".join(merge_commits(merges))
    line = f"DONE {urls} merged: {merged}"
    whole = f"{line}\nPR: {urls}\nCHECK: {commits}\n"
    state.after(lambda: state.keep_report(name, job_id, line, whole) or [])
    state.take_result(data, job_id, line, whole)
    job["waits"] = []


def add_prover(name: str, data: dict, job_id: str, reach: dict[str, dict], parts: dict[str, list[str]], staged: set[str]) -> str:
    """Hand the PRs a builder merged outside yasyf/*, as released_prs reads them, all of its PRs or those beside a yasyf/* one its own proof proves, to the builder proving them live on the release holding each merge, so each merged PR has one: each PR's prover, by prover_of, takes its parts, each list under the order holding it, the first order its own, and one no agent or lane took yet also takes the PRs it lacks and a merge handoff, or one proving them by their tests passing on dev when every PR changes only tests or CI config, by cli.release_reach's answer in reach; while a PR has no prover and none of theirs can take it, a new one, marked as building nothing, so it holds no builder slot, goes on those parts in the merged job's session on its brief flags, its Done when drive job prove's, its handoff naming each path its PRs change on the release tool's dev list, which a checkout of dev runs. A PR with no staging test before its merge, by state.staging_tested, releasing a target in staged, those the release live read finds running on the staging provider's env, gets a new prover, its handoff owing its after-merge staging pass on those targets first. A prover waits on no release the drive sets: it starts at the merge and sets its own release wait once it reads release live. A new prover's Mac: flag names its drive job prove, which runs only on this Mac, so its launch starts it here, and the Mac: line of the merged job's newest return holding one, the step only this Mac runs that its PROVE: line needs, joins the Mac: flag of the prover a new or unstarted one gets, while the merged job's own next lane goes where its brief alone places it. That prover's id."""
    from drive import brief

    job = data["jobs"][job_id]
    mac = brief.returned_mac(name, job_id)
    merges = {key: data["prs"][key]["merged_sha"] for key in handed_on(data, job_id)}
    found = list(dict.fromkeys(filter(None, (prover_of(data, key) for key in merges))))
    keys = [key for key in merges if not prover_of(data, key)]
    merged = ", ".join(f"{key} at {sha[:12]}" for key, sha in merges.items())
    stagings = [
        f"staging:{target}/{providers.of(data).staging.env}@{merges[key]}"
        for key in keys
        if not state.staging_tested(data, key)
        for target in reach.get(key, {}).get("releases", [])
        if target in staged
    ]
    waiting = None if stagings else next((builder_id for builder_id in found if state.untaken(data["jobs"][builder_id])), None)
    cut = not stagings and (not waiting or state.CUT_WAIT in data["jobs"][waiting]["waits"]) and bool(keys) and all(reach.get(key, {}).get("releases") and not reach[key].get("tested") and not reach[key].get("dev") for key in keys)
    proving = (
        f"it goes live on the next production release cut, which this job waits on, and you prove it with every other merge that cut carries:"
        f" for each order your brief names, write the PROVE: lines of its merged jobs' returns, {job_id}'s in reports/{job_id}.md among them, as one proof file,"
        " keep it with drive job set <your job> --repro <file> --order <order> and run drive job prove <your job>, which runs each order's file on its own timeout,"
        " and each one's exit 0 delivers that order's parts and posts its close while the others still run"
        f"; {state.PROOF_ON_PRODUCTION}"
        if cut
        else f"install it by the INSTALL: line of {job_id}'s return, in reports/{job_id}.md, write its PROVE: line as a proof file,"
        " keep it with drive job set <your job> --repro <file> and run drive job prove <your job>, which runs that file, and only its exit 0 delivers the part"
    )
    handoff = (
        f"{merged} merged, changing only tests or CI config, which no release ships: prove it by its tests passing on dev at or after its merge commit"
        if keys and all(reach.get(key, {}).get("tested") for key in merges)
        else f"{merged} merged with no staging test before its merge: run its {state.AFTER_MERGE_PASS} first, by the merge row of agents/builder.md,"
        f" its cases from the bodies of {' '.join(map(state.pr_url, keys))}, the PROVE: line and REPRO: of {job_id}'s return in reports/{job_id}.md and the words of {job['order']},"
        f" only in the top coordinator's staging window: return `WAITING staging-slot:<your job>`, which holds until root's START hands you the window, never a soak commit standing in for it,"
        f" then wait on {' '.join(stagings)} while staging serves no build holding its merge; then {proving}"
        if stagings
        else f"{merged} merged: {proving}"
    ) + "".join(
        f"; {key} changes {answer['dev'][0]}{f' and {len(answer["dev"]) - 1} more' if answer['dev'][1:] else ''} on the release tool's dev list, which a checkout of dev holding {merges[key][:12]} runs"
        for key, answer in reach.items()
        if key in keys and answer["dev"]
    )
    lead = waiting or (open_cut(data) if cut else None)
    for builder_id in dict.fromkeys([*found, *filter(None, [lead])]):
        widen(data, builder_id, parts, *((keys, {"handoff": handoff, "mac": mac}) if builder_id == lead else ([], {})))
    if lead or not keys:
        return lead or found[0]
    (order_id, own), *carried = parts.items()
    builder_id = state.next_id(data["jobs"], "j")
    data["jobs"][builder_id] = state.new_job(order_id, "builder", f"prove {', '.join(keys)} live", own, state.stamp()) | {
        "waits": [state.CUT_WAIT] if cut else [],
        "cut": cut,
        "session": job.get("session"),
        "brief_flags": joined(
            (job.get("brief_flags") or {}) | {"handoff": handoff, "done_when": f"drive job prove {builder_id} runs your proof file and takes your DONE on its exit 0, {PROVER_WAIT}"},
            {"mac": "; ".join(filter(None, [f"drive job prove {builder_id}", mac]))},
        ),
        "builds_nothing": True,
    }
    data["orders"][order_id]["jobs"].append(builder_id)
    for carried_id, carried_parts in carried:
        state.carry(data, builder_id, carried_id, carried_parts)
    return builder_id


def handed_on(data: dict, job_id: str) -> list[str]:
    """The merged PRs a job hands its prover: those it merged outside yasyf/*, as released_prs reads them, and, on a prover whose own PR merged, which took the DONE the watch takes at that merge, each PR its goal proves that no other prover proves, so the one prover of its own PR proves them too and no second is added for them."""
    job = data["jobs"][job_id]
    own = [key for key in proves(data, job_id) if data["prs"][key]["merged_sha"] and not prover_of(data, key)] if state.done_at_merge(job) else []
    return list(dict.fromkeys([*released_prs(data, job_id), *own]))


def open_cut(data: dict) -> str | None:
    """The prover no agent or lane took that waits on the next production release cut, by state.CUT_WAIT, which every merge outside yasyf/* reaching a release joins, so one prover proves each cut."""
    return next((job_id for job_id, job in data["jobs"].items() if job["kind"] == "builder" and state.CUT_WAIT in job["waits"] and state.untaken(job) and proves(data, job_id)), None)


def widen(data: dict, builder_id: str, parts: dict[str, list[str]], keys: list[str], flags: dict[str, str | None]) -> None:
    """Give a prover more parts, each list under the order holding it, and, while no agent or lane took it, the PRs keys in its goal and each brief flag value it lacks, its handoff and Mac: step, so one prover proves each merged PR live; a prover at work or done takes the parts alone, its DONE delivering them."""
    builder = data["jobs"][builder_id]
    for order_id, held in parts.items():
        state.deliver(data, builder_id, order_id, held)
    if not state.untaken(builder):
        return
    if keys:
        builder["goal"] = f"prove {', '.join([*proves(data, builder_id), *keys])} live"
    held = builder.get("brief_flags") or {}
    if (flagged := joined(held, flags)) != held:
        builder["brief_flags"] = flagged


def joined(held: dict, flags: dict[str, str | None]) -> dict:
    """Brief flags held, each value given that a held one does not already carry joined after it."""
    return held | {flag: "; ".join(filter(None, [held.get(flag), value])) for flag, value in flags.items() if value and value not in (held.get(flag) or "")}


def prover_of(data: dict, key: str) -> str | None:
    """The one builder proving the merged PR at key live, on whatever order holds it, marked as building nothing or not, of the builders prover_prs reads as proving it: the earliest done, then the earliest an agent or lane took, then the earliest none took yet; else the earliest such prover that failed or stopped, so its part reads ORPHAN and the watch adds no second; None while no builder proves it, so the watch adds one."""
    number = key.rpartition("#")[2]
    found = [
        (rank, position, job_id)
        for position, (job_id, job) in enumerate(data["jobs"].items())
        if job["kind"] == "builder" and (number in job["goal"] or number in (job["said"] or "")) and (rank := proof_rank(data, job_id, key)) is not None
    ]
    return min(found)[2] if found else None


def proof_rank(data: dict, job_id: str, key: str) -> int | None:
    job = data["jobs"][job_id]
    if key not in prover_prs(data, job_id):
        return None
    if job["state"] == "done":
        return 0
    if job["state"] in state.ENDED_JOB_STATES:
        return 3
    return 2 if state.untaken(job) else 1


def prover_prs(data: dict, job_id: str) -> list[str]:
    """The PRs a builder proves live: those its goal reads prove <PRs> live names, by proves, and on a done one also those its own proof proves, as proved_prs reads them, never one a brief or DONE only cites, as a staging tester's or a release follower's does; none for the DONE the watch takes at a merge, which proves nothing live."""
    job = data["jobs"][job_id]
    if state.done_at_merge(job):
        return []
    if job["state"] != "done":
        return proves(data, job_id)
    return list(dict.fromkeys([*proves(data, job_id), *proved_prs(data, job_id)]))


def proves(data: dict, job_id: str) -> list[str]:
    """The PRs a builder whose goal reads prove <PRs> live, as add_prover writes it, proves live."""
    job = data["jobs"][job_id]
    goal = state.PROVER_GOAL.fullmatch(job["goal"]) if job["kind"] == "builder" else None
    return state.named_prs(data, job["order"], goal["keys"]) if goal else []


def merged_said(name: str, builder_id: str, change: str) -> str:
    """The one line saying the builder add_prover added or widened, by change, to prove live the PRs of builders that took DONE at their merge, whatever their number, counting the releases it waits on, which drive status lists, briefed here so its agent starts on the watch's FREE once the last is out, or in the turn this line wakes when it waits on none, as a prover owing its after-merge staging pass does, or why its brief was not written; one an agent or lane already took, or that finished, keeps its brief and says its state."""
    from drive import cli, render

    data = state.load(name)
    builder = data["jobs"][builder_id]
    head = f"JOB {builder_id} {builder['order']} {change}, proves {', '.join(prover_prs(data, builder_id))}"
    if not state.untaken(builder):
        return f"{head}, already {builder['state']}: drive status {builder_id}"
    try:
        started = cli.write_brief(name, builder_id, {})[0].splitlines()[0]
    except cli.VERB_ERRORS as error:
        return f"{head}, has no brief: {error}; brief it with drive job brief {builder_id}"
    if state.CUT_WAIT in builder["waits"]:
        return f"{head}, waits on the next production release cut, which the watch binds it to once a cut carries its merges: drive status {builder_id}"
    if waits := len(builder["waits"]):
        return f"{head}, waits on {waits} release{'s' if waits > 1 else ''}: drive status {builder_id}"
    owes = f"owes its {state.AFTER_MERGE_PASS} first" if state.AFTER_MERGE_PASS in (builder.get("brief_flags") or {}).get("handoff", "") else "waits on no release"
    return f"{head}, {owes}: {render.start_now(started)}"


def proofless(name: str, data: dict, job_id: str) -> None:
    """Take BLOCKED on a builder whose READY or PUSHED's PRs all merged, a yasyf/* one among them, keeping no executable proof file, its merge commits its CHECK:, since nothing proves it live until drive job set keeps one and drive job prove runs it on the job."""
    line = (
        f"BLOCKED {' '.join(map(state.pr_url, reported_prs(data, job_id)))} merged, and {job_id} keeps no proof file to run, so nothing proves it live:"
        f" keep one with drive job set {job_id} --repro <file>, written from its PROVE: line, then drive job prove {job_id} proves it on {job_id}"
    )
    finish(name, data, job_id, line, merge_commits(merges_of(data, job_id)), "", "")


def merge_commits(merges: dict[str, str]) -> list[str]:
    return [f"https://github.com/{key.partition('#')[0]}/commit/{sha}" for key, sha in merges.items()]


RESERVATION = ("token", "members", "starter", "child", "state")


def proof_mark(job: dict) -> dict:
    """The job's proof as this process's run marks it from its start, no earlier run's result or wait on it: the reservation it adopted, by drive job prove --reserved, else one naming it as the child that adopted it, so state.holder reads it live while this process runs and no watch starts a second proof beside it."""
    held = job["proof"] if state.holder(job.get("proof")) == os.getpid() else {"child": state.born(os.getpid()), "state": "adopted"}
    return {field: held[field] for field in RESERVATION if field in held} | {"at": state.stamp(), "version": state.VERSION}


def prove(name: str, job_id: str, timeout: int, again: bool = False) -> str:
    """Prove live a builder's READY or PUSHED whose PRs all merged, the step the watch starts, on a builder that took BLOCKED, its own proof's among them, the newest READY or PUSHED its history keeps, which take_back puts it back on, so a failed proof runs again on the job that owns it: wait, with no deadline of its own, while the install train holds any of its yasyf/* merges, boarded or carried by an install it started that still runs, as train.await_departure reads the board, so no proof installs a plugin ahead of the train, then install each plugin a yasyf/* merge changes whose installs, binaries, Homebrew copy or Captain Hook.app lack it, its merges outside yasyf/* left to the prover add_prover hands them to, which proves them on their release, wait until the drive's dashboard runs the newest long-running installed, then run the job's proof file with the bin folders of those plugins' newest installs, read right before it runs, first on PATH, as newest_builds builds it, and the drive named, as proof_run does, with no wait for CI, since no install reads a CI run; when it fails while the CI of the push that carried a yasyf/* merge onto its default branch has not passed it, as drive pr ci judges the merge, wait for it to pass with no deadline, as await_ci does, and run the file once more, as it does once ccx's GitHub rate-limit floor lifts for a run its timeout cut off while it waited that floor out, and run it again on the newer installs when it fails while a newer install of any of them landed, each run of the file given the whole timeout from its own start, as proof_run gives it, so neither the installs, the dashboard's handover, a wait on CI nor an earlier run shortens it, keep the last run's output in reports/<j>.proof.txt, take DONE on exit 0 with output, since a proof file printing nothing proves nothing, each Slack post or picture link it names opening, as brief.unopened reads it, so a stand-in permalink proves nothing either, that output proving each user story the job's brief names on his surface, as brief.story_gap reads its lines in code, each gap it finds said on a line closing the output kept, and, once all that holds, each command its brief's After install: line names exiting 0, which after_steps runs, and BLOCKED otherwise, its line naming that file and quoting the run's last line opening FAIL, else saying it printed none, and a nonzero exit ending on a WAIT line with no FAIL line, or an install held for his card, which meet_helper's WAIT says, taking no result, any other install that fails, a merge no release will carry, or a dashboard still on an older install at the deadline taking BLOCKED naming it, but only on a run the watch started, as the proof reservation this process adopted, by drive job prove --reserved, or the proof mark it wrote naming it shows, since the proof's installs can restart the watch before the run ends, a session's failing run, the job's own agent's included, keeping its output and saying it on its PRs' lines while putting back the state, said line and proof mark it found, so its lane keeps its job and its Mac box, and keep the close report on its order for the watch to post when DONE leaves the order nothing to do, but for a drive tooling order a session opened, which closes done on its proof with no line to him, and an open incident, which the watch closes once DONE records its fix live, each said on its merged PRs' lines, an open PR a DONE of a job's own names being live nowhere; on a prover, a builder that opened no PR whose goal reads prove <PRs> live, naming merged PRs, and that waits on nothing but its own after-merge staging waits, as handed_prs reads it, or a repair, one whose said line names none and whose goal proves none, at work or waiting, or stopped or failed as take_back reopens it, its proof marked as this process's from its start, install only what a prover's yasyf/* merges change, run the file on the drive's own long-running when nothing is installed, take its DONE or BLOCKED as finish does, naming the job for its PRs and closing its order under the order's title, and on a WAIT keep the job waiting on a WAITING line quoting it, its proof marked waiting on it, said on the job's own line; refused for a job waiting on no such READY or PUSHED that is no such repair, or one proof_gap names, the job left as it was; nothing after another proof took the job's result first."""
    from drive import brief, dashboard

    def prove_write(data: dict):
        job = data["jobs"][job_id]
        found = {field: job.get(field) for field in ("state", "said", "updated", "proof")}
        take_back(name, data, job_id)
        if not provable(data, job_id):
            raise state.DriveError(
                f"{job_id} waits on no READY or PUSHED whose PRs all merged and owes no proof of a DONE of its own, so there is nothing to prove:"
                " it is no repair, a builder that opened no PR and names none, nor a prover, one that opened no PR, waits on nothing but its own after-merge staging waits and whose goal reads prove <PRs> live, naming PRs that all merged"
            )
        if why := proof_gap(data, job_id):
            raise state.DriveError(why)
        if not again and job.get("proof_failed") == proof_print(data, job_id):
            raise state.DriveError(
                f"{job_id}'s last proof failed on this same long-running, these same merges and this same proof file, so running it again proves nothing new:"
                f" fix what its FAIL line in {state.proof_path(name, job_id)} names, keep the fixed file with drive job set {job_id} --repro <file>, or wait for a newer install, then prove it again, or run drive job prove {job_id} --again once a fix outside them removed its cause"
            )
        own = done_unproven(data, job_id)
        if own_proof(data, job_id) or own:
            job["proof"] = proof_mark(job) | {"at": state.stamp(), "said": job["said"], "version": state.VERSION}
        merges = {} if own else merges_of(data, job_id)
        urls = " ".join(map(state.pr_url, proved_prs(data, job_id) if own else merges)) or (job_id if own else f"{job_id}'s repair")
        if job["state"] != found["state"]:
            job.update(found)
        return found, job, merges, urls

    found, job, merges, urls = state.write(name, prove_write)
    put_back = None if state.holder(found["proof"]) == os.getpid() else {"proof": found["proof"]}
    untapped ={key: sha for key, sha in merges.items() if state.merges_untapped(key)}
    train.await_departure(untapped)
    try:
        plugins = merged_plugins(untapped, clock() + timeout, timeout, name) if merges else []
    except (InstallError, NoRelease) as error:
        code, output, versions = "unrun", str(error), "no install holding it"
        line = f"BLOCKED {urls}: not installed, so {proof_files(job)} did not run: {str(error)[:SAID_CHARS]}"
    else:
        versions = newest_builds(plugins)[0]
        if repros := job.get("repros"):
            return prove_units(name, job_id, repros, plugins, timeout, put_back)
        try:
            followed = False
            while True:
                dashboard.await_newest(name, clock() + timeout)
                versions, path = newest_builds(plugins)
                try:
                    done = proof_run(name, job["repro"], path, timeout)
                except subprocess.TimeoutExpired as expired:
                    if (wait := github.floor_lifts_in(expired.stderr or "")) is None:
                        raise
                    print(f"drive job prove: {job['repro']} reached its deadline waiting for ccx's GitHub rate-limit floor to lift: running it again in {round(wait)} s, once it has", file=sys.stderr, flush=True)
                    sleep(wait)
                    continue
                code, output = done.returncode, done.stdout + done.stderr
                if not code:
                    break
                if not followed:
                    followed = True
                    if await_ci(untapped):
                        print(f"drive job prove: {job['repro']} exits {code} on {versions} while its merge's CI had not passed: running it again now that a run holding the merge passed", file=sys.stderr, flush=True)
                        continue
                if newest_builds(plugins)[0] == versions:
                    break
                print(f"drive job prove: {job['repro']} exits {code} on {versions}, and a newer install landed while it ran: running it again on that", file=sys.stderr, flush=True)
        except state.DriveError as error:
            code, output = "unrun", str(error)
        except subprocess.TimeoutExpired as expired:
            code, output = "past drive job prove's deadline", expired.stdout + expired.stderr
        except OSError as error:
            code, output = f"unrun ({error})", ""
        tail = output if code == "unrun" else quoted(output)
        if code == 0 and not output.strip():
            code, tail = "0 but prints nothing", "a proof file prints a line for each check it passed, so one printing nothing proves nothing"
        elif code == 0 and (shut := brief.dead([found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)], state.load(name))):
            code, tail = "0 but names a link that does not open", f"{shut[0]}: a proof file prints the live post or picture it read back, never a stand-in"
        elif code == 0 and (surfaced := brief.surface(job)) and (gap := brief.story_gap(job, surfaced, output, state.load(name))):
            code, tail = "0 but falls short of its surface", gap[0]
            output += "".join(f"{line}\n" for line in gap[1])
        elif code == 0 and (steps := (job.get("brief_flags") or {}).get("after")):
            ran, stopped = after_steps(name, steps, path, timeout)
            output += ran
            code, tail = stopped or (code, tail)
        versions = versions if untapped else shipped(job) if merges else f"{LONG_RUNNING} {state.VERSION}"
        line = (
            f"DONE {urls} live: {job['repro']} exits 0 on {versions}"
            if code == 0
            else f"BLOCKED {urls}: {job['repro']} exits {code} on {versions}, its output {state.proof_path(name, job_id)}: {tail[:SAID_CHARS]}"
        )
    return proof_kept(name, job_id, line, output, versions, code, put_back)


def proof_print(data: dict, job_id: str) -> str:
    """What a proof ran on, which a rerun of a failed one must change to prove anything new: the long-running it ran on, the merges it proves, by merges_of, and the bytes of each proof file the job keeps, its own and each order's."""
    job = data["jobs"][job_id]
    files = [job.get("repro"), *(job.get("repros") or {}).values()]
    held = [state.VERSION, *(f"{key}@{sha}" for key, sha in sorted(merges_of(data, job_id).items()))]
    return hashlib.sha256("\n".join([*held, *(hashlib.sha256(Path(file).read_bytes()).hexdigest() if Path(file).is_file() else "" for file in files if file)]).encode()).hexdigest()


def prove_units(name: str, job_id: str, repros: dict[str, str], plugins: list[str], timeout: int, found: dict | None) -> str:
    """Prove a release cut's prover by one proof unit per order it holds, each order's file drive job set --repro <file> --order <order> kept, every unit not yet passed run at once on the installed builds, each given the whole timeout from its own start, and each kept the moment it ends, by unit_kept, so a passing unit delivers its order's parts and posts its close while a slow one still runs; once all ended, the job takes DONE when every unit passed, else the BLOCKED of the first unit that failed, else the WAIT of one that waits on his next word, as proof_kept takes a whole proof's."""
    from drive import dashboard

    try:
        dashboard.await_newest(name, clock() + timeout)
    except state.DriveError as error:
        return proof_kept(name, job_id, f"BLOCKED {job_id}: its units did not run: {str(error)[:SAID_CHARS]}", str(error), newest_builds(plugins)[0], "unrun", found)
    versions, path = newest_builds(plugins)
    versions = versions if plugins else shipped(state.load(name)["jobs"][job_id])
    units = state.load(name)["jobs"][job_id].get("units") or {}
    due = {order_id: repro for order_id, repro in repros.items() if "result" not in units.get(order_id, {})}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(due), 1)) as pool:
        runs = {pool.submit(unit_run, name, repro, path, timeout): order_id for order_id, repro in due.items()}
        for ran in concurrent.futures.as_completed(runs):
            unit_kept(name, job_id, runs[ran], repros[runs[ran]], *ran.result(), versions)
    units = state.load(name)["jobs"][job_id].get("units") or {}
    outputs = {order_id: state.proof_path(name, job_id, order_id).read_text() if state.proof_path(name, job_id, order_id).is_file() else "" for order_id in repros}
    failed = [order_id for order_id in repros if "failed" in units.get(order_id, {})]
    waiting = [order_id for order_id in repros if "waits" in units.get(order_id, {})]
    urls = " ".join(map(state.pr_url, proved_prs(state.load(name), job_id))) or job_id
    if not failed and not waiting:
        return proof_kept(name, job_id, f"DONE {urls} live: each of {len(repros)} orders' proof files exits 0 on {versions}", "".join(outputs.values()), versions, 0, found)
    first = (failed or waiting)[0]
    said = units[first].get("failed") or units[first]["waits"]
    line = f"BLOCKED {urls}: {first}'s proof file {repros[first]} {said[:SAID_CHARS]}, its output {state.proof_path(name, job_id, first)}"
    return proof_kept(name, job_id, line, "".join(outputs[order_id] for order_id in repros if order_id != first) + outputs[first], versions, 1, found)


def unit_run(name: str, repro: str, path: str, timeout: int) -> tuple[int | str, str]:
    """Run one order's proof file as proof_run does: its exit and output, a run past its deadline, one that could not start or one exiting 0 with no output read as a failure."""
    try:
        done = proof_run(name, repro, path, timeout)
    except subprocess.TimeoutExpired as expired:
        return "past drive job prove's deadline", (expired.stdout or "") + (expired.stderr or "")
    except OSError as error:
        return f"unrun ({error})", ""
    output = done.stdout + done.stderr
    return ("0 but prints nothing" if not output.strip() else 0) if done.returncode == 0 else done.returncode, output


def unit_kept(name: str, job_id: str, order_id: str, repro: str, code: int | str, output: str, versions: str) -> None:
    """Keep what one order's proof unit came to on the job, its output in reports/<j>.<order>.proof.txt, said on the lines of that order's merged PRs among the job's, by proved_prs: a pass delivers each part of that order the job owes, by state.owed_work, and closes the order once it leaves nothing to do, by closed, while the job's other units still run; a WAIT ending a nonzero exit with no FAIL line marks the unit waiting on it; any other end marks it failed on its last FAIL line."""
    from drive import brief

    kept = state.proof_path(name, job_id, order_id)
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(output)

    def kept_unit(data: dict) -> None:
        job = data["jobs"][job_id]
        keys = [key for key in proved_prs(data, job_id) if data["prs"][key].get("order") == order_id]
        units = job.setdefault("units", {})
        if code == 0:
            line = f"DONE {' '.join(map(state.pr_url, keys)) or order_id} live: {repro} exits 0 on {versions}"
            for owed_order, part in state.owed_work(data, job_id):
                if owed_order == order_id:
                    data["orders"][order_id]["parts"][part].update(posted=line.removeprefix("DONE "), posted_by=job_id)
            units[order_id] = {"result": line, "at": state.stamp()}
            seen = [found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)]
            proven = f"{PROVEN} on {versions}: {repro} exits 0, its output {kept}"
            change = proven + (f"; {order_id} closes on it" if closed(name, data, job_id, order_id, keys, [str(kept), *seen], f"is live in {versions}", f"Live: {versions}\nProof: {proven}\n") else "")
        elif wait := waited(code, output):
            units[order_id] = {"waits": wait, "at": state.stamp()}
            change = f"{PROOF_WAITS}: {order_id}'s {repro}: {wait}"
        else:
            units[order_id] = {"failed": f"exits {code} on {versions}: {quoted(output)}", "at": state.stamp()}
            change = f"{PROOF_FAILED}: {order_id}'s {repro} exits {code} on {versions}, its output {kept}: {quoted(output)[:SAID_CHARS]}"
        said_on_prs(name, data, job_id, keys, change)

    state.write(name, kept_unit, patient=True)


def after_steps(name: str, steps: list[str], path: str, timeout: int) -> tuple[str, tuple[str, str] | None]:
    """Run each command a brief's After install: line names, in order, on the installed build a proof file just passed on, with that proof's PATH and the drive named, as proof_run gives them, each given the whole timeout: what each printed, under the command, and, for the first that exits nonzero or runs past the timeout, which stops the rest, what the proof's BLOCKED line says, its exit and the last line it printed; None once each exits 0."""
    printed = ""
    for step in steps:
        try:
            ran = subprocess.run(shlex.split(step), capture_output=True, text=True, env=os.environ | {"PATH": path, state.DRIVE_ENV: name}, timeout=timeout)
        except subprocess.TimeoutExpired:
            return printed + f"$ {step}\n", (f"0, then its After install: {step} runs past {timeout} s", "it was stopped there")
        said = ran.stdout + ran.stderr
        printed += f"$ {step}\n{said}"
        if ran.returncode:
            lines = [line.strip() for line in said.splitlines() if line.strip()]
            return printed, (f"0, then its After install: {step} exits {ran.returncode}", lines[-1] if lines else "it printed nothing")
    return printed, None


def shipped(job: dict) -> str:
    """The releases a prover's shipped list holds, each carrying one of its merges outside yasyf/*, which its proof ran on, else its merge commits."""
    releases = [f"the {found['service']} release of {found['pr']}" for found in map(state.RELEASE_WAIT.fullmatch, job["shipped"])]
    return ", ".join(releases) or "its merge commits"


def quoted(output: str) -> str:
    """The line a proof's run is said by: its last line opening FAIL, the check that failed, since a proof stopped by a failing step can print lines after it that read as passing; else its last line when it opens WAIT; else that it printed no FAIL line, never a line of its own, which can be a shell's report of a killed step quoting its whole command line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if fail := next((line for line in reversed(lines) if line.startswith("FAIL")), None):
        return fail
    if lines and lines[-1].startswith("WAIT"):
        return lines[-1]
    return "it printed no FAIL line" if lines else "no output"


def waited(code: int | str, output: str) -> str | None:
    """The WAIT line a proof file's nonzero exit ended on with no FAIL line before it, or an install held for his card opens, saying only his next word can let it pass, or None for a proof that failed."""
    quote = quoted(output)
    return quote if (code == "unrun" or isinstance(code, int) and code) and quote.startswith("WAIT") else None


def proof_run(name: str, repro: str, path: str, timeout: int) -> subprocess.CompletedProcess:
    """Run the proof file as subprocess.run would, in the folder the drive's coordinator runs in, the drive's checkout, read from its saved sessions by state.saved, never the folder drive job prove was started in, such as the drive's state folder, which no git repository holds, so a proof calling git, ccn or a repository tool by relative path runs as its builder ran it, its output read as text, PATH set to `path`, the one newest_builds builds, DRIVE_ENV naming the drive it proves, so each drive call in it that names no drive acts on that one whatever session or drives this Mac holds, and PROOF_DEADLINE_ENV the Unix second its deadline, `timeout` seconds from its start, falls at, so a proof that waits prints its WAIT line before it, but as a process group of its own, which reads.end ends at that deadline with SIGINT, the signal that unwinds a Python proof, one run with -I included, so it writes what it printed before it exits, and SIGKILL reads.STOP_GRACE seconds on; the TimeoutExpired raised then holds everything the group wrote."""
    from drive import reads

    with subprocess.Popen(
        [repro],
        cwd=state.saved(name)["roles"]["coordinator"]["cwd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ | {"PATH": path, state.DRIVE_ENV: name, PROOF_DEADLINE_ENV: str(int(time.time() + timeout))},
        start_new_session=True,
    ) as running:
        try:
            out, err = running.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            raise subprocess.TimeoutExpired(expired.cmd, expired.timeout, *reads.end(running, time.monotonic() + reads.STOP_GRACE, signal.SIGINT)) from None
    return subprocess.CompletedProcess([repro], running.returncode, out, err)


def proof_files(job: dict) -> str:
    """The proof files a prover runs: its own, kept by drive job set --repro, else each its orders keep, by --repro --order."""
    return job.get("repro") or ", ".join((job.get("repros") or {}).values())


def proof_kept(name: str, job_id: str, line: str, output: str, versions: str, code: int | str, found: dict | None) -> str:
    """Keep what a proof came to on its job, its output in reports/<j>.proof.txt, and say it on the job's merged PRs' lines, by proved_prs, a repair's or one naming no merged PR on its own, nothing after another proof took the job's result first; a run waited on his next word, a WAIT line ending a nonzero exit with no FAIL line, takes no result and is said on those lines only when its WAIT line differs from the one the output it overwrites ended on, so a rerun after his word or an install that waits on the same word says nothing again, so the job stays waiting on its READY or PUSHED, a repair taking the WAITING that quotes it, and marks its proof waiting, which the watch runs again once he is heard after the run started; a failed run the watch did not start, as found holds the fields prove changed before it ran, a session's such as the job's own agent's, takes no result either, the job put back on the state, said line and proof mark it found, so a save never reads it failed and takes down the Mac box its lane still works on."""
    from drive import brief

    kept = state.proof_path(name, job_id)
    kept.parent.mkdir(exist_ok=True)
    before = quoted(kept.read_text()) if kept.exists() else None
    kept.write_text(output)

    def proving(data: dict):
        job = data["jobs"][job_id]
        then = {field: job.get(field) for field in ("state", "said", "updated")}
        take_back(name, data, job_id)
        if not provable(data, job_id):
            return f"{job_id}: another proof took its result first", None
        keys = proved_prs(data, job_id)
        proven = f"{PROVEN} on {versions}: {proof_files(job)} exits 0, its output {kept}"
        repeated = False
        if wait := waited(code, output):
            repeated = wait == before
            if own_proof(data, job_id) and job["said"] != (said := f"WAITING {PROOF_WAITS}: {wait}"):
                state.after(lambda: state.keep_report(name, job_id, said, f"{said}\nCHECK: {kept}\n") or [])
                state.take_result(data, job_id, said)
            job["proof"] = (job.get("proof") or proof_mark(job)) | {"said": job["said"], "waits": wait}
            change = f"{PROOF_WAITS}: {line.removeprefix('BLOCKED ')}"
        elif code and found is not None:
            job.update(then | found, proof_failed=proof_print(data, job_id))
            change = f"{PROOF_FAILED}: {line.removeprefix('BLOCKED ')}; {job_id} stays {job['state']} on its line, since only a proof the watch starts takes its failure as the job's result"
        else:
            job["proof"] = (job.get("proof") or proof_mark(job)) | {"said": job["said"], "result": line}
            seen = [found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)]
            post, facts = (f"is live in {versions}", f"Live: {versions}\n") if keys else ("is fixed", "")
            closes = finish(name, data, job_id, line, [str(kept), *seen], post, f"{facts}Proof: {proven}\n")
            change = proven
            job["proof_failed"] = proof_print(data, job_id) if code else None
            if code:
                change = f"{PROOF_FAILED}: {line.removeprefix('BLOCKED ')}"
            elif closes and state.unasked_tooling(data["orders"][job["order"]]):
                change = f"{proven}; {job['order']} closed done on it with no close report, since a session opened that drive tooling order, so no line reaches him and the dashboard shows it live"
            elif closes:
                change = f"{proven}; its close report of {job['order']} waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
        return None, (data, keys, change, repeated)

    refused, proved = state.write(name, proving, patient=True)
    if refused:
        return refused
    data, keys, change, repeated = proved
    if not repeated:
        said_on_prs(name, data, job_id, keys, change)
    return f"{job_id}: {change}"


def reload_session(drive: str, seat: str, terminals: list[dict], force: bool) -> str:
    """Hand one drive session's reload to the one process typing into the first Orca terminal it runs in, of the terminals given, `drive reload --seat` detached from this verb, so no install, lander or verb waits on its turn: unless it already reloaded onto the newest plugin install, as reloads.json keeps it, and not forced, as a bare drive reload is; a terminal whose reload lock another process holds is left to it, since that process reloads it again once its reload shows when a newer install landed meanwhile. A seat is a role, or `sub-coordinator <project>`."""
    from drive import orca

    who = f"{drive} {seat}"
    if not terminals:
        return f"{who}: in no Orca terminal, skipped"
    terminal = terminals[0]["handle"]
    onto, plugin = newest_update()
    if not force and reloaded_onto(drive, seat) >= onto:
        return f"{who}: already reloaded onto {plugin}"
    if not force and (at := reload_held(drive, seat)):
        hold_reload(drive, seat)
        return f"{who}: reloaded at {at}, so it loads {plugin} on the drive's watch pass an hour after that; drive reload loads it now"
    path = reload_lock(terminal)
    with open(path, "a") as handle:
        if not state.taken(handle, 0):
            return f"{who}: pending, as the process holding its reload lock ({state.holders(path)}) types {orca.RELOAD} into {terminal} once its turn ends"
    pid = state.detach(drive, ["reload", "--seat", seat, "--terminal", terminal, *(["--force"] if force else [])], RELOADS_LOG)
    return f"{who}: pid {pid} types {orca.RELOAD} into {terminal} once its turn ends, said in {state.drive_dir(drive) / RELOADS_LOG}"


def reload_seat(drive: str, seat: str, force: bool, terminal: str | None) -> None:
    """`drive reload --seat`: reload the seat's session onto the newest plugin install as the one process typing into its terminal, then again for each install that landed before that reload showed, printing a line for each, unforced only once its hour since the last reload is up, by reload_held, marking the install held for the watch until then; nothing once another process holds the terminal's reload lock, which does the same, or once a reload fails, which the next install tries again. What is left to load is read again each time the lock is let go, so an install that found it held while this process found nothing left is still reloaded."""
    failures = 0
    while force or reloaded_onto(drive, seat) < newest_update()[0]:
        if not force and reload_held(drive, seat):
            hold_reload(drive, seat)
            return
        if not (said := held_reload(drive, seat, force, terminal)):
            return
        line, reloaded = said
        print(f"{state.stamp()} {drive} {seat}: {line}", flush=True)
        if not reloaded and not line.startswith(RETRIED):
            return
        if not reloaded:
            failures += 1
            if failures == RELOAD_DEFECT_AFTER:
                reload_defect(drive, seat, failures, line)
            if failures == RELOAD_TRIES:
                return
            sleep(RELOAD_RETRY_SECONDS)
            continue
        force = False


def reload_defect(drive: str, seat: str, failures: int, line: str) -> None:
    """Tell the meta session, as a defect, that the seat's reload failed failures times in a row and the seat still runs older code, as Orca's runtime_timeout left a seat communicator on old code for 15 minutes."""
    from drive import tools

    tools.sent(drive, LANE_RELOAD, [{"kind": "defect", "to": [f"meta@{drive}"], "text": f"{seat}'s reload failed {failures} times in a row, so it still runs older code, and the reload retries every {RELOAD_RETRY_SECONDS}s up to {RELOAD_TRIES} tries: {line}"}])


def held_reload(drive: str, seat: str, force: bool, given: str | None) -> tuple[str, bool] | None:
    """Under the reload lock of the first Orca terminal the seat's session runs in, as one read of Orca and the process tree finds it, reload its session onto the newest plugin install, unless it already reloaded onto it and not forced; what it did and whether the session is reloaded, or None when another process holds the lock."""
    from drive import orca

    held = state.bound_sessions(state.saved(drive))[seat]
    terminal = given
    if not terminal:
        try:
            terminals = orca.session_terminals([held["session"]])[held["session"]]
        except orca.OrcaError as error:
            return f"not reloaded, since Orca could not show the terminals it runs in ({error})", False
        if not terminals:
            return "in no Orca terminal, so nothing was typed", False
        terminal = terminals[0]["handle"]
    path = reload_lock(terminal)
    with open(path, "a") as handle:
        if not state.taken(handle, 0):
            return None
        onto, plugin = newest_update()
        if not force and reloaded_onto(drive, seat) >= onto:
            return f"already reloaded onto {plugin}", True
        return typed_reload(drive, seat, held, terminal, onto, plugin)


def typed_reload(drive: str, seat: str, held: dict, terminal: str, onto: str, plugin: str) -> tuple[str, bool]:
    """Type /reload-plugins once into the terminal the seat's session runs in once Claude Code reads its turn over, never into a busy prompt, which loads the install's skills, agents and new hooks with nothing restarted, then wait for its transcript to show it ran; a session homed ends there types /exit instead, which cci resumes in the drive's checkout on the newest install. Stamped in reloads.json as reloaded onto that install once it shows; what it did and whether the session reloaded."""
    from drive import orca, watch

    moved = homed(drive, seat, held)
    typed = orca.EXIT if moved else orca.RELOAD
    if not (pid := idle_pid(held["session"], partial(owes_him, drive) if seat.startswith("communicator") else None)):
        return f"its Claude Code process exited, so it loads {plugin} once it starts again", False
    transcript = watch.session_transcript(held)
    start = transcript.stat().st_size if transcript.exists() else 0
    if failed := sent_once(terminal, typed):
        return f"not reloaded ({failed})", False
    if not moved and not reload_shown(pid, held["session"], transcript, start):
        return f"{typed} typed in {terminal}, but no {RELOADED.removeprefix('<local-command-stdout>')} line showed in {transcript} while it sat idle {RELOAD_WAIT_SECONDS}s", False
    with state.locked(drive):
        path = state.drive_dir(drive) / RELOADS
        sent = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps(sent | {seat: {"onto": onto, "at": state.stamp()}}))
    return f"reloaded onto {plugin} in {terminal}" if not moved else f"{typed} typed in {terminal}{moved()}", True


def idle_pid(session: str, owing: Callable[[], bool] | None = None) -> int | None:
    """The pid of the Claude Code process running the session once its turn is over, waiting each second until the status Claude Code keeps in its sessions/<pid>.json reads idle, which it writes as the turn ends, never while it runs or shows a menu or permission prompt the command would answer, and for a communicator, by owing, never while a message of his waits for its answer, so no reload or restart lands mid-answer; a newer install landing meanwhile takes the wait over in this process by reopen, so no older install holds the seat's reload lock; None once the process exits or runs another session."""
    from drive import orca

    if not (pid := orca.live_pid(session)):
        return None
    since = time.time()
    while ((status := session_status(pid, session)) != "idle" and not turn_ended_since(session, since)) or (owing and owing()):
        if status is None:
            return None
        reopen(sys.argv[1:])
        sleep(1)
    return pid


def turn_ends(session: str) -> Path:
    """The file the Stop hook touches as the session's turn ends, which a reload waiting on that session reads as its moment."""
    path = state.root() / TURN_ENDS / session
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def turn_ended_since(session: str, since: float) -> bool:
    """Whether the session's turn ended since the reload began waiting, within TURN_END_SECONDS, so a reload types into a session that is never idle for a whole second, where Claude Code queues it as the next input and runs it between turns."""
    path = turn_ends(session)
    return path.exists() and (ended := path.stat().st_mtime) >= since - TURN_END_SECONDS and time.time() - ended <= TURN_END_SECONDS


def turn_ended(session: str) -> None:
    """From the Stop hook as a drive session's turn ends: mark the moment for any reload waiting on it, and start the seat's reload when it runs an older install than the newest and no process waits to type one, unless it is the communicator owing him an answer, by "a reload that waits for idle should instead fire at the session's next turn end"."""
    turn_ends(session).touch()
    for drive in state.session_drives(session):
        for seat, held in state.bound_sessions(state.saved(drive)).items():
            if held["session"] != session or reloaded_onto(drive, seat) >= newest_update()[0] or reload_held(drive, seat):
                continue
            if seat.startswith("communicator") and owes_him(drive):
                continue
            state.detach(drive, ["reload", "--seat", seat], RELOADS_LOG)


def owes_him(drive: str) -> bool:
    """Whether a message of his from the last ten minutes waits for its answer, as the drive's watch last read `cc-slack dm-owed` into its owed replies."""
    return state.answering(state.load(drive, ("owed_replies",)).get("owed_replies") or {}, state.now())


def session_status(pid: int, session: str) -> str | None:
    """The status Claude Code keeps for its process running the session, empty while its record is half written; None once the process exits or runs another session."""
    from drive import agents

    record = agents.registry_record(config_dir() / "sessions" / f"{pid}.json")
    if not state.alive(pid) or (record and record["sessionId"] != session):
        return None
    return record["status"] if record else ""


def sent_once(terminal: str, text: str) -> str | None:
    """Send text to the terminal under one --retry-request id, reissued each second for up to RELOAD_WAIT_SECONDS while Orca's failure leaves open whether it took it, so it is typed at most once; None once Orca takes it, else why not and the command that types it, naming that id when Orca never answered."""
    from drive import orca

    request_id = str(uuid.uuid4())
    until = clock() + RELOAD_WAIT_SECONDS
    while True:
        try:
            orca.send_command(terminal, text, request_id)
            return None
        except orca.OrcaError as error:
            if error.code not in orca.UNANSWERED:
                return f"{error}; send it with: {orca.reload_command(terminal, text)}"
            if clock() >= until:
                return f"{error}; reissue it with: {orca.reload_command(terminal, text, request_id)}"
        sleep(1)


def reload_shown(pid: int, session: str, transcript: Path, start: int) -> bool:
    """Whether /reload-plugins ran, as Claude Code's Reloaded: line in the session's transcript past start shows it, read forward each second from the last whole line read, through any turn that began before it ran, which it queues behind; False once the process exits, or once the session has read idle RELOAD_WAIT_SECONDS with no such line."""
    idle = 0
    while True:
        if transcript.exists():
            with open(transcript, "rb") as handle:
                handle.seek(start)
                read = handle.read()
            whole = read[: read.rfind(b"\n") + 1]
            if any(reloaded_row(line) for line in whole.splitlines()):
                return True
            start += len(whole)
        if (status := session_status(pid, session)) is None or idle >= RELOAD_WAIT_SECONDS:
            return False
        idle = idle + 1 if status == "idle" else 0
        sleep(1)


def reloaded_row(line: bytes) -> bool:
    try:
        row = json.loads(line)
    except ValueError:
        return False
    return row.get("type") == "system" and row.get("subtype") == "local_command" and row.get("content", "").startswith(RELOADED)


def newest_update() -> tuple[str, str]:
    """When a session last had a plugin install to load: the newest of the times each plugin version first showed in installed_plugins.json, among installs a session can load, ones whose project folder is still on this Mac, and that plugin and version. A version already installed that Claude Code installs again, as it does for a project's plugin in each new worktree of that project, moves nothing, so no session reloads onto the version it already runs."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    first: dict[tuple[str, str], str] = {}
    for key, entries in plugins.items():
        for entry in entries:
            if "projectPath" not in entry or Path(entry["projectPath"]).is_dir():
                pair = (key, entry["version"])
                first[pair] = min(first.get(pair, entry["lastUpdated"]), entry["lastUpdated"])
    (key, version), at = max(first.items(), key=lambda item: item[1])
    return at, f"{key} {version}"


def reloaded_onto(drive: str, seat: str) -> str:
    """The lastUpdated of the plugin install the seat's session last reloaded onto, empty before its first."""
    path = state.drive_dir(drive) / RELOADS
    return (json.loads(path.read_text()).get(seat) or {}).get("onto", "") if path.exists() else ""


def reload_held(drive: str, seat: str) -> str | None:
    """When the seat's session last reloaded, while that was under RELOAD_EVERY_SECONDS ago, so each session reloads at most once an hour, by his words of Nov 15 "drive-tooling orders costing so much means we have a lot of cruft and waste to cut down there", since each reload makes a root write its whole context to the cache again: an install landing in that hour is marked held on the seat, which the drive's watch reloads once the hour is up, by due_reloads, and a bare drive reload reloads at once; None once it may reload."""
    path = state.drive_dir(drive) / RELOADS
    at = (json.loads(path.read_text()).get(seat) or {}).get("at") if path.exists() else None
    return at if at and (state.now() - state.parse_time(at)).total_seconds() < RELOAD_EVERY_SECONDS else None


def hold_reload(drive: str, seat: str) -> None:
    """Mark in reloads.json that an install the seat's session has not loaded waits for its hour, which typed_reload's next stamp clears."""
    with state.locked(drive):
        path = state.drive_dir(drive) / RELOADS
        sent = json.loads(path.read_text())
        path.write_text(json.dumps(sent | {seat: sent[seat] | {"held": True}}))


def forget_reloads(drive: str, seat: str) -> None:
    """Drop a retired seat's row from reloads.json, so a version check never reads a seat that is gone as one lagging."""
    path = state.drive_dir(drive) / RELOADS
    with state.locked(drive):
        if path.exists() and seat in (sent := json.loads(path.read_text())):
            path.write_text(json.dumps({key: value for key, value in sent.items() if key != seat}))


def due_reloads(drive: str) -> list[str]:
    """Reload each session of the drive an install marked held once its hour is up, reading only reloads.json until one is due, then Orca as reload_sessions does, taking the held mark off each due seat first, so a reload that fails waits for the next install, as one an install started does, rather than being typed again on every watch pass; a line each."""
    path = state.drive_dir(drive) / RELOADS
    held = [seat for seat, stamp in (json.loads(path.read_text()) if path.exists() else {}).items() if stamp.get("held")]
    if not (due := [seat for seat in held if not reload_held(drive, seat)]):
        return []
    with state.locked(drive):
        sent = json.loads(path.read_text())
        path.write_text(json.dumps(sent | {seat: {key: value for key, value in sent[seat].items() if key != "held"} for seat in due}))
    return reload_sessions([drive], False)


def reload_lock(terminal: str) -> Path:
    """The file whose lock the one process typing a reload into the Orca terminal holds, whichever drive binds it."""
    path = state.root() / RELOAD_LOCKS / f"{terminal}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def homed(drive: str, seat: str, held: dict) -> Callable[[], str] | None:
    """Move a seat whose session's Claude Code process runs outside the drive's checkout into it, by his pick "Sessions run in the drive's dev-head checkout; every job inherits it", whose option restarts each role once, so the agents it starts and the repository tools it runs read the default branch head: the session registered with cci in the checkout, where cci session run resumes it in the tmux session of its alias once the /exit typed in place of its reload ends it, an open incident order included; None for a session already there, else what runs once the /exit lands and says what its reload line adds, for one no tmux session of cci's runs the Orca terminal that shows the one cci resumes it in."""
    from drive import agents, orca

    if not (ran := orca.launch_dir(held["session"])):
        return None
    if ran.resolve() == (kept := orca.checkout(drive, held["cwd"])).resolve():
        return None
    project = seat.removeprefix("sub-coordinator ") if seat.startswith("sub-coordinator ") else None
    role = "coordinator" if project else seat
    agents.register(drive, role, project, str(kept), held["session"])
    resumed = f"cci session run resumes it in the drive's checkout {kept}"
    if orca.in_tmux(alias := state.alias(drive, seat, project), held["session"]):
        return lambda: f", so {resumed}"
    return partial(attached, drive=drive, alias=alias, session=held["session"], show=agents.launch(drive, role, project, str(kept))[1], resumed=resumed)


def attached(drive: str, alias: str, session: str, show: str, resumed: str) -> str:
    """Once cci session run resumes the session in the tmux session of its alias, which cci's channel starts as the /exit ends its Claude Code process, or once RELOAD_WAIT_SECONDS pass, run Start or resume's `orca terminal create` line, whose `cci session attach` shows that tmux session in Orca, starting it when it is down, so he opens the seat from Orca's sidebar, since the one it ran in is left at a shell prompt; what its reload line adds."""
    from drive import orca

    until = clock() + RELOAD_WAIT_SECONDS
    while not orca.in_tmux(alias, session) and clock() < until:
        sleep(1)
    try:
        handle = orca.opened(shlex.split(show))
    except orca.OrcaError as error:
        return f", so {resumed} under tmux session {alias}, which no Orca terminal shows ({error}): show it with {show}"
    return f", so {resumed} under tmux session {alias}, shown in Orca terminal {handle}"


def reload_sessions(drives: list[str], force: bool) -> list[str]:
    """Reload every session bound to each drive: the communicator, the top coordinator, the meta session and each sub-coordinator, read from each drive's saved bindings alone, each in an Orca terminal one read of Orca and the process tree finds it runs in."""
    from drive import orca

    seats = [(name, seat, held) for name in drives for seat, held in state.bound_sessions(state.saved(name)).items()]
    try:
        found = orca.session_terminals([held["session"] for _, _, held in seats])
    except orca.OrcaError as error:
        return [f"{name} {seat}: not reloaded, since Orca could not show the terminals it runs in ({error})" for name, seat, _ in seats]
    return [reload_session(name, seat, found[held["session"]], force) for name, seat, held in seats]


def reload_main(drive: str | None, seat: str | None, force: bool, terminal: str | None = None) -> int:
    """`drive reload`: with --seat, the one process typing that seat's reload into its terminal; otherwise every session of the drive, or of every drive on this Mac, handed to that process now whatever installs reloaded it before."""
    if seat:
        reload_seat(drive, seat, force, terminal)
        return 0
    for line in reload_sessions([drive] if drive else state.names(), True):
        print(line)
    return 0


def main(plugin_at_marketplace: str, sha: str | None, repo: str | None, timeout: int, drive: str | None) -> int:
    """Wait for a release holding sha, its commit and each binary it pins, bring each sibling its manifest there needs up to that release first, Captain Hook.app only once he tapped Install now on the drive's card for it, update the plugin, run its step after an update from the new install, such as long-running's `drive migrate`, and its Homebrew twin until the bare binary holds sha, print the installed version, take the plugin off the install train's board when sha is the one it boarded with, by train.alighted, so an install by hand runs ahead of the train and the train installs it no second time, then reload each drive session on this Mac that has not reloaded onto the newest plugin install, as reload_session hands it on. A sha no release will hold prints why and installs nothing."""
    deadline = clock() + timeout
    try:
        version = install(plugin_at_marketplace, sha, repo, deadline, timeout, drive)
    except NoRelease as reason:
        print(reason)
        return 0
    except InstallError as error:
        print(f"drive install: {error}", file=sys.stderr)
        return 1
    print(f"installed {plugin_at_marketplace} {version}")
    if sha:
        train.alighted(plugin_at_marketplace, sha)
    for line in reload_sessions(state.names(), False):
        print(line)
    return 0
