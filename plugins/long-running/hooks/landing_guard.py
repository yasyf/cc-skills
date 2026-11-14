import csv
import json
import os
import re
import sys
from collections.abc import Generator, Iterator
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, card, github, land, limits, public_check, reports, settled, slack, state, superseded, view
from drive.cli import tap_verdict
from drive.github import repo_name
from drive.shell import expanded, runnable, templates

ADMIN_REASON = "Admin merges bypass required review; never on Forge-AI repos"
TAP_HINT = "Draw his PR card with slack_dm_status tap_prs, or record an exemption with drive pr set --exempt"
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
SHELL_SCRIPT_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
KEYWORDS = {"!", "{", "if", "then", "elif", "else", "do", "while", "until"}
LOOPS = {"for", "while", "until"}
LEADING = KEYWORDS - LOOPS
WRAPPERS = {
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "time": frozenset(),
    "nice": frozenset({"-n"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "xargs": frozenset({"-P", "-n", "-I", "-L", "-d", "-s", "-a", "-E"}),
}
CHDIR = {"-C", "--chdir"}
REPO_VARIABLE = "GH_REPO"
SHELLS = {"sh", "bash", "zsh", "dash"}
FALSE_WORDS = {"0", "f", "false"}
MERGE_ENDPOINT = re.compile(r"/?repos/([^/\s]+/[^/\s]+)/pulls/(\d+)/merge(?:-async)?")
INCIDENT_FIX = "incident fix"
QUEUE_REASON = (
    "Refused on purpose: {key} lands only through the Trunk merge queue, since every other way into Forge-AI/monorepo"
    " skips the checks it runs. Enqueue it with `.agents/skills/submit-pr/scripts/stack-enqueue <PR>...`, naming every"
    ' PR of its stack. An incident fix skips the queue only once `drive pr set <url> --exempt "incident fix"` records it'
)
CARD_REASON = (
    "Refused on purpose: {key} waits on his open card {cards}, and its recommended option never stands in for his answer."
    " Land and enqueue nothing: return `WAITING {cards}`; the watch says FREE once he answers"
)
RELEASE_UNASKED = (
    "Refused on purpose: this Sprite holds no copy of the drive, so it asked the coordinator's Mac with `drive job held"
    " --release` whether his hold on every release refuses `{release}`, and the Mac did not say: {error}. Run it again once"
    " it answers"
)
UNASKED = (
    "Refused on purpose: this Sprite holds no copy of the drive, so it asked the coordinator's Mac with `drive job held --pr`"
    " whether his open cards hold {keys}, and the Mac did not say: {error}. Land and enqueue nothing until it answers, then"
    " run this again"
)
STACK_REASON = (
    "Refused on purpose: {named} leaves out {left}, which his Approve lands with it: the PRs his card drew and every PR under them."
    " Name each of them: `stack-enqueue {every}`"
)
COORDINATOR_STEP = (
    "Refused on purpose: the {who} does no order's work itself, so its turn never blocks, and `{step}` is such"
    ' work. Start a job with this step (`drive job add <kind> --order <o> --goal "<the step>"`), or hand it to the job'
    " that owns it with `drive job resume <j> -`"
)
EXPOSED = "Refused on purpose, before it goes up: {exposed}. Fix the body, then run this again"
UNWRITTEN = (
    "Refused on purpose, before it goes up: the body file {file} does not exist yet, so its body cannot be checked"
    " against {repo}, a public repository. Write the file in a call of its own, then run this one"
)
UNCHECKED = (
    "Refused on purpose, before it goes up: GitHub did not answer whether a repository this body goes to or names is"
    " public, or cc-slack did not list the teammates whose names it must not show ({error}), so the body cannot be"
    " checked. Run this again once both answer"
)
UNREAD = (
    "landing_guard let this run unchecked, since a read it needed failed, so a Forge-AI drive PR's tap and open cards"
    " went unread. Once GitHub answers, read them with `drive status <PR url>`. The read said: {error}"
)
CCI_SERVER = "plugin:cc-inbox:cci"
CCI_TOOLS = "mcp__plugin_cc-inbox_cci__"
CLI_POST = (
    "Refused on purpose: this session's cci MCP server is connected, so it posts, answers and closes records with the cci"
    " `post` and `close` tools (ToolSearch `select:mcp__plugin_cc-inbox_cci__post,mcp__plugin_cc-inbox_cci__close` loads"
    " them when deferred), never `cci post` in Bash, by his Nov 12 ruling: \"metadata session is using the CLI insrqad of"
    " mcp for `cci post`\". `cci post` in Bash is for a session whose cci server is gone, and while cci is down entirely"
    " a session reaches another with ListAgents, then SendMessage to `<drive>-<role>`"
)
AGENTS = "long-running:"
COORDINATOR_WORK = (
    ("ccx", "vcs", "ship"),
    ("ccx", "vcs", "stack"),
    ("gh", "pr", "create"),
    ("git", "commit"),
    ("git", "push"),
    ("jj", "git", "push"),
    ("go", "build"),
    ("go", "test"),
    ("cargo", "build"),
    ("cargo", "test"),
    ("make",),
)
RELEASE_CUTS = frozenset({"start", "hotfix", "resume", "approve", "rollback", "rerun", "api"})
PLAN_ONLY = frozenset({"--plan", "--dry-run"})
RELEASE_PIPELINE = "release"
RELEASE_BUILDS = re.compile(rf"(?:^|/)pipelines/{RELEASE_PIPELINE}/builds(?:/(\d+))?(?:/|$)")
RELEASE_BUILDKITE = f"forge/{RELEASE_PIPELINE}"
RELEASE_START_ENV = "RELEASE_START"
HOLD_UNREAD = (
    "Refused on purpose: whether he holds every release could not be read, so `{command}` waits until it can: {error}."
    " Run it again once it answers"
)
CUT_UNREAD = (
    "Refused on purpose: whether this forge/release build is a release cut, one whose env holds RELEASE_START, could not be"
    " read, so `{command}` waits until it can: {error}. Run it again once Buildkite answers"
)
RELEASE_CANCEL = (
    "Refused on purpose: a drive job never cancels a release, one it started included, however wide its plan reads, by his"
    ' words "next time fucking do the right thing and then dont cancel it". Leave it running, or held at approve, and return'
    " with the release held for the coordinator, naming it and what you would decline"
)


@dataclass(frozen=True)
class Shell:
    cwd: str
    env: dict[str, str]
    session: str
    driving: bool
    job: bool
    caller: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Landing:
    path: tuple[str, ...]
    valued: frozenset[str]
    shorts: dict[str, str] = field(default_factory=dict)
    every_pr: bool = False
    repo: str | None = None
    labels: frozenset[str] = frozenset()
    reads: tuple[str, ...] = ()
    endpoint: bool = False


LANDINGS = (
    Landing(
        ("gh", "pr", "merge"),
        frozenset({"repo", "author-email", "body", "body-file", "match-head-commit", "subject"}),
        {"R": "repo", "A": "author-email", "b": "body", "F": "body-file", "t": "subject"},
        reads=("disable-auto",),
    ),
    Landing(
        ("gh", "pr", "edit"),
        frozenset({
            "repo", "add-assignee", "add-label", "add-project", "add-reviewer", "attach", "base", "body", "body-file",
            "milestone", "remove-assignee", "remove-label", "remove-project", "remove-reviewer", "title",
        }),
        {"R": "repo", "B": "base", "b": "body", "F": "body-file", "m": "milestone", "t": "title"},
        labels=frozenset({"merge", "merge-fast"}),
    ),
    Landing(("gh", "stack", "merge"), frozenset({"repo", "merge-method"}), {"R": "repo"}),
    Landing(
        ("gh", "api"),
        frozenset({"method", "field", "raw-field", "header", "input", "jq", "template", "hostname", "cache", "preview"}),
        {"X": "method", "F": "field", "f": "raw-field", "H": "header", "q": "jq", "t": "template", "p": "preview"},
        endpoint=True,
    ),
    Landing(("ccx", "vcs", "merge"), frozenset({"repo", "match-head-commit", "red-on-main"}), {"R": "repo"}),
    Landing(("ci", "trunk", "submit"), frozenset({"priority"}), every_pr=True, repo=state.MONOREPO),
    Landing(("stack-enqueue",), frozenset(), every_pr=True, repo=state.MONOREPO, reads=("check", "status", "dequeue")),
)
BODY_POSTS = (
    Landing(
        ("gh", "pr", "create"),
        frozenset({
            "repo", "assignee", "base", "body", "body-file", "head", "label", "milestone", "project", "recover", "reviewer",
            "template", "title",
        }),
        {
            "R": "repo", "a": "assignee", "B": "base", "b": "body", "F": "body-file", "H": "head", "l": "label",
            "m": "milestone", "p": "project", "r": "reviewer", "T": "template", "t": "title",
        },
    ),
    LANDINGS[1],
    Landing(
        ("ccx", "vcs", "ship"),
        frozenset({"m", "branch", "bookmark", "budget", "only-hunk", "parent", "pr-body-file", "pr-title", "skip-hunk"}),
    ),
)

CCI_POST = Landing(
    ("cci", "post"),
    frozenset({
        "attr", "batch", "board", "body-file", "build", "ccn", "census", "count", "drive", "env", "head", "id", "kind", "lane",
        "lane-ref", "mode", "outcome", "owned-by", "path", "pr", "quoted", "re", "resolves", "stack", "successor", "target",
        "text", "to", "topic", "ttl", "url",
    }),
    {"h": "help"},
    reads=("help",),
)
RELEASE_BY_HAND = (
    "Refused on purpose: a drive starts a release only through `drive job add builder --order <o> --goal \"<what it ships>\""
    ' --release "{command}"`, run by the top coordinator, which reads the answer of his its --order names, starts it from the'
    " top's checkout, prints its build and briefs the job's agent to follow it and read its approve hold. A job needing a release"
    " returns WAITING release naming that exact command once its `tools/ci release plan` reads right, and the top asks him for his go"
)
MISROUTED = (
    "This {kind} record goes to {lanes}: a defect, and the communicator's owner record about the drive, to the meta"
    " session, the communicator's answer, handoff or refused record naming no job to the coordinator that wrote"
    " the record it answers, its other owner, answer, handoff or refused record, and any other session's handoff,"
    " to the session holding the order it serves; post it again with `{flags}`, or on the cci `post` tool, whose hook addresses it"
)


def run(args: list[str], cwd: str) -> str:
    return github.run(args, cwd=cwd, timeout=20).strip()


def moved(cwd: str, target: str) -> str:
    return str(Path(cwd, Path(target).expanduser()))


def assigned(shell: Shell, words: list[str]) -> Shell:
    pairs = (word.split("=", 1) for word in words if ENV_ASSIGNMENT.fullmatch(word))
    return replace(shell, env=shell.env | dict(pairs))


def past_options(words: list[str], valued: frozenset[str], shell: Shell) -> tuple[list[str], Shell]:
    rest = iter(words)
    for word in rest:
        if word == "--":
            return list(rest), shell
        if not word.startswith("-"):
            return [word, *rest], shell
        option, given, value = word.partition("=")
        if not given and not word.startswith("--") and len(word) > 2 and word[:2] in valued:
            option, value = word[:2], word[2:]
        elif not given and option in valued:
            value = next(rest, "")
        if option in CHDIR:
            shell = replace(shell, cwd=moved(shell.cwd, value))
    return [], shell


def command_words(words: list[str], shell: Shell) -> tuple[list[str], Shell]:
    """The words of the command a segment runs, past assignments, keywords and wrappers, with the shell it runs in."""
    while words:
        if ENV_ASSIGNMENT.fullmatch(words[0]):
            shell = assigned(shell, words[:1])
            words = words[1:]
        elif words[0] in KEYWORDS:
            words = words[1:]
        elif (wrapper := Path(words[0]).name) in WRAPPERS:
            words, shell = past_options(words[1:], WRAPPERS[wrapper], shell)
            words = words[1:] if wrapper == "timeout" else words
        elif Path(words[0]).name == "drive" and words[1:2] == ["break-glass"] and "--" in words:
            words = words[words.index("--") + 1 :]
        else:
            break
    return words, shell


def shell_script(words: list[str]) -> str | None:
    if Path(words[0]).name not in SHELLS:
        return None
    flagged = (index for index, word in enumerate(words[1:-1], 1) if SHELL_SCRIPT_FLAG.fullmatch(word))
    index = next(flagged, None)
    return None if index is None else words[index + 1]


def parse(args: list[str], landing: Landing) -> tuple[list[str], dict[str, list[str]]]:
    """Positional words and option values of args, read the way gh's flag parser reads them."""
    positionals: list[str] = []
    options: dict[str, list[str]] = {}
    rest = iter(args)
    for word in rest:
        if word == "--":
            positionals.extend(rest)
        elif word.startswith("--"):
            name, given, value = word[2:].partition("=")
            if not given:
                value = next(rest, "") if name in landing.valued else "true"
            options.setdefault(name, []).append(value)
        elif word.startswith("-") and len(word) > 1:
            short_options(word[1:], rest, landing, options)
        else:
            positionals.append(word)
    return positionals, options


def short_options(cluster: str, rest: Iterator[str], landing: Landing, options: dict[str, list[str]]) -> None:
    for index, letter in enumerate(cluster):
        name = landing.shorts.get(letter, letter)
        tail = cluster[index + 1 :]
        if name in landing.valued:
            options.setdefault(name, []).append(tail.removeprefix("=") or next(rest, ""))
            return
        if tail.startswith("="):
            options.setdefault(name, []).append(tail[1:])
            return
        options.setdefault(name, []).append("true")


def switched_on(options: dict[str, list[str]], name: str) -> bool:
    return name in options and options[name][-1].lower() not in FALSE_WORDS


def landing_of(words: list[str], landings: tuple[Landing, ...] = LANDINGS) -> tuple[Landing, list[str], dict[str, list[str]]] | None:
    for landing in landings:
        if Path(words[0]).name != landing.path[0]:
            continue
        positionals, options = parse(words[1:], landing)
        subcommand = list(landing.path[1:])
        if positionals[: len(subcommand)] == subcommand:
            return landing, positionals[len(subcommand) :], options
    return None


def lands(landing: Landing, options: dict[str, list[str]]) -> bool:
    if any(switched_on(options, name) for name in landing.reads):
        return False
    if landing.labels:
        labels = ",".join(options.get("add-label", [])).split(",")
        return bool(landing.labels & {label.strip() for label in labels})
    return True


def selected_repo(landing: Landing, options: dict[str, list[str]], shell: Shell) -> str | None:
    """The repo -R/--repo names, or for gh the one GH_REPO names."""
    if options.get("repo"):
        return repo_name(options["repo"][-1])
    if landing.path[0] == "gh" and shell.env.get(REPO_VARIABLE):
        return repo_name(shell.env[REPO_VARIABLE])
    return None


def target_repo(landing: Landing, first: str | None, options: dict[str, list[str]], shell: Shell) -> str:
    if landing.repo:
        return landing.repo
    if first and state.PR_URL.search(first):
        return state.pr_key(first).split("#")[0]
    selected = selected_repo(landing, options, shell)
    return selected or repo_name(run(["git", "remote", "get-url", "origin"], shell.cwd))


def pr_view(ref: str | None, repo: str | None, cwd: str) -> str:
    selected = [*([ref] if ref else []), *(["-R", repo] if repo else [])]
    return state.pr_key(run(["gh", "pr", "view", *selected, "--json", "url", "-q", ".url"], cwd))


def resolve_key(ref: str | None, repo: str, selected: str | None, cwd: str) -> str:
    if ref is None:
        branch = run(["git", "branch", "--show-current"], cwd) if selected else None
        return pr_view(branch, selected, cwd)
    if state.PR_URL.search(ref):
        return state.pr_key(ref)
    if ref.removeprefix("#").isdigit():
        return f"{repo}#{ref.removeprefix('#')}"
    return pr_view(ref, repo, cwd)


def judged_by(key: str, session: str) -> tuple[str, dict] | None:
    """The drive and its entry for a PR: this session's own drive's when it tracks the PR, else the entry updated last, since a drive copied from another keeps the older copy of each entry."""
    own = state.session_drives(session)
    known = [
        (name in own, state.parse_time(pr["updated"]), name, pr)
        for name in state.names()
        for stored, pr in state.load(name)["prs"].items()
        if stored.lower() == key.lower()
    ]
    return max(known, key=lambda entry: entry[:2])[2:] if known else None


def foreign_reason(key: str, shell: Shell) -> str | None:
    """Why a drive session may not land a PR: someone other than him opened it, and no order of his the drive tracks it for names it, read from GitHub since the drive drops such a PR."""
    if (read := card.read(key)) is None:
        raise github.GithubError(f"could not read who opened {key}")
    judged = judged_by(key, shell.session)
    why = state.foreign(key, read["author"]["login"], state.served(state.load(judged[0]), judged[1]) if judged else [])
    return why and f"Refused on purpose: {why}. Land, enqueue, label and rebase nothing of it"


def pr_reason(landing: Landing, key: str, shell: Shell) -> str | None:
    if (judged := judged_by(key, shell.session)) is None:
        return None
    name, pr = judged
    if shell.driving and (cards := land.pr_cards(state.load(name), pr, view.live_cards(name))):
        return CARD_REASON.format(key=key, cards=", ".join(cards))
    may_land, why = tap_verdict(pr)
    if not may_land:
        return f"{key}: {why}. {TAP_HINT}"
    if shell.driving and not landing.repo and state.queued_by_lander(key) and pr.get("exempt") != INCIDENT_FIX:
        return QUEUE_REASON.format(key=key)
    return None


def stacked(keys: list[str], session: str) -> list[str]:
    """The named PRs with every open PR his Approve lands with each, by land.landed_with as land.unit reads it, each bottom first, never a PR above on a card of its own."""
    members: dict[str, None] = {}
    for key in keys:
        if judged := judged_by(key, session):
            data = state.load(judged[0])
            members |= dict.fromkeys(land.landed_with(data, land.unders(data), land.landing_stacks(data), state.pr_key(judged[1]["url"])))
    shown = {member.lower() for member in members}
    return [*members, *(key for key in keys if key.lower() not in shown)]


def merged_through_api(refs: list[str], options: dict[str, list[str]]) -> list[str] | None:
    """The PR a `gh api` call merges, as its URL, when it PUTs GitHub's merge or merge-async endpoint of a pull request."""
    merged = MERGE_ENDPOINT.fullmatch(next(iter(refs), ""))
    if not merged or options.get("method", ["GET"])[-1].upper() != "PUT":
        return None
    return [f"https://github.com/{merged[1]}/pull/{merged[2]}"]


def landing_reason(landing: Landing, refs: list[str], options: dict[str, list[str]], shell: Shell) -> str | None:
    if landing.endpoint:
        if (merged := merged_through_api(refs, options)) is None:
            return None
        refs, options = merged, {}
    if not lands(landing, options):
        return None
    named = refs if landing.every_pr else refs[:1] or [None]
    repo = target_repo(landing, next(iter(named), None), options, shell)
    if not state.guarded(repo):
        return None
    if switched_on(options, "admin"):
        return ADMIN_REASON
    selected = selected_repo(landing, options, shell)
    keys = [resolve_key(ref, repo, selected, shell.cwd) for ref in named]
    if os.environ.get(state.REMOTE_LANE_ENV):
        return lane_reason(keys)
    if shell.driving and (foreign := next(filter(None, (foreign_reason(key, shell) for key in keys)), None)):
        return foreign
    if landing.every_pr and shell.driving:
        every = stacked(keys, shell.session)
        listed = {key.lower() for key in keys}
        if left := [member for member in every if member.lower() not in listed]:
            numbers = " ".join(member.split("#")[1] for member in every)
            return STACK_REASON.format(named=", ".join(keys), left=", ".join(left), every=numbers)
    return next(filter(None, (pr_reason(landing, key, shell) for key in keys)), None)


def lane_reason(keys: list[str]) -> str | None:
    """Why a Sprite lane may not land these PRs, which no drive here can judge: `drive job held --pr`, which the lane's `drive` asks of the coordinator's Mac, names his open cards holding one, as pr_reason() reads them there, or the Mac did not answer."""
    try:
        held = land.lane_held(*(f"--pr={key}" for key in keys))["prs"]
    except state.DriveError as error:
        return UNASKED.format(keys=", ".join(keys), error=error)
    return next((CARD_REASON.format(key=key, cards=", ".join(cards)) for key, cards in held.items() if cards), None)


def cut_reason(words: list[str], shell: Shell) -> str | None:
    """Why a drive session, an agent it started or a Sprite lane may not run this release verb: a drive job cancels no release, so it returns with one held for the coordinator; a sub-coordinator's session may not start, resume, approve, rerun or roll back a production cut, since one cut carries every project's changes and the top coordinator alone runs them, one at a time; none of them starts or hotfixes a release by hand, since `drive job add --release` starts each one on his answer approving it; and while he holds every release, each other verb runs only on his exception for the release it runs, which a Sprite lane asks the coordinator's Mac with `drive job held --release`, and a codex job's run, whose environment names its job, reads from the Release line of the brief the drive kept for that job."""
    try:
        verb = release_verb(words)
    except github.GithubError as error:
        return CUT_UNREAD.format(command=" ".join(words), error=error)
    if verb is None:
        return None
    lane = os.environ.get(state.REMOTE_LANE_ENV)
    codex = None if lane else os.environ.get(state.OWN_JOB_ENV)
    job = shell.job or bool(lane or codex)
    if verb == "cancel" and job:
        return RELEASE_CANCEL
    if verb not in RELEASE_CUTS or PLAN_ONLY & set(words):
        return None
    names = state.session_drives(shell.session)
    for name in names:
        data = state.load(name)
        if project := state.session_project(data, shell.session):
            top = data["roles"].get("coordinator", {}).get("session") or "not started"
            return (
                f"ci {' '.join(words[1:3])}: the sub-coordinator for {project} starts, approves and rolls back no production cut;"
                f" the top coordinator of drive {name} (session {top}) does, one at a time. Send it a record at root@{name} naming"
                " the merged changes the cut must carry"
            )
    if verb in brief.RELEASE_STARTS and (shell.driving or job):
        return RELEASE_BY_HAND.format(command=" ".join(words))
    if lane:
        return lane_release_reason(release_of(words, agents.brief(shell.caller)))
    if not (drives := [os.environ[state.DRIVE_ENV]] if codex else names):
        return None
    data = state.load(drives[0])
    try:
        hold = settled.hold(data["roles"]["coordinator"]["cwd"])
        return hold and brief.hold_refusal(release_of(words, Path(data["jobs"][codex]["brief"]).read_text() if codex else agents.brief(shell.caller)), data, hold)
    except state.DriveError as error:
        return HOLD_UNREAD.format(command=" ".join(words), error=error)


def release_of(words: list[str], briefed: str) -> str:
    """The release a release verb runs, as his exception names it: the newest Release line of the caller's brief, the release its job started on, else the verb's own words."""
    return next(reversed(brief.RELEASE.findall(briefed)), " ".join(words))


def release_verb(words: list[str]) -> str | None:
    """The release verb a command runs: a `ci release` verb, a `ci bk rerun` or `cancel` of a forge/release build that is a release cut, or `api` for a `ci bk api` call creating a forge/release build or writing to a cut, such as unblocking its approval step; a build is a cut only when its env holds RELEASE_START, the release tool's own test, so the gate of a cluster job or any other build go/ci starts on that pipeline is no release verb."""
    if Path(words[0]).name not in brief.CI_BINARIES:
        return None
    if words[1:2] == ["release"]:
        return next(iter(words[2:3]), None)
    if words[1:2] == ["bk"] and words[2:3] in (["rerun"], ["cancel"]) and words[3:4] == [RELEASE_PIPELINE]:
        return words[2] if release_cut(next(iter(words[4:5]), None)) else None
    if words[1:3] != ["bk", "api"] or not (named := next(filter(None, map(RELEASE_BUILDS.search, words[3:])), None)):
        return None
    method, rest = "GET", iter(words[3:])
    for word in rest:
        flag, given, value = word.partition("=")
        if flag in ("-X", "--X"):
            method = value if given else next(rest, "")
    return "api" if method.upper() != "GET" and release_cut(named[1]) else None


def release_cut(number: str | None) -> bool:
    """Whether the forge/release build a release verb names is a release cut: its env holds RELEASE_START; a write naming no build, such as one creating a build, counts as one."""
    return not (number and number.isdigit()) or bool(github.build_env(RELEASE_BUILDKITE, number).get(RELEASE_START_ENV))


def lane_release_reason(release: str) -> str | None:
    """Why a Sprite lane, which holds no drive, may not run a release verb: `drive job held --release` on the coordinator's Mac refuses the release it runs under his hold, as hold_refusal() reads it there, or the Mac did not answer."""
    try:
        return land.lane_held(f"--release={release}")["release"]
    except state.DriveError as error:
        return RELEASE_UNASKED.format(release=release, error=error)


def commands(script: str, shell: Shell) -> Iterator[tuple[list[str], list[str], Shell]]:
    """Each command a script runs, as its segment, its own words and the shell it runs in, following cd, assignments, export, each value of a for loop and nested shell scripts."""
    yield from run_through(templates(runnable(script)), shell)


def run_through(groups: list[list[str]], shell: Shell) -> Generator[tuple[list[str], list[str], Shell], None, Shell]:
    """The commands of groups in order, each word expanded with the variables set before it, then the shell they leave."""
    index = 0
    while index < len(groups):
        segment = [expanded(word, shell.env) for word in groups[index]]
        words, run_in = command_words(segment, shell)
        index += 1
        if not words:
            shell = run_in
        elif words[0] == "for":
            yield segment, words, run_in
            end = loop_end(groups, index)
            for binding in [{words[1]: value} for value in words[3:]] if words[2:3] == ["in"] else [{}]:
                shell = yield from run_through(groups[index:end], replace(shell, env=shell.env | binding))
            index = end + 1
        elif words[0] == "cd":
            shell = replace(shell, cwd=moved(shell.cwd, words[1]) if len(words) > 1 else str(Path.home()))
        elif words[0] == "export":
            shell = assigned(shell, words[1:])
        elif (inner := shell_script(words)) is not None:
            yield from commands(inner, run_in)
        else:
            yield segment, words, run_in
    return shell


def loop_end(groups: list[list[str]], start: int) -> int:
    """The index of the `done` closing the loop whose body starts at start, or past the last group when none does."""
    depth = 1
    for index in range(start, len(groups)):
        head = next((word for word in groups[index] if word not in LEADING), "")
        depth += (head in LOOPS) - (head == "done")
        if not depth:
            return index
    return len(groups)


def script_reason(script: str, shell: Shell) -> str | None:
    reasons = (landing_reason(*found, run_in) if (found := landing_of(words)) else cut_reason(words, run_in) for _, words, run_in in commands(script, shell))
    return next(filter(None, reasons), None)


def own_work(script: str, shell: Shell) -> str | None:
    """The first step of a script that is an order's work, not a coordinator's own call: a build, a ship, a commit or a push."""
    for _, words, _ in commands(script, shell):
        called = (Path(words[0]).name, *words[1:])
        if any(called[: len(work)] == work for work in COORDINATOR_WORK):
            return " ".join(called[:3])
    return None


def exposed_reason(landing: Landing, refs: list[str], options: dict[str, list[str]], shell: Shell) -> str | None:
    files = [file for file in (*options.get("body-file", []), *(value.partition("=")[2] or value for value in options.get("pr-body-file", []))) if file != "-"]
    if not files and not options.get("body"):
        return None
    repo = target_repo(landing, next(iter(refs), None), options, shell)
    try:
        if github.visibility(repo) != "public":
            return None
        if unwritten := next((file for file in files if not Path(moved(shell.cwd, file)).exists()), None):
            return UNWRITTEN.format(file=unwritten, repo=repo)
        texts = [*options.get("body", []), *(Path(moved(shell.cwd, file)).read_text() for file in files)]
        exposed = public_check.body(repo, "\n".join(texts))
    except (github.GithubError, slack.SlackError) as error:
        return UNCHECKED.format(error=error)
    return EXPOSED.format(exposed=exposed) if exposed else None


def body_reason(script: str, shell: Shell) -> str | None:
    posted = (exposed_reason(*found, run_in) for _, words, run_in in commands(script, shell) if (found := landing_of(words, BODY_POSTS)))
    return next(filter(None, posted), None)


def cci_posts(script: str, shell: Shell) -> list[dict]:
    """Each record a script posts with `cci post`, never a call asking for its help with `--help` or `-h`, which cci answers before it reads a record, in the shape the cci `post` tool takes: its kind, attrs, addressed lanes, text, body, url, topic and answered seqs, each read as cci reads it: its text the positionals, else --text, else the opening line of the file --path names, its body the file --body-file or --path names, and each `--attr` as cci's flag parser reads it, one `key=value` whole, commas and all, and with several `=` comma-separated `key=value` fields, quotes around one holding a comma."""
    posts = []
    for _, words, run_in in commands(script, shell):
        if (found := landing_of(words, (CCI_POST,))) and lands(found[0], found[2]):
            _, positionals, options = found
            path = (options.get("path") or [None])[-1]
            posts.append({
                "kind": (options.get("kind") or [""])[-1].lower(),
                "attrs": dict(pair.split("=", 1) for value in options.get("attr", []) for pair in ([value.strip('"')] if value.count("=") == 1 else next(csv.reader([value])))),
                "to": [lane for value in options.get("to", []) for lane in value.split(",")],
                "text": " ".join(positionals) or (options.get("text") or [""])[-1] or limits.opening_line(file_text(run_in.cwd, path)),
                "body": file_text(run_in.cwd, (options.get("body-file") or [path])[-1]),
                "url": (options.get("url") or [None])[-1],
                "topic": (options.get("topic") or [None])[-1],
                "re": [seq for value in options.get("re", []) for seq in value.split(",")],
            })
    return posts


def cci_connected(payload: dict) -> bool:
    """Whether the session's cci MCP server is connected, as the newest change its transcript records leaves it: an instructions or deferred-tools delta adding or removing the server or its tools, or naming it failed. The agent's own transcript answers when it records a change, else its session's; one recording none reads as gone, so `cci post` stays open."""
    paths = [agents.transcript(payload)] if payload.get("agent_id") else []
    for path in [*paths, Path(payload["transcript_path"])]:
        connected = None
        with suppress(FileNotFoundError), path.open("rb") as rows:
            for row in rows:
                if b"cc-inbox" in row and b"_delta" in row:
                    connected = cci_change(json.loads(row).get("attachment") or {}, connected)
        if connected is not None:
            return connected
    return False


def cci_change(attachment: dict, connected: bool | None) -> bool | None:
    """The cci server's state after one transcript attachment: connected when it adds the server or its tools, gone when it removes them or names the server failed, else as it was."""
    ours = {
        field
        for field in ("addedNames", "removedNames", "failedMcpServers")
        for entry in attachment.get(field) or []
        if (name := entry if isinstance(entry, str) else entry.get("name", "")) == CCI_SERVER or name.startswith(CCI_TOOLS)
    }
    if "addedNames" in ours:
        return True
    return False if ours else connected


def file_text(cwd: str, named: str | None) -> str:
    """The text of the file a `cci post` flag names, from the folder the post runs in; none when it names no file there, such as one an earlier command in the call writes."""
    if named is None or not (path := Path(moved(cwd, named))).is_file():
        return ""
    return path.read_text()


def misrouted(posts: list[dict], drives: dict[str, dict], session: str) -> str | None:
    """Why a drive session's `cci post` names lanes other than the drive routes its record to, which the cci `post` tool's hook readdresses: a defect, or a communicator's owner record about the drive, not to the meta session, a communicator's record naming no job not to the coordinator that wrote the record it answers, or another communicator's record, or any other session's handoff, not to the session holding the order it serves."""
    for post in posts:
        if (routed := state.routed(drives, session, post["kind"], post["attrs"], post["to"], post["re"])) != list(dict.fromkeys(post["to"])):
            return MISROUTED.format(kind=post["kind"], lanes=", ".join(routed), flags=" ".join(f"--to {lane}" for lane in routed))
    return None



def deny_reason(payload: dict) -> str | None:
    """Why a Bash call must not run: a `cci post` the cci `post` tool sends, else a judged reason, else a guarded one."""
    return cli_posted(payload) or judged_reason(payload) or guarded_reason(payload)


def cli_posted(payload: dict) -> str | None:
    """Why a drive session's or agent's `cci post` in Bash must not run whatever its words say, a refusal capt-hook's Jev never weighs again: it posts the communicator's owner record about the drive, which only the drive's `record` tool sends, adding its order first, or its cci MCP server is connected, so the cci `post` and `close` tools send the record and their hook checks its words, and no refusal of its words comes first to be fixed in a command this one then refuses."""
    names, shell = shell_of(payload)
    posts = cci_posts(payload["tool_input"]["command"], shell) if shell.driving else []
    role = state.session_role(state.saved(names[0]), shell.session) if names and posts else None
    if unordered := next(filter(None, (state.unrecorded(role, post["kind"], post["attrs"]) for post in posts)), None):
        return unordered
    return CLI_POST if posts and cci_connected(payload) else None


def shell_of(payload: dict) -> tuple[list[str], Shell]:
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    job = (payload.get("agent_type") or "").startswith(AGENTS) or bool(names and payload.get("agent_id"))
    return names, Shell(payload.get("cwd") or os.getcwd(), dict(os.environ), session, bool(names) or job, job, caller=payload)


def judged_reason(payload: dict) -> str | None:
    """Why a drive session's Bash call looks wrong in a way Jev judges before it stands: it posts a drive's ask record, which no session posts since `drive job ask` draws his card itself, a report carrying a part in an attr other than part, which no post reads, or naming an attr a separator, a capital or one letter from one the drive reads, a record to lanes other than the drive routes it to, a report to the communicator that the checks the cci `post` tool's hook runs refuse, such as one leaving out a part a finished job answered, a record a handler posts to him typing a clock time for work already done, or a record asking a session for a step of a production release that brief.release_ask_refusal() refuses, which blocks outright, or is the coordinator's or the meta session's own order work."""
    names, shell = shell_of(payload)
    session = shell.session
    command = payload["tool_input"]["command"]
    posts = cci_posts(command, shell)
    if names and any(post["kind"] == "ask" for post in posts):
        return settled.HAND_ASK
    if names and (misnamed := next(filter(None, (reports.stray_parts(post["kind"], post["attrs"]) or limits.misnamed(post["kind"], post["attrs"]) for post in posts)), None)):
        return misnamed
    if posts and (wrong := misrouted(posts, state.bound_drives(names), session)):
        return wrong
    drive = state.load(names[0]) if names and posts else None
    if drive and (unreported := next(filter(None, (refused for index in range(len(posts)) for refused in reports.report_refusals(payload, drive, posts, index))), None)):
        return unreported
    if drive and (typed := next(filter(None, (reports.relayed_time(post, drive) for post in posts)), None)):
        return typed
    if drive and (asked := next(filter(None, (brief.release_ask_refusal(post["kind"], post["to"], f"{post['text']}\n{post['body']}", drive) for post in posts)), None)):
        return agents.Unjudged(asked)
    roles = [] if payload.get("agent_id") else [state.session_role(state.saved(name), session) for name in names]
    who = next((role for role in roles if role in ("coordinator", "meta")), None)
    if who and (step := own_work(command, shell)):
        return COORDINATOR_STEP.format(who=state.title(who), step=step)
    return None


def unprevented_reports(payload: dict) -> str | None:
    """What a drive session's `cci post` in Bash hears beside its call, as the cci `post` tool's hook tells it: each POST report to the communicator telling him a failure of the drive's with no word on how it is prevented, which goes out as written."""
    names, shell = shell_of(payload)
    posts = cci_posts(payload["tool_input"]["command"], shell) if names else []
    drive = state.load(names[0]) if posts else None
    return "\n".join(filter(None, (reports.unprevented(drive, post) for post in posts))) or None


def guarded_reason(payload: dict) -> str | None:
    """Why a Bash call must not run, a refusal capt-hook's Jev never weighs again: it lands a Forge-AI drive PR without his tap or merges with --admin there, in a drive session or agent lands a Forge-AI PR someone else opened that no order of his names, starts, approves or rolls back a production cut from a sub-coordinator's session, cancels a release from a drive job or starts one in a drive session or agent rather than through `drive job add --release`, and in a drive session or agent lands a Forge-AI PR while his card on it is open, around the monorepo's queue or apart from its stack, or on a Sprite lane lands one while `drive job held --pr` on the coordinator's Mac names his card on it open or does not answer, puts up a drive's public PR body showing what only a private place may, or posts with `cci post` an owner or answer record of his words on an open card of his, which only the drive's `record` tool closes in the same call, or an owner record naming no about, which picks the session it reaches."""
    names, shell = shell_of(payload)
    command = payload["tool_input"]["command"]
    if shell.driving and (exposed := body_reason(command, shell)):
        return exposed
    posts = cci_posts(command, shell)
    unclosed = (superseded.unclosed_refusal(names, post["kind"], post["attrs"], post["topic"], post["re"], post["url"]) for post in posts)
    if names and (left_up := next(filter(None, unclosed), None)):
        return left_up
    if names and (unsaid := next(filter(None, (state.unabout(post["kind"], post["attrs"]) for post in posts)), None)):
        return unsaid
    return script_reason(command, shell)


def denial(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> None:
    try:
        payload = agents.seen(json.load(sys.stdin))
        unjudged = cli_posted(payload)
        judged = None if unjudged else judged_reason(payload)
        reason = unjudged or judged or guarded_reason(payload)
        context = None if reason else unprevented_reports(payload)
    except (ValueError, KeyError):
        return
    except github.GithubError as error:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": UNREAD.format(error=error)}}))
        return
    if reason:
        print(json.dumps(denial(reason) if os.environ.get(state.OWN_JOB_ENV) else denial(reason) | {"judged": bool(judged)} | agents.ruled(reason)))
        return
    agents.hold(payload)
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}))


if __name__ == "__main__":
    main()
