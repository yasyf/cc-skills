import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, github, public_check, settled, slack, state, view
from drive.cli import tap_verdict
from drive.github import repo_name
from drive.shell import runnable

GUARDED_OWNER = "forge-ai"
MONOREPO = "Forge-AI/monorepo"
WAITS_FOR_CI = frozenset({"yasyf/cc-skills"})
PASSED = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
ADMIN_REASON = "Admin merges bypass required review; never on Forge-AI repos"
TAP_HINT = "Draw his PR card with slack_dm_status tap_prs, or record an exemption with drive pr set --exempt"
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
SHELL_SCRIPT_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
KEYWORDS = {"!", "{", "if", "then", "elif", "else", "do", "while", "until"}
WRAPPERS = {
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "time": frozenset(),
    "nice": frozenset({"-n"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
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
STACK_REASON = (
    "Refused on purpose: {named} leaves out {left} of the stack his card drew, and a stack enqueues together, never"
    " part alone. Name every open PR of it: `stack-enqueue {every}`"
)
COORDINATOR_STEP = (
    "Refused on purpose: the coordinator does no order's work itself, so its turn never blocks, and `{step}` is such"
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
AGENTS = "long-running:"
LOOPS = {"for", "while", "until"}
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
    ("orca",),
)
TRANSCRIPT_READ = ("orca", "orchestration", "worker-read")
UNKEPT_ASK = (
    "An ask record names the ASK `drive job ask` kept (--attr ask=<the path it printed>), and the communicator draws"
    " its card from that file; keep the question with `drive job ask <j> <file>`, then post the record with that path"
)


@dataclass(frozen=True)
class Shell:
    cwd: str
    env: dict[str, str]
    session: str
    driving: bool


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
    Landing(("ci", "trunk", "submit"), frozenset({"priority"}), every_pr=True, repo=MONOREPO),
    Landing(("stack-enqueue",), frozenset(), every_pr=True, repo=MONOREPO, reads=("check", "status", "dequeue")),
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


def run(args: list[str], cwd: str) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, timeout=20).stdout.strip()


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(runnable(command).replace("\n", ";"), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    groups: list[list[str]] = [[]]
    for token in lexer:
        if set(token) <= set(lexer.punctuation_chars):
            groups.append([])
        else:
            groups[-1].append(token)
    return [group for group in groups if group]


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


def open_cards(name: str, pr: dict) -> list[str]:
    """His open cards a drive PR or its job waits on, outside an open incident, whose repair lands on the recommended option."""
    data = state.load(name)
    job = data["jobs"][pr["job"]]
    if state.open_incidents(data, [job["order"], *job["carries"]]):
        return []
    waits = list(dict.fromkeys([*pr["waits"], *job["waits"]]))
    return [wait for wait in view.holding(data, waits, view.live_cards(name)) if wait not in data["jobs"] and not state.RELEASE_WAIT.fullmatch(wait)]


def pr_reason(landing: Landing, key: str, shell: Shell) -> str | None:
    if (judged := judged_by(key, shell.session)) is None:
        return None
    name, pr = judged
    if shell.driving and (cards := open_cards(name, pr)):
        return CARD_REASON.format(key=key, cards=", ".join(cards))
    may_land, why = tap_verdict(pr)
    if not may_land:
        return f"{key}: {why}. {TAP_HINT}"
    if shell.driving and not landing.repo and key.split("#")[0].lower() == MONOREPO.lower() and pr.get("exempt") != INCIDENT_FIX:
        return QUEUE_REASON.format(key=key)
    return None


def stacked(keys: list[str], session: str) -> list[str]:
    """The named PRs with every open PR of each stack a named PR's card drew, each stack bottom first."""
    named = {key.lower() for key in keys}
    stacks = [judged[1].get("stack") or [] for key in keys if (judged := judged_by(key, session))]
    members = [
        member
        for member in dict.fromkeys(member for stack in stacks for member in stack)
        if member.lower() in named or ((judged := judged_by(member, session)) and judged[1]["state"] == "open")
    ]
    shown = {member.lower() for member in members}
    return [*members, *(key for key in keys if key.lower() not in shown)]


def checks_reason(key: str, cwd: str) -> str | None:
    """Why a PR in a repository whose CI runs in about a minute must not merge yet: a check on its head still runs or is red."""
    repo, number = key.split("#")
    rollup = json.loads(run(["gh", "pr", "view", number, "-R", repo, "--json", "statusCheckRollup"], cwd))["statusCheckRollup"]
    waiting = [
        check["name"] if check["__typename"] == "CheckRun" else check["context"]
        for check in rollup
        if (check["conclusion"] if check["__typename"] == "CheckRun" else check["state"]) not in PASSED
    ]
    if not waiting:
        return None
    return f"{key}: {', '.join(waiting)} not green on its head yet; its CI takes about a minute, so merge once every check passes, fixing a red one first"


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
    guarded = repo.split("/")[0].lower() == GUARDED_OWNER
    if not guarded and repo.lower() not in WAITS_FOR_CI:
        return None
    if guarded and switched_on(options, "admin"):
        return ADMIN_REASON
    selected = selected_repo(landing, options, shell)
    keys = [resolve_key(ref, repo, selected, shell.cwd) for ref in named]
    if not guarded:
        return next(filter(None, (checks_reason(key, shell.cwd) for key in keys)), None)
    if landing.every_pr and shell.driving:
        every = stacked(keys, shell.session)
        listed = {key.lower() for key in keys}
        if left := [member for member in every if member.lower() not in listed]:
            numbers = " ".join(member.split("#")[1] for member in every)
            return STACK_REASON.format(named=", ".join(keys), left=", ".join(left), every=numbers)
    return next(filter(None, (pr_reason(landing, key, shell) for key in keys)), None)


def commands(script: str, shell: Shell) -> Iterator[tuple[list[str], list[str], Shell]]:
    """Each command a script runs, as its segment, its own words and the shell it runs in, following cd, export and nested shell scripts."""
    for segment in segments(script):
        words, run_in = command_words(segment, shell)
        if not words:
            continue
        if words[0] == "cd":
            shell = replace(shell, cwd=moved(shell.cwd, words[1]) if len(words) > 1 else str(Path.home()))
        elif words[0] == "export":
            shell = assigned(shell, words[1:])
        elif (inner := shell_script(words)) is not None:
            yield from commands(inner, run_in)
        else:
            yield segment, words, run_in


def script_reason(script: str, shell: Shell) -> str | None:
    landed = (landing_reason(*found, run_in) for _, words, run_in in commands(script, shell) if (found := landing_of(words)))
    return next(filter(None, landed), None)


def own_work(script: str, shell: Shell) -> str | None:
    """The first step of a script that is an order's work, not a coordinator's own call: a loop, a build, a ship, commit or push, or an `orca` call other than a transcript read."""
    for segment, words, _ in commands(script, shell):
        if segment[0] in LOOPS:
            return segment[0]
        called = (Path(words[0]).name, *words[1:])
        if called[:3] != TRANSCRIPT_READ and any(called[: len(work)] == work for work in COORDINATOR_WORK):
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


def asks_posted(script: str, shell: Shell) -> list[str | None]:
    """The `ask` attr, None when it has none, of each ask record a script posts with `cci post`."""
    posted = []
    for segment in segments(script):
        words, run_in = command_words(segment, shell)
        if not words:
            continue
        if (inner := shell_script(words)) is not None:
            posted += asks_posted(inner, run_in)
        if Path(words[0]).name != "cci" or words[1:2] != ["post"]:
            continue
        pairs = list(pairwise(words))
        kinds = [value for option, value in pairs if option == "--kind"] + [word.removeprefix("--kind=") for word in words if word.startswith("--kind=")]
        if "ask" in map(str.lower, kinds):
            posted.append(next((value.removeprefix("ask=") for option, value in pairs if option == "--attr" and value.startswith("ask=")), None))
    return posted


def deny_reason(payload: dict) -> str | None:
    """Why a Bash call must not run: it lands a Forge-AI drive PR without his tap or merges with --admin there, merges a cc-skills PR before its CI is green, and in a drive session or agent lands a Forge-AI PR while his card on it is open, around the monorepo's queue or apart from its stack, posts a drive's ask record past the check against his answers, is the coordinator's own order work, or puts up a drive's public PR body showing what only a private place may."""
    env = {REPO_VARIABLE: os.environ[REPO_VARIABLE]} if REPO_VARIABLE in os.environ else {}
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    shell = Shell(payload.get("cwd") or os.getcwd(), env, session, bool(names) or (payload.get("agent_type") or "").startswith(AGENTS))
    command = payload["tool_input"]["command"]
    asks = asks_posted(command, shell)
    if asks and names and not all(settled.kept(names, ask) for ask in asks):
        return UNKEPT_ASK
    coordinates = not payload.get("agent_id") and any(state.session_role(state.load(name), session) == "coordinator" for name in names)
    if coordinates and (step := own_work(command, shell)):
        return COORDINATOR_STEP.format(step=step)
    if shell.driving and (exposed := body_reason(command, shell)):
        return exposed
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
        payload = json.load(sys.stdin)
        reason = deny_reason(payload)
    except (ValueError, KeyError):
        return
    if reason:
        print(json.dumps(denial(reason)))
        return
    agents.hold(payload)


if __name__ == "__main__":
    main()
