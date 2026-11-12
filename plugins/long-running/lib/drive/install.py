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
PROVEN = "proven live by drive install"
SAID_CHARS = 300


class InstallError(Exception):
    pass


class UnknownPlugin(InstallError):
    pass


def clock() -> float:
    return time.monotonic()


def sleep(seconds: float) -> None:
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


def bare_clone(clone: str, repo: str, deadline: float) -> None:
    run(["git", "init", "--bare", "--quiet", clone], deadline)
    git(clone, "remote", "add", "origin", f"https://github.com/{repo}", deadline=deadline)


def await_release(clone: str, repo: str, plugin: str, sha: str, deadline: float, timeout: int) -> tuple[str, str]:
    """The newest release the marketplace names for the plugin once it holds sha, its ref and the plugin's folder in it."""
    last = "no release read"
    failure = ""
    bare_clone(clone, repo, deadline)
    while True:
        try:
            named = newest_release(clone, plugin, deadline)
            lacking = release_lacking(clone, repo, named, sha, deadline)
            failure = ""
            if named and lacking is None:
                return named
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
        with state.flocked(config_dir() / "plugins" / UPDATE_LOCK):
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
    with state.flocked(config_dir() / "plugins" / UPDATE_LOCK):
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


def upgraded() -> Path | None:
    """The drive of the newest installed long-running, when this code runs from the plugin cache and a newer one is installed."""
    if not DRIVE.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        return None
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    drive = Path(newest([entry for key, entries in plugins.items() if key.partition("@")[0] == "long-running" for entry in entries])["installPath"]) / "bin" / "drive"
    return None if drive.resolve() == DRIVE else drive


@contextmanager
def handed_to_newest(argv: list[str]) -> Iterator[None]:
    """Run `drive <argv>` from the newest install in place of this process when the block is refused because a newer install saved drive.json while this verb acted, since this code may misread it: argv records what the verb already did, such as a worker it started, and does none of it again."""
    try:
        yield
    except state.NewerDrive:
        if not (newer := upgraded()):
            raise
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
            named = newest_release(clone, plugin, deadline)
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
        with state.flocked(config_dir() / "plugins" / UPDATE_LOCK):
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


def proved(key: str, repo: str | None, deadline: float) -> list[str]:
    """Prove each fix the install of key made live, with no session turn: each builder of every drive still waiting on its READY or PUSHED with a kept proof file, whose PRs the drive saw merge into the plugin's repo, once the newest install of every plugin its merges change holds them, key among them; a line for each."""
    if not (repo := repo or source_repo(known_marketplaces()[key.partition("@")[2]]["source"])):
        return []
    if not (due := [(name, job_id) for name in state.names() for job_id in provable(state.load(name), repo)]):
        return []
    with tempfile.TemporaryDirectory(prefix="drive-install-") as clone:
        bare_clone(clone, repo, deadline)
        return [line for name, job_id in due if (line := prove(name, job_id, key, repo, clone, deadline))]


def provable(data: dict, repo: str) -> list[str]:
    return [
        job_id
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder"
        and job["state"] == "waiting"
        and state.REPORTED_LINE.match(job["said"] or "")
        and job.get("repro")
        and os.access(job["repro"], os.X_OK)
        and job["prs"]
        and all(state.pr_key(url).partition("#")[0].lower() == repo.lower() and data["prs"][state.pr_key(url)]["merged_sha"] for url in job["prs"])
    ]


def prove(name: str, job_id: str, key: str, repo: str, clone: str, deadline: float) -> str | None:
    """Run the job's kept proof file with the bin folder of each plugin its merges change first on PATH, keep its output beside the job's report, take DONE on exit 0 and BLOCKED otherwise, and send the close report when DONE leaves its order nothing to do, each said on its PRs' lines; nothing while an install another plugin needs is still to come, or after another install took the job's result first."""
    data = state.load(name)
    job = data["jobs"][job_id]
    merges = {state.pr_key(url): data["prs"][state.pr_key(url)]["merged_sha"] for url in job["prs"]}
    git(clone, *FETCH, *merges.values(), deadline=deadline)
    changes = {pr: touched(repo, git(clone, "diff-tree", "-r", "--name-only", f"{sha}^", sha, deadline=deadline).splitlines()) for pr, sha in merges.items()}
    if key not in {plugin for plugins in changes.values() for plugin in plugins}:
        return None
    builds = {plugin: installed(plugin) for plugins in changes.values() for plugin in plugins}
    git(clone, *FETCH, *{build["gitCommitSha"] for build in builds.values()}, deadline=deadline)
    if not all(holds(clone, builds[plugin]["gitCommitSha"], merges[pr], deadline) for pr, plugins in changes.items() for plugin in plugins):
        return None
    path = os.pathsep.join([*(str(Path(build["installPath"]) / "bin") for build in builds.values()), os.environ.get("PATH", "")])
    try:
        done = subprocess.run([job["repro"]], check=False, capture_output=True, text=True, timeout=max(deadline - clock(), 0.001), env=os.environ | {"PATH": path})
        code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as expired:
        code, output = "past drive install's deadline", f"{expired.stdout or ''}{expired.stderr or ''}"
    except OSError as error:
        code, output = f"unrun ({error})", ""
    kept = state.report_path(name, job_id).with_suffix(".proof.txt")
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(output)
    versions = ", ".join(f"{plugin} {build['version']}" for plugin, build in builds.items())
    urls = " ".join(job["prs"])
    tail = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "no output")
    line = f"DONE {urls} live: {job['repro']} exits 0 on {versions}" if code == 0 else f"BLOCKED {urls}: {job['repro']} exits {code} on {versions}: {tail[:SAID_CHARS]}"
    from drive import brief, cli

    with state.edit(name) as data:
        job, order = data["jobs"][job_id], data["orders"][data["jobs"][job_id]["order"]]
        if job["state"] != "waiting" or not state.REPORTED_LINE.match(job["said"] or ""):
            return None
        seen = [found for found in brief.proofs(output) if brief.SEEN_PROOF.search(found)]
        whole = f"{line}\nPR: {urls}\nCHECK: {' '.join([str(kept), *seen])}\n"
        state.keep_report(name, job_id, job, line, whole)
        state.take_result(data, job_id, line, whole)
        unseen = cli.surface(data, order) if not seen else None
        closes = code == 0 and not unseen and all(state.delivered(data, job["order"], part) for part in order["parts"])
        closes = closes and all(data["jobs"][other]["state"] in state.FINISHED_JOB_STATES for other in order["jobs"])
        if closes:
            order["proof"] = " ".join([str(kept), *seen])
        cwd, order_id, lane, thread = data["roles"]["coordinator"]["cwd"], job["order"], state.heard_lane(data, job["order"]), order["thread"]
        title = "; ".join(data["prs"][pr]["title"] for pr in merges)
    change = f"{PROVEN} on {versions}: {job['repro']} exits 0, its output {kept}"
    if code:
        change = f"drive install's proof failed: {line.removeprefix('BLOCKED ')}"
    elif unseen:
        change = f"drive install's proof passed on {versions}, but he sees this on {unseen}: prove it there with a recording or the live post, then send the close report"
    if closes:
        change = closed(name, order_id, title, urls, versions, change, thread, cwd, deadline)
    for pr in merges:
        merged_in, number = pr.split("#")
        state.append_event(name, "proof", repo=merged_in, number=int(number), change=change, lane=lane)
    return f"{name} {job_id}: {change}"


def closed(name: str, order_id: str, title: str, urls: str, versions: str, change: str, thread: str | None, cwd: str, deadline: float) -> str:
    """Send the communicator the report whose post closes the order its proven job finishes, as a builder's DONE gets; the proof line, naming a post that failed."""
    facts = f"Fixed: {title}, {urls}\nLive: {versions}\nProof: {change}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md") as body:
        body.write(facts)
        body.flush()
        argv = ["cci", "post", "--drive", name, "--lane", "watch", "--kind", "report", "--to", f"communicator@{name}", "--topic", f"install-proved-{order_id}"]
        argv += ["--attr", f"close={order_id}", *(["--attr", f"thread={thread}"] if thread else []), "--body-file", body.name, "--text", f"POST {title} is live in {versions}"]
        try:
            run(argv, deadline, cwd)
        except InstallError as error:
            return f"drive install proved it, but its close report of {order_id} did not post, so send it by the builder DONE row: {error}"
    return f"{change}; its close report of {order_id} went to the communicator"


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
    """Wait for a release holding sha, its commit and each binary it pins, bring each sibling its manifest there needs up to that release first, Captain Hook.app only once he tapped Install now on the drive's card for it, update the plugin and its Homebrew twin until the bare binary holds sha, print the installed version, reload every drive session on this Mac, and prove each fix the install made live; each drive's dashboard hands itself to the new install on its own."""
    deadline = clock() + timeout
    try:
        version = install(plugin_at_marketplace, sha, repo, deadline, timeout, drive)
    except InstallError as error:
        print(f"drive install: {error}", file=sys.stderr)
        return 1
    updated = state.now()
    print(f"installed {plugin_at_marketplace} {version}")
    for line in reload_sessions(version, updated, deadline):
        print(line)
    try:
        lines = proved(plugin_at_marketplace, repo, deadline)
    except InstallError as error:
        print(f"drive install: installed, but its proof of the fixes it made live failed: {error}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0
