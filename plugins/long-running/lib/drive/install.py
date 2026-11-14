import json
import os
import plistlib
import re
import signal
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
UNREACHED = re.compile(r"unable to access '|RPC failed|early EOF|remote end hung up")
AFTER_UPDATE = {"cc-inbox": ("bin/cci", "serve")}
HOMEBREW = {"cc-context": "ccx", "cc-inbox": "cci", "cc-notes": "cc-notes", "cc-orchestrate": "cco", "cc-present": "cc-present", "cc-review": "cc-review", "cc-slack": "cc-slack", "codex": "codex-ask"}
CASKS = {"cco": "cc-orchestrate"}
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
        except (github.GithubError, state.DriveError, state.NewerDrive) as error:
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


def handed(newer: Path, name: str, verb: list[str], receipt: Path, kept: dict, what: str, seconds: float) -> str:
    """Run the newest install's `drive <verb> <receipt>` on what an older install's verb kept in receipt, once a newer install saved the drive while that verb worked, so what it did lands either way; its stdout, or a refusal naming the receipt, which stays."""
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(kept))
    try:
        done = subprocess.run([str(newer), *verb, str(receipt), "--drive", name, "--json"], capture_output=True, text=True, check=False, timeout=seconds)
    except subprocess.TimeoutExpired:
        raise state.DriveError(f"{what}, and {newer} did not record it within {seconds:g}s; its receipt is kept in {receipt}") from None
    if done.returncode:
        raise state.DriveError(f"{what}, and {newer} did not record it: {done.stderr.strip()}; its receipt is kept in {receipt}")
    return done.stdout


def upgraded() -> Path | None:
    """The drive of the newest installed long-running, when this code runs from the plugin cache and a newer one is installed."""
    if not DRIVE.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        return None
    drive = newest_drive()
    return None if drive.resolve() == DRIVE else drive


def reopen(argv: list[str], stdin: str | None = None) -> None:
    """Run `drive <argv>` from the newest install in place of this process when a newer one is installed, so what this process reads and writes of a drive from here on runs on the newest install: a verb at its start, a verb a newer install's save refused before it wrote anything, given as stdin what it already read of its own, and a process that outlives its verb before each step, argv saying what it already did."""
    if not (newer := upgraded()):
        return
    if stdin is not None:
        with tempfile.TemporaryFile("w+") as given:
            given.write(stdin)
            given.seek(0)
            os.dup2(given.fileno(), 0)
    os.execv(newer, [str(newer), *argv])


def rechecked(hook: str, payload: dict) -> bool:
    """Run the newest install's copy of a drive hook on its payload in place of this one, as reopen runs a verb, when a newer one is installed and this hook changed nothing, as one a newer install's save refused mid-check: its output and exit code become this hook's; whether it ran."""
    if state.WROTE or not (newer := upgraded()):
        return False
    done = subprocess.run([sys.executable, "-I", str(newer.parents[1] / "hooks" / hook)], input=json.dumps(payload), capture_output=True, text=True, check=False)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    if done.returncode:
        raise SystemExit(done.returncode)
    return True


def newest(entries: list[dict]) -> dict:
    return max(entries, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")


def installs(key: str) -> list[dict]:
    """Each install of the plugin a session can load: the user scope's, and for each git repository the first project or local scope's whose folder is still on this Mac. Claude Code takes a repository's checkout and its worktrees for one project: a session started in any of them loads, and `claude plugin update` run in any of them moves, that repository's first entry in installed_plugins.json, so a later entry, such as the one an Orca workspace of the monorepo records when its first session starts, is never loaded or updated. Each install listed is updated and checked on its own, since `claude plugin update` moves one."""
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
    """Run the release's own drive status on a copy of each drive on this Mac before any scope switches to it, then its drive status of a job no drive holds, refusing a release that cannot read a drive or that answers that refusal with anything but its `drive:` line and exit 2, how every caller of drive reads a refusal, since every drive verb, tool, watch and hook runs the newest install."""
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
        if after := AFTER_UPDATE.get(plugin):
            run([str(Path(entry["installPath"]) / after[0]), *after[1:]], deadline)
        if binary := HOMEBREW.get(plugin):
            await_homebrew(clone, binary, sha, deadline, timeout)
    return entry["version"]


def reported_prs(data: dict, job_id: str) -> list[str]:
    """The drive PRs a builder's said line names, its READY or PUSHED's PRs, the ones its proof proves: a PR registered on the job that the line leaves out, such as one closed as superseded, owes the proof nothing; on a WAITING line, which names what it waits on, such as a release, rather than its PRs, each PR registered on the job but one closed unmerged."""
    job = data["jobs"][job_id]
    if re.match(r"WAITING\b", job["said"] or ""):
        return [key for key in dict.fromkeys(map(state.pr_key, job["prs"])) if key in data["prs"] and data["prs"][key]["state"] != "closed"]
    return state.named_prs(data, job["order"], job["said"] or "")


def merged_ready(data: dict, job_id: str) -> bool:
    """Whether the job is a builder still waiting on its taken READY or PUSHED, every PR of which merged."""
    job = data["jobs"][job_id]
    return (
        job["kind"] == "builder"
        and job["state"] == "waiting"
        and bool(state.REPORTED_LINE.match(job["said"] or ""))
        and bool(keys := reported_prs(data, job_id))
        and all(data["prs"][key]["merged_sha"] for key in keys)
    )


def repair(data: dict, job_id: str) -> bool:
    """Whether the job is a builder that opened no PR, its said line and its brief's Handoff naming none, such as a hand repair of the drive's records: drive job prove installs nothing for it and runs its proof file alone, on its own agent's call while it runs, on one take_back reopens, or again on the proof it marked waiting."""
    job = data["jobs"][job_id]
    named = f"{job['said'] or ''}\n{(job.get('brief_flags') or {}).get('handoff') or ''}"
    return job["kind"] == "builder" and not job["prs"] and not state.named_prs(data, job["order"], named)


def provable(data: dict, job_id: str) -> bool:
    """Whether drive job prove runs on the job: a builder waiting on a READY or PUSHED whose PRs all merged, or a repair at work or waiting."""
    return merged_ready(data, job_id) or repair(data, job_id) and data["jobs"][job_id]["state"] in ("running", "waiting")


def watch_proves(data: dict, job_id: str) -> bool:
    """Whether the watch proves the job: a builder waiting on a READY or PUSHED whose PRs all merged, or a repair waiting on the proof drive job prove marked on its said line, since only its own drive job prove starts a repair's first proof."""
    job = data["jobs"][job_id]
    return merged_ready(data, job_id) or repair(data, job_id) and job["state"] == "waiting" and bool(job.get("proof")) and job["proof"]["said"] == job["said"]


def take_back(name: str, data: dict, job_id: str) -> None:
    """Put a job that took BLOCKED, such as the BLOCKED its own proof took, back on the newest READY or PUSHED its history keeps, and a stopped or failed repair back on its said line, waiting, its proof marked as this process's as the watch marks one it starts, so drive job prove proves that line again on the job that owns it and no watch starts a second proof on it meanwhile."""
    job = data["jobs"][job_id]
    reported = job["state"] == "failed" and next((line for reply in state.returns(name, job_id) if state.REPORTED_LINE.match(line := state.opening(reply["body"]))), None)
    if reported or job["state"] in ("failed", "stopped") and repair(data, job_id):
        said = reported or job["said"]
        job.update(state="waiting", said=said, updated=state.stamp(), proof={"pid": os.getpid(), "at": state.stamp(), "said": said, "version": state.VERSION})


def product(data: dict, job_id: str) -> bool:
    """Whether the job's READY or PUSHED names PRs that all merge outside yasyf/*, a product change, which no drive install puts on this Mac."""
    return bool(keys := reported_prs(data, job_id)) and not any(state.merges_untapped(key) for key in keys)


def proof_gap(data: dict, job_id: str) -> str | None:
    """What keeps `drive job prove` from proving a builder whose PRs merged, naming who proves it instead, or None when it runs on its yasyf/* merges, its others merged at once: a job whose every merge is outside yasyf/* goes live only in its repository's release, and a job keeping no proof file the kernel can exec, as state.unrunnable reads it, leaves nothing to run."""
    job = data["jobs"][job_id]
    if product(data, job_id):
        return (
            f"{', '.join(reported_prs(data, job_id))} merged outside yasyf/*, where a merge goes live only in its repository's release, which no drive install"
            " puts on this Mac: a fresh builder with a merge handoff, added with drive job add builder --builds-nothing, proves it from its INSTALL: and PROVE: lines once that release is out"
        )
    repro = job.get("repro")
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


def await_ci(merges: dict[str, str]) -> bool:
    """Follow each workflow that ran on a merge's commit on its repository's default branch until it has completed its first run from that one on that was not cancelled, the run drive pr ci judges the merge by: a run its concurrency group cancelled for a newer push hands the wait to that push's run. The runs are read again each time the receiver hears a workflow run complete on that branch, the repository hooked first when no hook of the receiver's sends those, and a read that fails is said and read again within POLL_SECONDS; no deadline cuts the wait, since GitHub ends each run at its jobs' timeouts. Whether a read found any such run still running."""
    from drive import receiver

    waited = False
    for key, sha in merges.items():
        repo, triggered, last = key.partition("#")[0], None, ""
        while True:
            heard, failed = receiver.heard(repo, "ci"), False
            try:
                if receiver.unhooked({repo}):
                    receiver.hook(repo)
                branch = github.default_branch(repo)
                if triggered is None:
                    triggered = ci_runs(repo, f"branch={branch}&head_sha={sha}")
                if not (running := [f"{run['name']} {run['html_url']} {run['status']}" for run in ci_firsts(repo, branch, triggered).values() if run["status"] != "completed"]):
                    break
                waiting, waited = ", ".join(running), True
            except github.GithubError as error:
                waiting, failed = f"no run read: {error}", True
            if waiting != last:
                print(f"drive job prove: waiting for {key}'s CI at {sha[:12]}: {waiting}", file=sys.stderr, flush=True)
            last = waiting
            if failed:
                sleep(POLL_SECONDS)
            else:
                receiver.hear(repo, "ci", heard)
    return waited


def ci_runs(repo: str, query: str) -> list[dict]:
    jq = ".workflow_runs[] | {id, workflow_id, name, created_at, status, conclusion, html_url}"
    return [json.loads(line) for line in github.read(f"{repo}'s workflow runs", ["gh", "api", "--paginate", f"repos/{repo}/actions/runs?per_page=100&{query}", "--jq", jq]).splitlines()]


def ci_firsts(repo: str, branch: str, triggered: list[dict]) -> dict[int, dict]:
    """The first run not cancelled of each workflow in triggered, by its workflow id, counting by run id from its run there, the runs on a merge's commit, and never a run of a push merged before it, which GitHub can create in the merge's own second."""
    if not triggered:
        return {}
    starts: dict[int, int] = {}
    for run in triggered:
        starts[run["workflow_id"]] = min(starts.get(run["workflow_id"], run["id"]), run["id"])
    firsts: dict[int, dict] = {}
    for run in sorted(ci_runs(repo, f"branch={branch}&created=>={min(run['created_at'] for run in triggered)}"), key=lambda run: run["id"]):
        if run["workflow_id"] in starts and run["id"] >= starts[run["workflow_id"]] and run["conclusion"] != "cancelled":
            firsts.setdefault(run["workflow_id"], run)
    return firsts


def ci_verdict(repo: str, sha: str) -> list[tuple[str, bool]]:
    """How the CI of a merge at sha on repo's default branch judges it, a line naming each workflow that ran on that commit beside whether it passed: its first run not cancelled from its run there on, passed once it completed with a conclusion GitHub counts as passed, or none yet, which has not; a commit no workflow ran on has not passed."""
    branch = github.default_branch(repo)
    triggered = ci_runs(repo, f"branch={branch}&head_sha={sha}")
    if not triggered:
        return [(f"no workflow ran on {sha[:12]} on {branch}", False)]
    firsts = ci_firsts(repo, branch, triggered)
    names = {run["workflow_id"]: run["name"] for run in triggered}
    runs = [
        (f"{run['name']} {run['html_url']} {run['status']} {run['conclusion'] or ''}".rstrip(), run["status"] == "completed" and (run["conclusion"] or "").upper() in github.PASSED)
        for run in firsts.values()
    ]
    return runs + [(f"{names[workflow]}: no run since {sha[:12]} that was not cancelled", False) for workflow in names.keys() - firsts.keys()]


def merges_of(data: dict, job_id: str) -> dict[str, str]:
    return {key: data["prs"][key]["merged_sha"] for key in reported_prs(data, job_id)}


def finish(name: str, data: dict, job_id: str, line: str, check: list[str], post: str, facts: str) -> bool:
    """Take the line drive job prove sets on its builder, its report naming its PRs, if any, and CHECK:, and on a DONE that leaves its open order nothing to do, an open incident's aside, which the watch closes once every merged fix PR of it runs on each production stack, keep on the order its proof and the close report the watch posts to the communicator, opening POST with the PRs' titles, a repair's order title, and post; held while he sees any of the order's results on a surface no CHECK: came from. Whether the order closes."""
    from drive import brief, view

    job, keys = data["jobs"][job_id], reported_prs(data, job_id)
    order_id, urls = job["order"], " ".join(map(state.pr_url, keys))
    order = data["orders"][order_id]
    whole = f"{line}\n{f'PR: {urls}\n' if urls else ''}CHECK: {' '.join(check)}\n"
    state.keep_report(name, job_id, line, whole)
    state.take_result(data, job_id, line, whole)
    closes = line.startswith("DONE") and not brief.unseen(data, order, " ".join(check)) and order["state"] not in state.CLOSED_ORDER_STATES and all(state.delivered(data, order_id, part) for part in order["parts"])
    closes = closes and all(data["jobs"][other]["state"] in state.FINISHED_JOB_STATES for other in order["jobs"]) and not state.open_incidents(data, [order_id])
    if closes:
        title = "; ".join(data["prs"][key]["title"] for key in keys) or view.order_title(order)
        order["proof"] = " ".join(check)
        order["close_report"] = {
            "id": f"install-proved-{job_id}",
            "kind": "report",
            "to": [f"communicator@{name}"],
            "topic": f"install-proved-{order_id}",
            "attr": {"close": order_id} | state.thread_attr(order),
            "text": f"POST {title} {post}",
            "body": f"Fixed: {', '.join(filter(None, [title, urls]))}\n{facts}",
        }
    return closes


def said_on_prs(name: str, data: dict, job_id: str, keys: list[str], change: str) -> None:
    order_id = data["jobs"][job_id]["order"]
    lane = state.heard_lane(data, order_id)
    if not keys:
        state.append_event(name, "proof", job=job_id, order=order_id, change=change, lane=lane)
    for key in keys:
        merged_in, number = key.split("#")
        state.append_event(name, "proof", repo=merged_in, number=int(number), change=change, lane=lane)


def merged_done(name: str, data: dict, job_id: str) -> list[str]:
    """Take DONE at its merge on a builder whose READY, PUSHED or WAITING line's PRs all merged outside yasyf/*, a product change, its merge commits its CHECK:, keeping no proof and no close report on its order, since a merge goes live only once a release holding it is out, where the builder add_prover hands it to proves it; the release waits it held, which move to that builder."""
    job = data["jobs"][job_id]
    waits = job["waits"]
    merges = merges_of(data, job_id)
    urls = " ".join(map(state.pr_url, merges))
    merged = ", ".join(f"{key} at {sha[:12]}" for key, sha in merges.items())
    commits = " ".join(merge_commits(merges))
    line = f"DONE {urls} merged: {merged}"
    whole = f"{line}\nPR: {urls}\nCHECK: {commits}\n"
    state.keep_report(name, job_id, line, whole)
    state.take_result(data, job_id, line, whole)
    state.rewait(job, [])
    return waits


def add_prover(data: dict, job_id: str, reach: dict[str, dict], parts: dict[str, list[str]], waits: list[str]) -> str:
    """Hand a builder whose PRs all merged outside yasyf/* to the builder proving them live on the release holding each merge, so each merged PR has one: each PR's prover, by prover_of, takes its parts, each list under the order holding it, the first order its own, and one no agent or lane took yet also takes the PRs it lacks, their release waits, the release waits the merged job held, waits, and a merge handoff, or one proving them by their tests passing on dev when every PR changes only tests or CI config; while a PR has no prover and none of theirs can take it, a new one, marked as building nothing, so it holds no builder slot, goes on those parts in the merged job's session on its brief flags, waiting on the release of each component cli.release_reach's answer in reach finds its PRs' paths touch. That prover's id."""
    job = data["jobs"][job_id]
    merges = merges_of(data, job_id)
    found = list(dict.fromkeys(filter(None, (prover_of(data, key) for key in merges))))
    keys = [key for key in merges if not prover_of(data, key)]
    merged = ", ".join(f"{key} at {sha[:12]}" for key, sha in merges.items())
    handoff = (
        f"{merged} merged, changing only tests or CI config, which no release ships: prove it by its tests passing on dev at or after its merge commit"
        if keys and all(reach.get(key, {}).get("tested") for key in merges)
        else f"{merged} merged: install it and prove it live by the INSTALL: and PROVE: lines of {job_id}'s return before its DONE, in reports/{job_id}.md"
    )
    waits = list(dict.fromkeys([*waits, *(f"release:{component}:{key}" for key, answer in reach.items() if key in keys for component in answer["touched"])]))
    lead = next((builder_id for builder_id in found if state.untaken(data["jobs"][builder_id])), None)
    for builder_id in found:
        widen(data, builder_id, parts, *((keys, waits, handoff) if builder_id == lead else ([], [], None)))
    if lead or not keys:
        return lead or found[0]
    (order_id, own), *carried = parts.items()
    builder_id = state.next_id(data["jobs"], "j")
    builder = data["jobs"][builder_id] = state.new_job(order_id, "builder", f"prove {', '.join(keys)} live", own, state.stamp()) | {
        "session": job.get("session"),
        "waits": waits,
        "brief_flags": (job.get("brief_flags") or {}) | {"handoff": handoff},
        "builds_nothing": True,
    }
    state.hold_unstarted(builder)
    data["orders"][order_id]["jobs"].append(builder_id)
    for carried_id, carried_parts in carried:
        state.carry(data, builder_id, carried_id, carried_parts)
    return builder_id


def widen(data: dict, builder_id: str, parts: dict[str, list[str]], keys: list[str], waits: list[str], handoff: str | None) -> None:
    """Give a prover more parts, each list under the order holding it, and, while no agent or lane took it, the PRs keys in its goal, their release waits and a handoff it lacks, so one prover proves each merged PR live; a prover at work or done takes the parts alone, its DONE delivering them."""
    builder = data["jobs"][builder_id]
    for order_id, held in parts.items():
        state.deliver(data, builder_id, order_id, held)
    if not state.untaken(builder):
        return
    if keys:
        builder["goal"] = f"prove {', '.join([*proves(data, builder_id), *keys])} live"
    flags = builder.get("brief_flags") or {}
    if handoff and handoff not in (flags.get("handoff") or ""):
        builder["brief_flags"] = flags | {"handoff": "; ".join(filter(None, [flags.get("handoff"), handoff]))}
    state.rewait(builder, [wait for wait in dict.fromkeys([*builder["waits"], *waits]) if wait != state.SLOT_WAIT])
    state.hold_unstarted(builder)


def prover_of(data: dict, key: str) -> str | None:
    """The one builder proving the merged PR at key live, on whatever order holds it, marked as building nothing or not: the earliest done, whose DONE proved it, then the earliest an agent or lane took, then the earliest none took yet, of the builders whose goal reads prove <PRs> live naming it or whose DONE names it, never the DONE the watch takes at a merge, which proves nothing live; else the earliest such prover that failed or stopped, so its part reads ORPHAN and the watch adds no second; None while no builder proves it."""
    number = key.rpartition("#")[2]
    found = [
        (rank, position, job_id)
        for position, (job_id, job) in enumerate(data["jobs"].items())
        if job["kind"] == "builder" and (number in job["goal"] or number in (job["said"] or "")) and (rank := proof_rank(data, job_id, key)) is not None
    ]
    return min(found)[2] if found else None


def proof_rank(data: dict, job_id: str, key: str) -> int | None:
    job = data["jobs"][job_id]
    if state.done_at_merge(job):
        return None
    if job["state"] == "done":
        return 0 if key in proves(data, job_id) or key in state.named_prs(data, job["order"], job["said"] or "") else None
    if key not in proves(data, job_id):
        return None
    if job["state"] in state.ENDED_JOB_STATES:
        return 3
    return 2 if state.untaken(job) else 1


def proves(data: dict, job_id: str) -> list[str]:
    """The PRs a builder whose goal reads prove <PRs> live, as add_prover writes it, proves live."""
    job = data["jobs"][job_id]
    goal = state.PROVER_GOAL.fullmatch(job["goal"]) if job["kind"] == "builder" else None
    return state.named_prs(data, job["order"], goal["keys"]) if goal else []


def merged_said(name: str, builder_id: str, change: str) -> str:
    """The one line saying the builder add_prover added or widened, by change, to prove live the PRs of builders that took DONE at their merge, whatever their number, counting the releases it waits on, which drive status lists, briefed here so its agent starts on the watch's FREE once the last is out, or in the turn this line wakes when it waits on none, or why its brief was not written; one an agent or lane already took, or that finished, keeps its brief and says its state."""
    from drive import cli

    data = state.load(name)
    builder = data["jobs"][builder_id]
    proven = proves(data, builder_id) or state.named_prs(data, builder["order"], builder["said"] or "")
    head = f"JOB {builder_id} {builder['order']} {change}, proves {', '.join(proven)}"
    if not state.untaken(builder):
        return f"{head}, already {builder['state']}: drive status {builder_id}"
    try:
        started = cli.write_brief(name, builder_id, {})[0].splitlines()[0]
    except cli.VERB_ERRORS as error:
        return f"{head}, has no brief: {error}; brief it with drive job brief {builder_id}"
    if waits := len(builder["waits"]):
        return f"{head}, waits on {waits} release{'s' if waits > 1 else ''}: drive status {builder_id}"
    return f'{head}, waits on no release: start its agent now, run_in_background, on the prompt "{started}"'


def proofless(name: str, data: dict, job_id: str) -> None:
    """Take BLOCKED on a builder whose READY or PUSHED's PRs all merged, a yasyf/* one among them, keeping no executable proof file, its merge commits its CHECK:, since nothing proves it live until drive job set keeps one and drive job prove runs it on the job."""
    line = (
        f"BLOCKED {' '.join(map(state.pr_url, reported_prs(data, job_id)))} merged, and {job_id} keeps no proof file to run, so nothing proves it live:"
        f" keep one with drive job set {job_id} --repro <file>, written from its PROVE: line, then drive job prove {job_id} proves it on {job_id}"
    )
    finish(name, data, job_id, line, merge_commits(merges_of(data, job_id)), "", "")


def merge_commits(merges: dict[str, str]) -> list[str]:
    return [f"https://github.com/{key.partition('#')[0]}/commit/{sha}" for key, sha in merges.items()]


def prove(name: str, job_id: str, timeout: int) -> str:
    """Prove live a builder's READY or PUSHED whose PRs all merged, the step the watch starts, on a builder that took BLOCKED, its own proof's among them, the newest READY or PUSHED its history keeps, which take_back puts it back on, so a failed proof runs again on the job that owns it: install each plugin a yasyf/* merge changes whose installs, binaries, Homebrew copy or Captain Hook.app lack it, its other merges taken at once, wait until the drive's dashboard runs the newest long-running installed, then run the job's proof file with the bin folders of those plugins' newest installs, read right before it runs, first on PATH, as newest_builds builds it, and the drive named, as proof_run does, with no wait for CI, since no install reads a CI run; when it fails while a workflow a yasyf/* merge ran on its default branch has not completed its first run from the merge on that was not cancelled, the run drive pr ci judges the merge by, follow that run to its end with no deadline, as await_ci does, and run the file once more, as it does once ccx's GitHub rate-limit floor lifts for a run its timeout cut off while it waited that floor out, and run it again on the newer installs when it fails while a newer install of any of them landed, each run of the file given the whole timeout from its own start, as proof_run gives it, so neither the installs, the dashboard's handover, a wait on CI nor an earlier run shortens it, keep the last run's output in reports/<j>.proof.txt, take DONE on exit 0 with output, since a proof file printing nothing proves nothing, each Slack post or picture link it names opening, as brief.unopened reads it, so a stand-in permalink proves nothing either, that output proving each user story the job's brief names on his surface, as brief.story_gap reads it, and the proof file taking the step its brief's Mac: line names and reading back what it changed, as brief.mac_gap reads it, and BLOCKED otherwise, its line naming that file and quoting the run's last line opening FAIL, else saying it printed none, and a nonzero exit ending on a WAIT line with no FAIL line, or an install held for his card, which meet_helper's WAIT says, taking no result, any other install that fails, a merge no release will carry, or a dashboard still on an older install at the deadline taking BLOCKED naming it, and keep the close report on its order for the watch to post when DONE leaves the order nothing to do, but an open incident's, which the watch closes once DONE records its fix live, each said on its PRs' lines; on a repair, a builder that opened no PR, at work or waiting, or stopped or failed as take_back reopens it, its proof marked as this process's from its start, install nothing, run the file on the drive's own long-running, take its DONE or BLOCKED as finish does, naming the job for its PRs and closing its order under the order's title, and on a WAIT keep the job waiting on a WAITING line quoting it, its proof marked waiting on it, said on the job's own line; refused for a job waiting on no such READY or PUSHED that is no such repair, or one proof_gap names, the job left as it was; nothing after another proof took the job's result first. When a newer install saved the drive while it installed, waited on CI or ran the proof file, that install's `drive job proved` keeps the result, which proof_kept hands it."""
    from drive import brief, dashboard

    with state.edit(name) as data:
        take_back(name, data, job_id)
        if not provable(data, job_id):
            raise state.DriveError(f"{job_id} waits on no READY or PUSHED whose PRs all merged, so there is nothing to prove")
        if why := proof_gap(data, job_id):
            raise state.DriveError(why)
        if repair(data, job_id):
            data["jobs"][job_id]["proof"] = {"pid": os.getpid(), "at": state.stamp(), "said": data["jobs"][job_id]["said"], "version": state.VERSION}
    job = data["jobs"][job_id]
    merges = merges_of(data, job_id)
    urls = " ".join(map(state.pr_url, merges)) or f"{job_id}'s repair"
    untapped = {key: sha for key, sha in merges.items() if state.merges_untapped(key)}
    try:
        plugins = merged_plugins(untapped, clock() + timeout, timeout, name) if merges else []
    except (InstallError, NoRelease) as error:
        code, output, versions = "unrun", str(error), "no install holding it"
        line = f"BLOCKED {urls}: not installed, so {job['repro']} did not run: {str(error)[:SAID_CHARS]}"
    else:
        versions = newest_builds(plugins)[0]
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
                        print(f"drive job prove: {job['repro']} exits {code} on {versions} while its merge's CI ran: running it again now that CI completed", file=sys.stderr, flush=True)
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
        elif code == 0 and (shut := brief.dead([found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)])):
            code, tail = "0 but names a link that does not open", f"{shut[0]}: a proof file prints the live post or picture it read back, never a stand-in"
        elif code == 0 and brief.surface(job) and (gap := brief.story_gap(job_id, job, brief.proofs(output), output)):
            code, tail = "0 but falls short of its surface", gap
        elif code == 0 and (gap := brief.mac_gap(job, output)):
            code, tail = "0 but leaves its Mac: step undone", gap
        versions = versions if merges else f"{LONG_RUNNING} {state.VERSION}"
        line = (
            f"DONE {urls} live: {job['repro']} exits 0 on {versions}"
            if code == 0
            else f"BLOCKED {urls}: {job['repro']} exits {code} on {versions}, its output {state.proof_path(name, job_id)}: {tail[:SAID_CHARS]}"
        )
    return proof_kept(name, job_id, {"line": line, "output": output, "versions": versions, "code": code})


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
    """Run the proof file as subprocess.run would, its output read as text, PATH set to `path`, the one newest_builds builds, DRIVE_ENV naming the drive it proves, so each drive call in it that names no drive acts on that one whatever session or drives this Mac holds, and PROOF_DEADLINE_ENV the Unix second its deadline, `timeout` seconds from its start, falls at, so a proof that waits prints its WAIT line before it, but as a process group of its own, which reads.end ends at that deadline with SIGINT, the signal that unwinds a Python proof, one run with -I included, so it writes what it printed before it exits, and SIGKILL reads.STOP_GRACE seconds on; the TimeoutExpired raised then holds everything the group wrote."""
    from drive import reads

    with subprocess.Popen([repro], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ | {"PATH": path, state.DRIVE_ENV: name, PROOF_DEADLINE_ENV: str(int(time.time() + timeout))}, start_new_session=True) as running:
        try:
            out, err = running.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            raise subprocess.TimeoutExpired(expired.cmd, expired.timeout, *reads.end(running, time.monotonic() + reads.STOP_GRACE, signal.SIGINT)) from None
    return subprocess.CompletedProcess([repro], running.returncode, out, err)


def proof_kept(name: str, job_id: str, result: dict) -> str:
    """Keep what a proof came to on its job, its output in reports/<j>.proof.txt, and say it on the job's PRs' lines, a repair's on its own, nothing after another proof took the job's result first; a run waited on his next word, a WAIT line ending a nonzero exit with no FAIL line, takes no result, so the job stays waiting on its READY or PUSHED, a repair taking the WAITING that quotes it, and marks its proof waiting, which the watch runs again once he is heard after the run started; when a newer install saved the drive while the proof ran, as one the proof installed does, the newest install's `drive job proved` keeps it, so the proof never runs twice."""
    from drive import brief

    kept = state.proof_path(name, job_id)
    line, output, versions, code = result["line"], result["output"], result["versions"], result["code"]
    try:
        with state.edit(name) as data:
            job = data["jobs"][job_id]
            if not provable(data, job_id):
                return f"{job_id}: another proof took its result first"
            keys = reported_prs(data, job_id)
            proven = f"{PROVEN} on {versions}: {job['repro']} exits 0, its output {kept}"
            kept.parent.mkdir(exist_ok=True)
            kept.write_text(output)
            if wait := waited(code, output):
                if not keys and job["said"] != (said := f"WAITING {PROOF_WAITS}: {wait}"):
                    state.keep_report(name, job_id, said, f"{said}\nCHECK: {kept}\n")
                    state.take_result(data, job_id, said)
                job["proof"] = (job.get("proof") or {"pid": os.getpid(), "at": state.stamp(), "version": state.VERSION}) | {"said": job["said"], "waits": wait}
                change = f"{PROOF_WAITS}: {line.removeprefix('BLOCKED ')}"
            else:
                seen = [found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)]
                post, facts = (f"is live in {versions}", f"Live: {versions}\n") if keys else ("is fixed", "")
                closes = finish(name, data, job_id, line, [str(kept), *seen], post, f"{facts}Proof: {proven}\n")
                change = proven
                if code:
                    change = f"{PROOF_FAILED}: {line.removeprefix('BLOCKED ')}"
                elif closes:
                    change = f"{proven}; its close report of {job['order']} waits on the order for the watch, which posts it to the communicator on its next read and again each read until cci takes it"
    except state.NewerDrive:
        if not (newer := upgraded()):
            raise
        receipt = state.drive_dir(name) / "jobs" / f"{job_id}.proved.{os.getpid()}.json"
        return json.loads(handed(newer, name, ["job", "proved", job_id], receipt, result, f"{job_id}: its proof", state.DRIVE_LOCK_SECONDS))["line"]
    said_on_prs(name, data, job_id, keys, change)
    return f"{job_id}: {change}"


def reload_session(drive: str, seat: str, version: str, updated: datetime, deadline: float) -> str:
    """Type /reload-plugins into one drive session's Orca terminal once it is idle, or after RELOAD_WAIT_SECONDS into its busy prompt, which loads an install's skills, agents and new hooks with nothing restarted, unless another install sent it one after this install's update, which loaded this one too; a session homed ends there types /exit instead, which cci resumes in the drive's checkout on the newest install. A seat is a role, or `sub-coordinator <project>`."""
    from drive import orca

    who = f"{drive} {seat}"
    held = state.bound_sessions(state.saved(drive)).get(seat, {})
    if not (terminal := held.get("terminal")):
        return f"{who}: no terminal recorded, skipped"
    if sent := claim_reload(drive, seat, version, updated):
        return f"{who}: already reloaded onto {sent['version']}"
    wait = max(0.001, min(RELOAD_WAIT_SECONDS, deadline - clock() - orca.SUBMIT_SECONDS))
    typed = orca.RELOAD
    try:
        moved, ends = homed(drive, seat, held)
        typed = orca.EXIT if ends else orca.RELOAD
        idle = orca.typed_when_idle(terminal, typed, int(wait * 1000))
    except state.DriveError as error:
        unclaim_reload(drive, seat, version)
        return f"{who}: not reloaded ({error}); send it with: {orca.reload_command(terminal, typed)}"
    if not idle:
        return f"{who}: busy for {wait:.0f}s, so {typed} is queued in {terminal} and runs when its turn ends{moved}"
    return f"{who}: {'reloaded' if typed == orca.RELOAD else f'{typed} typed'} in {terminal}{moved}"


def homed(drive: str, seat: str, held: dict) -> tuple[str, bool]:
    """Move a seat whose session's Claude Code process runs outside the drive's checkout into it, by his pick "Sessions run in the drive's dev-head checkout; every job inherits it", whose option restarts each role once, so the agents it starts and the tools/ci it runs read the default branch head: the session registered with cci in the checkout, where cci resumes it at its next exit, and ended now unless an incident order is open, which drive move refuses too, or no tmux session of cci's runs it, which would leave nothing to resume it; what its reload line adds, and whether it ends now."""
    from drive import agents, orca

    if not (ran := orca.launch_dir(held["session"])):
        return "", False
    if ran.resolve() == (kept := orca.checkout(drive, held["cwd"])).resolve():
        return "", False
    project = seat.removeprefix("sub-coordinator ") if seat.startswith("sub-coordinator ") else None
    agents.register(drive, "coordinator" if project else seat, project, str(kept), held["session"])
    resumed = f"cci session run resumes it in the drive's checkout {kept}"
    orders = state.stored(drive, ("orders",))
    if incidents := state.open_incidents(orders, list(orders["orders"])):
        return f"; it runs in {ran} until it next exits, when {resumed}, since incident {', '.join(incidents)} is open", False
    if not orca.in_tmux(state.alias(drive, seat, project), held["session"]):
        return f"; it runs in {ran} until it next exits, when {resumed}, since no tmux session of cci's runs it", False
    return f", so {resumed}", True


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
    """Wait for a release holding sha, its commit and each binary it pins, bring each sibling its manifest there needs up to that release first, Captain Hook.app only once he tapped Install now on the drive's card for it, update the plugin and its Homebrew twin until the bare binary holds sha, print the installed version, then reload every drive session on this Mac. A sha no release will hold prints why and installs nothing."""
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
    for line in reload_sessions(version, updated, deadline):
        print(line)
    return 0
