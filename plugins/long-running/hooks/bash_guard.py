import json
import os
import re
import sys
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from functools import partial, reduce
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, gate, github, install, land, orca, providers, reads, reports, state
from drive.orca import OrcaError
from drive.shell import KEYWORDS, Case, Function, Loop, expanded, grouped, literal, runnable, simple_commands, spread

AGENTS = "long-running:"
DESIGNER = "long-running:designer"
BUILDER = "long-running:builder"
SHELLS = {"sh", "bash", "zsh", "dash", "fish"}
WRAPPERS = {"command", "exec", "nohup", "env", "time"}
VALUED = {"-C", "-R", "--repo", "--drive"}
CCX_RAW = re.compile(r"#\s*ccx:raw\b|\bCAPT_HOOK_CCX_RAW=1\b")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
SHIP = ("ccx", "vcs", "ship")
PUSHES = (("gh", "pr", "create"), ("git", "push"), ("jj", "git", "push"), SHIP, ("gt", "submit"))
PUSH_ONLY = {"--no-pr", "--dry-run"}
CACHED_PAGE = re.compile(r"plugins/cache/([^/\s'\"]+)/long-running/([^/\s'\"]+)/skills/long-running/reference/")
RERUNS = {
    ("bk", "api"): frozenset({"-X", "--method", "-H", "--headers", "-d", "--data", "-f", "--file"}),
    ("bk", "build", "rebuild"): frozenset({"-p", "--pipeline", "-b", "--branch", "-u", "--user"}),
    ("bk", "job", "retry"): frozenset(),
    ("gh", "run", "rerun"): frozenset({"-R", "--repo", "-j", "--job"}),
}
HELP = frozenset({"-h", "--help", "-help"})
RETRY_PATH = re.compile(r"(?:^|/)(?:organizations/(?P<org>[\w-]+)/)?pipelines/(?P<pipeline>[\w.-]+)/builds/(?P<number>\d+)/(?:jobs/(?P<job>[\w-]+)/retry|rebuild)/?$")
FAILED = frozenset({"failed", "canceled", "timed_out", "expired", *(conclusion.lower() for conclusion in github.FAILED)})
FLAKE = "--flake"
FIX_THE_CAUSE = (
    "a check that fails is fixed at its cause, never retried past with {flake} or a rerun. Read the failed job's own output"
    "{tip} with `gh run view <run> --log-failed` on GitHub Actions, fix the cause and push the fix; a test that flakes gets"
    " a flake fix of its own"
)
RERUN_FAILED = (
    "refused: `{step}` reruns {target}, which reads `{state}`, and the watchdog reruns only a build that failed, by his pick"
    ' "Report, plus two safe acts, rerunning a failed build and messaging the owning job"; put `{command}` in your NEXT line'
    " for the holder instead"
)
RERUN_UNREAD = "bash_guard let `{step}` through unjudged: whether the build, job or run it reruns passed could not be read ({error}), and a gate that cannot read its inputs lets the call go"
BREW_RESTARTS = {"upgrade", "reinstall"}
RESUME_TIMEOUT = 20000
UNSEEN_PROMPT = (
    "refused: Claude Code asked for permission to make this call, and nobody watches a drive agent's terminal, so the"
    " prompt would hold your job unseen for hours. Bypass permissions mode still asks when Claude Code's parser cannot"
    " read a Bash command, such as one with a zsh expansion flag like `${(s:,:)tables}`: write the command to a script in"
    " your brief's Scratch folder, with the Write tool or a quoted heredoc `cat > <scratch>/<name>.sh <<'EOF'` … `EOF`,"
    " and run it as `zsh <scratch>/<name>.sh`. When the command removes files outside your worktree or Scratch folder,"
    " run nothing and return BLOCKED naming it. Any other tool that asks, such as computer use's `request_access`, gets"
    " no answer either: reach what it does another way, or return BLOCKED naming the call"
)
STORAGE_TOOLS = {"aws", "s5cmd", "gsutil", "gcloud", "rclone", "azcopy", "az"}
SERVICES = {"aws": ("s3",), "gcloud": ("storage",), "az": ("storage",)}
S3API_OBJECTS = re.compile(r"object|upload|part")
BUCKET_URI = re.compile(r"(?:s3|gs)://([^\s]*)")
AZURE_URL = re.compile(r"https://([^/.\s]+)\.(?:blob|dfs)\.core\.windows\.net/([^?\s]*)")
RCLONE_PATH = re.compile(r"[\w.-]*:(?!//)([^\s]*)")
OPTIONED = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "exec": {"-a"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-P", "-n", "-I", "-L", "-d", "-s", "-a", "-E", "--max-procs", "--max-args", "--replace", "--max-lines", "--delimiter", "--max-chars", "--arg-file", "--eof"},
}
SOURCES = {"source", "."}
PARSE_ONLY = re.compile(r"-[A-Za-z]*n[A-Za-z]*")
XARGS_INPUT = "$(xargs)"
XARGS_REPLACE = re.compile(r"(?:-I|-i|--replace=)(?P<string>.*)|--replace")
FOLLOWED_DEPTH = 4
FIRST_LINE_LIMIT = 512
SCRIPT_FOLDER = re.compile(r'\$\(cd "\$\(dirname "(?:\$0|\$\{BASH_SOURCE\[0\]\})"\)([^"]*)" && pwd\)')
JSON_FIELD = re.compile(r"\$\(jq -r '?((?:\.\w+)+)'? (\S+)\)")
NAMESPACE_REACHES = {"exec", "port-forward", "attach", "cp", "debug"}
SECRETS = {"secret", "secrets"}
KIND_READS = {"get", "describe", "edit"}
ALL_NAMESPACES = {"-A", "--all-namespaces", "--all-namespaces=true"}
EVERY_NAMESPACE = "in every namespace, those holding a team's data among them,"
KUBECTL_NAMESPACE = re.compile(r"(?:-n=?|--namespace=)(?P<namespace>.+)")
KUBECTL_VALUED = {"-n", "--namespace", "--context", "--cluster", "--user", "--kubeconfig", "-c", "--container", "-o", "--output", "-l", "--selector", "-f", "--filename", "--address", "-s", "--server", "--as", "--raw"}

DATADOG_CLI = "pup"
DATADOG_READ = (
    "refused: a drive session reads Datadog only through the Datadog MCP's tools, whose `execute_code` reaches every "
    "Datadog API but Database Monitoring's, never the `pup` command, whose OAuth grant keeps the scopes its `pup auth "
    "login` was granted while later pup releases add commands needing more, such as profiling's "
    "`continuous_profiler_read`. Load them with ToolSearch "
    "`select:mcp__plugin_datadog_mcp__execute_code,mcp__plugin_datadog_mcp__search_datadog_sdk` and make the same read "
    "through `execute_code`, or in any session that has no Datadog MCP tool, a Sprite lane or a drive job on this Mac, through `drive datadog execute_code -` (or `drive datadog <tool> -`) with its"
    " JSON arguments on stdin. Read Database Monitoring's query samples, plans and query performance, such as "
    "`search_datadog_database_samples`, on this Mac or a Sprite through `drive datadog <tool> -`, since only it asks the "
    "server for Database Monitoring's tools"
)


def commands(text: str) -> list[list[str]]:
    """The words of each command the script runs, past assignments and wrappers, and those of each `sh -c` script inside it."""
    found = []
    for words in simple_commands(text):
        while words and (ASSIGNMENT.fullmatch(words[0]) or Path(words[0]).name in WRAPPERS):
            words = words[1:]
        if not words:
            continue
        found.append(words)
        if Path(words[0]).name in SHELLS and "-c" in words[:-1]:
            found += commands(runnable(words[words.index("-c") + 1]))
    return found


def verb(words: list[str]) -> list[str]:
    named, rest = [Path(words[0]).name], iter(words[1:])
    for word in rest:
        if word in VALUED:
            next(rest, None)
        elif not word.startswith("-"):
            named.append(word)
    return named


def option(words: list[str], name: str) -> str | None:
    given = [value for flag, value in pairwise(words) if flag == name]
    given += [word.removeprefix(f"{name}=") for word in words if word.startswith(f"{name}=")]
    return given[-1] if given else None


def cci_chatter(found: list[list[str]]) -> str | None:
    """Why a `cci post` from a drive session, agent or lane is chatter, as the cci post hook reads the same record, by reports.chatter."""
    posts = [words for words in found if verb(words)[:2] == ["cci", "post"]]
    records = [{"kind": option(words, "--kind"), "to": [value for flag, value in pairwise(words) if flag == "--to"], "text": option(words, "--text")} for words in posts]
    return next(filter(None, map(reports.chatter, records)), None)


def terminal_scripts(found: list[list[str]]) -> list[list[list[str]]]:
    """The commands each `orca terminal create --command` starts its terminal with."""
    return [commands(runnable(option(words, "--command") or "")) for words in found if verb(words)[:3] == ["orca", "terminal", "create"]]


def designer_push(found: list[list[str]]) -> str | None:
    pushed = next((" ".join(push) for words in found for push in PUSHES if tuple(verb(words)[: len(push)]) == push), None)
    if not pushed:
        return None
    return (
        f"refused: the designer is read only, so it runs no `{pushed}`: it opens no PR and pushes nothing. Keep the design"
        " in your report, piped with a quoted heredoc into `drive job report <your job> -`, and the coordinator starts"
        " the builder that ships it"
    )


def bare_pr(found: list[list[str]], command: str) -> str | None:
    """Why a builder's `gh pr create` must not run: it skips the repository's own way of opening a PR; None for one carrying the `# ccx:raw` escape hatch or `CAPT_HOOK_CCX_RAW=1`, the route when ccx itself refuses."""
    if CCX_RAW.search(command) or not any(tuple(verb(words)[:3]) == ("gh", "pr", "create") for words in found):
        return None
    return (
        "refused: a builder opens its PR the repository's way, never a bare `gh pr create`: its own submit wrapper, otherwise"
        " the `open-pr` skill, which ships it with `ccx vcs ship --pr-title-file <title> --pr-body-file <body>` and keeps"
        " the branch, stack and PR text the repository expects; when ccx itself refuses, end the raw call with `# ccx:raw`"
    )


def opens_pr(found: list[list[str]]) -> bool:
    """Whether a call runs a `ccx vcs ship` that may open a PR: one with neither `--no-pr` nor `--dry-run`."""
    return any(tuple(verb(words)[:3]) == SHIP and not PUSH_ONLY & set(words) for words in found)


def exempted(found: list[list[str]]) -> str | None:
    """The exempt class a `drive pr add` in the same call registers the shipped PR under."""
    return next((option(words, "--exempt") for words in found if verb(words)[:3] == ["drive", "pr", "add"]), None)


def held_pr(found: list[list[str]], agent: str, drives: list[str]) -> str | None:
    """Why a builder's `ccx vcs ship` on this Mac may not open its PR: for a main fix, another job already owns every test main fails, by land.claim_main, which claims the rest for this job."""
    if not opens_pr(found) or exempted(found) != state.MAIN_FIX:
        return None
    for name in drives:
        data = state.load(name)
        if not (job_id := state.agent_job(data, agent)):
            continue
        if owned := land.claim_main(name, job_id):
            return f"refused: {owned}"
    return None


def lane_held(found: list[list[str]]) -> str | None:
    """Why a Sprite lane's `ccx vcs ship` of a main fix may not open its PR: `drive job held`, which the Sprite's `drive` asks of the coordinator's Mac, names the job already owning every test main fails; a Mac whose drive predates that claim answers no `main`."""
    if not opens_pr(found) or exempted(found) != state.MAIN_FIX:
        return None
    try:
        answer = land.lane_held(f"--exempt={state.MAIN_FIX}")
    except state.DriveError as error:
        return f"refused: `drive job held` could not say whether another job already owns the tests main fails: {error}; ship again once it answers"
    return f"refused: {owned}" if (owned := answer.get("main")) else None


def cached_page(text: str) -> str | None:
    if not (cached := CACHED_PAGE.search(text)):
        return None
    if (current := install.installed(f"long-running@{cached[1]}")["version"]) == cached[2]:
        return None
    return (
        f"refused: this reads the skill's pages from long-running {cached[2]}'s copy in Claude Code's plugin cache, and"
        f" {current} is installed now; a cached copy goes stale with each install. Read a page's rule with"
        " `drive manual <page> <row>`, which prints the installed page's rows, bullets and sections naming it"
    )


def shell_after_claude(scripts: list[list[list[str]]]) -> str | None:
    for found in scripts:
        names = [Path(words[0]).name for words in found]
        if "claude" in names and (shell := next((name for name in names[names.index("claude") :] if name in SHELLS), None)):
            return (
                f"refused: the terminal's --command runs `{shell}` after claude, and a shell there outlives the session in"
                " the terminal; drop it, so --command ends with the claude command"
            )
    return None


def reran(found: list[list[str]], cwd: str, watchdog: bool = False) -> str | None:
    """Why a call retries past a failing check, or reruns what the watchdog may not: the merge queue's flake requeue queues a PR again past its failed verification; a drive session reruns what it judges a rerun remedies, a release job its tool names the rerun for included, and the watchdog only one that reads failed, by his pick that it reruns a failed build, waiting while that state could not be read."""
    for words in found:
        if any(word == FLAKE or word.startswith(f"{FLAKE}=") for word in words) and (retry := flake_retry(words, cwd)):
            return retry
        if not watchdog:
            continue
        try:
            rerun = rerun_target(words, cwd)
        except (providers.ProviderError, state.DriveError) as error:
            print(RERUN_UNREAD.format(step=" ".join(verb(words)[:3]), error=error), file=sys.stderr)
            continue
        if rerun is None:
            continue
        step, read = rerun
        try:
            target, reads = read()
        except github.GithubError as error:
            print(RERUN_UNREAD.format(step=step, error=error), file=sys.stderr)
            continue
        if reads not in FAILED:
            return RERUN_FAILED.format(step=step, target=target, state=reads, command=" ".join(words))
    return None


def flake_retry(words: list[str], cwd: str) -> str | None:
    """Why a run of the repository's merge-queue script with --flake must not run: it requeues a PR past its failed verification; a refusal naming the error when the providers that name that script fail to load."""
    try:
        made = providers.at(Path(cwd))
    except providers.ProviderError as error:
        print(f"bash_guard let `{FLAKE}` through unjudged: the providers that name this repository's merge queue failed to load, and a gate that cannot read its inputs lets the call go: {error}", file=sys.stderr)
        return None
    queue = made.repo.queue()
    if queue is None or verb(words)[0] != Path(queue.script).name:
        return None
    tip = made.build.log_tip("<job URL>")
    cause = FIX_THE_CAUSE.format(flake=FLAKE, tip=f" with {tip}, or" if tip else "")
    return f"refused: `{verb(words)[0]} {FLAKE}` retries past a failing check, and {cause}"


def rerun_target(words: list[str], cwd: str) -> tuple[str, Callable[[], tuple[str, str]]] | None:
    """The step a command reruns with and the read of what it reruns, which answers that build, job or run by name and its state: what a run of the repository's own CI tool reruns, as its BuildProvider reads it, a branch meaning its pipeline's newest build there to finish, a `bk build rebuild` build, the job a `bk job retry` or a CI API retry names and the build an API rebuild names, or a `gh run rerun` run or job; None for a command that reruns nothing, such as a help read or one naming no target, which its tool refuses."""
    if HELP & set(words):
        return None
    if (own := own_build(cwd).retried(words)) is not None:
        read = partial(newest, own["pipeline"], own["branch"]) if own["branch"] else partial(job_in, own["pipeline"], own["build"], own["job"]) if own["job"] else partial(built, own["pipeline"], own["build"])
        return own["step"], read
    head = Path(words[0]).name
    for path, valued in RERUNS.items():
        positionals, values = operands(words[1:], valued)
        if (head, *positionals[: len(path) - 1]) == path:
            return rerun_read(path, positionals[len(path) - 1 :], values, cwd)
    return None


def own_build(cwd: str) -> providers.BuildProvider:
    """The BuildProvider of the repository at the folder's default branch, or of the drive the session's environment names, as the watchdog's does, when that folder is no clone or its repository builds nothing of its own; the base class, which reruns nothing of its own, when neither names one."""
    try:
        providers.default_head(Path(cwd))
    except providers.ProviderError:
        found = providers.BuildProvider(Path(), Path(), {})
    else:
        found = providers.at(Path(cwd)).build
    if type(found) is providers.BuildProvider and (name := os.environ.get(state.DRIVE_ENV)):
        return providers.of(state.load(name)).build
    return found


def rerun_read(path: tuple[str, ...], args: list[str], values: dict[str, str], cwd: str) -> tuple[str, Callable[[], tuple[str, str]]] | None:
    step = " ".join(path)
    if path[-1] == "api":
        method = values.get("-X") or values.get("--X") or values.get("--method") or "GET"
        if method.upper() == "GET" or not (found := RETRY_PATH.search(next(iter(args), ""))):
            return None
        pipeline = f"{found['org']}/{found['pipeline']}" if found["org"] else found["pipeline"]
        return step, partial(job_in, pipeline, found["number"], found["job"]) if found["job"] else partial(built, pipeline, found["number"])
    if path == ("bk", "build", "rebuild"):
        pipeline, number = values.get("-p") or values.get("--pipeline"), next(iter(args), None)
        return step, partial(built, pipeline, number) if pipeline and number else unnamed
    if path == ("bk", "job", "retry"):
        return (step, partial(retried_job, args[0])) if args else None
    run, job, repo = next(iter(args), None), values.get("-j") or values.get("--job"), values.get("-R") or values.get("--repo")
    return (step, partial(ran, run, job, repo, cwd)) if run or job else None


def operands(words: list[str], valued: frozenset[str]) -> tuple[list[str], dict[str, str]]:
    """A command's positional words and the last value of each flag it gives, a flag in valued taking the next word unless written `<flag>=<value>`, any other flag none."""
    positionals, values, rest = [], {}, iter(words)
    for word in rest:
        flag, given, value = word.partition("=")
        if not word.startswith("-"):
            positionals.append(word)
        elif given:
            values[flag] = value
        else:
            values[word] = next(rest, "") if word in valued else ""
    return positionals, values


def built(pipeline: str, number: str) -> tuple[str, str]:
    return f"{pipeline} build {number}", github.build(pipeline, number)["state"]


def newest(pipeline: str, branch: str) -> tuple[str, str]:
    found = github.finished(pipeline, branch)
    return f"{pipeline} build {found['number']}, its newest {branch} build to finish", found["state"]


def job_in(pipeline: str, number: str, job: str) -> tuple[str, str]:
    if (ran := next((each for each in github.build(pipeline, number)["jobs"] if each["id"] == job), None)) is None:
        raise github.GithubError(f"{pipeline} build {number} holds no job {job}")
    return f"job {job} of {pipeline} build {number}", ran["state"]


def retried_job(job: str) -> tuple[str, str]:
    return job_in(*github.job_build(job), job)


def ran(run: str | None, job: str | None, repo: str | None, cwd: str) -> tuple[str, str]:
    named = f"GitHub Actions job {job}" if job else f"GitHub Actions run {run}"
    return f"{named} of {repo}" if repo else named, github.run_conclusion(run, job, repo, cwd)


def unnamed() -> tuple[str, str]:
    raise github.GithubError("it names no build number or no pipeline, so the build it rebuilds is unknown; name both, as `bk build rebuild <number> -p <org/pipeline>`")


def watchdog_call(found: list[list[str]]) -> str | None:
    """Why a watchdog agent's call must not run: a command past the reads and two acts agents.WATCHDOG_RUNS lists."""
    for words in found:
        named = verb(words)
        if not any(tuple(named[: len(allowed)]) == allowed for allowed in agents.WATCHDOG_RUNS):
            runs = ", ".join(" ".join(allowed) for allowed in agents.WATCHDOG_RUNS)
            return f"refused: the watchdog runs only {runs}; put `{' '.join(named)}` in your NEXT line for the coordinator instead"
    return None


def helper_restart(named: list[str]) -> str | None:
    """The hook-helper restart a command's words run: `capt-hook helper install`, or a `brew upgrade` or `brew reinstall` of captain-hook, which bare `brew upgrade` is too."""
    if any(named[index + 1 : index + 3] == ["helper", "install"] and re.split(r"[@=<>]", named[index])[0] == install.HELPER for index in range(len(named))):
        return f"`{install.HELPER} helper install`"
    if named[0] == "brew" and named[1:2] and named[1] in BREW_RESTARTS and (named[1:] == ["upgrade"] or any(Path(word).name == install.HOOKS for word in named[2:])):
        return f"`brew {named[1]}` of {install.HOOKS}"
    return None


def helper_install(found: list[list[str]], drives: list[str]) -> str | None:
    """Why any session's call may not restart the hook helper every session on this Mac shares: no answer of his recorded for a drive picks Install now on the card asking to install captain-hook's newest release, or Captain Hook.app already holds it, so the call is a second restart he never approved."""
    if not (step := next(filter(None, (helper_restart(verb(words)) for words in found)), None)) or not (names := drives or state.names()):
        return None
    try:
        release = install.helper_release()
        runs = install.app_release()
        approved = install.helper_approved(names, release)
        card = install.helper_card(names, release)
    except (github.GithubError, state.DriveError) as error:
        return f"refused: {step} restarts the hook helper every session on this Mac shares, and whether he approved this one could not be read: {error}"
    if runs == release:
        return (
            f"refused: Captain Hook.app already holds captain-hook {release}, the newest release, so {step}"
            " only restarts the hook helper again, a restart every session on this Mac shares that he never approved; leave it running"
        )
    if approved:
        return None
    return (
        f"refused: {step} installs captain-hook {release}, the newest release, and restarts the hook helper every session on"
        f" this Mac shares; he approves each such restart on its own card, and no answer of his picks {install.INSTALL_NOW} on"
        f" {card}; run it once his tap or `drive answer` records his pick"
    )


@dataclass
class Place:
    """Where a script's commands run: the folder it is in, None once a `cd` names one the guard cannot expand, the variables it set, the script file being read and those read on the way to it, the positional parameters its call bound, None when that call is unknown, the functions it defined and those it is inside."""

    cwd: Path | None
    env: dict[str, str] = field(default_factory=dict)
    script: Path | None = None
    seen: frozenset[Path] = frozenset()
    args: list[str] | None = None
    functions: dict[str, list] = field(default_factory=dict)
    calling: frozenset[str] = frozenset()

    def at(self, word: str) -> Path | None:
        """The path a word names from the folder the script runs in; None when the word holds a parameter or substitution left unexpanded, or is relative and that folder is unknown."""
        if not literal(word):
            return None
        path = Path(os.path.expanduser(word))
        return path if path.is_absolute() else self.cwd / path if self.cwd else None


def reached(text: str, place: Place, depth: int = 0) -> Iterator[list[str]]:
    """The words of each command shell text runs and of each command it starts: an `sh -c` script, a script file a shell, `source` or its own path runs, the body of each function it calls, a for loop's body once per word it walks, the command after `drive break-glass … --` and the one `xargs` runs, each word expanded with the variables and positional parameters set before it; a function nothing calls runs none of its commands, and a case statement only the arms its selector's value picks."""
    yield from walked(parsed(text), place, depth)


def parsed(text: str) -> list:
    """The commands of shell text as `grouped` reads them, line by line where the whole text does not parse, so one unclosed quote hides no other line."""
    try:
        return grouped(runnable(text))
    except ValueError:
        return [node for line in text.splitlines() if line.strip() != text.strip() for node in parsed(line)]


def walked(nodes: list, place: Place, depth: int) -> Iterator[list[str]]:
    for node in nodes:
        if isinstance(node, Function):
            place.functions[node.name] = node.body
        elif isinstance(node, Case):
            for body in taken(node, place):
                yield from walked(body, place, depth)
        elif isinstance(node, Loop):
            for value in spread(node.values, place.env, place.args):
                place.env[node.name] = value
                yield from walked(node.body, place, depth)
        else:
            yield from started(spread(node, place.env, place.args), place, depth)


def taken(case: Case, place: Place) -> list[list]:
    """The bodies of the arms a case statement can run: the first whose literal pattern its selector's literal value matches and each a `;&` or `;;&` goes on to, with each arm before it whose pattern is not literal; every arm when the selector is not literal."""
    selector = expanded(case.selector, place.env, place.args)
    known, bodies = literal(selector), []
    for patterns, body, falls in case.arms:
        words = [expanded(pattern, place.env, place.args) for pattern in patterns]
        certain = known and all(map(literal, words))
        if certain and not any(fnmatchcase(selector, word) for word in words):
            continue
        bodies.append(body)
        if certain and not falls:
            break
        known = known and not falls
    return bodies


def started(written: list[str], place: Place, depth: int) -> Iterator[list[str]]:
    words = unwrapped(written, place)
    if not words:
        return
    if words[0] == "cd":
        place.cwd = place.at(words[1]) if words[1:] else Path.home()
        return
    if words[0] == "export":
        place.env |= dict(word.split("=", 1) for word in words[1:] if ASSIGNMENT.fullmatch(word))
        return
    yield words
    if Path(words[0]).name in SHELLS and "-c" in words[:-1]:
        yield from reached(words[words.index("-c") + 1], Place(place.cwd, dict(place.env), place.script, place.seen), depth + 1)
    elif words[0] in place.functions and words[0] not in place.calling:
        called = replace(place, args=words[1:], calling=place.calling | {words[0]})
        yield from walked(place.functions[words[0]], called, depth)
        place.cwd = called.cwd
    elif depth < FOLLOWED_DEPTH and (followed := script_file(words, place)):
        script, args = followed
        fed = XARGS_INPUT in words
        functions = place.functions if Path(words[0]).name in SOURCES else {}
        yield from reached(located(script), Place(place.cwd, dict(place.env), script, place.seen | {script}, None if fed else args, functions), depth + 1)


def located(script: Path) -> str:
    """A script's text with each `$(cd "$(dirname "$0")/<path>" && pwd)` written as the folder it names, since the shell words split a substitution nested in quotes at its parentheses."""
    return SCRIPT_FOLDER.sub(lambda found: str((script.parent / found[1].lstrip("/")).resolve()), script.read_text(errors="replace"))


def json_field(value: str, place: Place) -> str:
    """The string a `$(jq -r '.<field>' <file>)` value reads from that JSON file, such as `tools/artifact.sh`'s bucket from `tools/artifacts.json`; any other value, or one the file holds no string for, as written."""
    if not (found := JSON_FIELD.fullmatch(value)) or not (path := place.at(found[2])) or not os.path.isfile(path):
        return value
    try:
        read = reduce(lambda node, key: node[key], found[1].split(".")[1:], json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError):
        return value
    return read if isinstance(read, str) else value


def unwrapped(words: list[str], place: Place) -> list[str]:
    """A command's own words, past the assignments it makes, shell keywords, wrappers and their options, and `drive break-glass` up to its `--`, the input an `xargs` gives it as a word the guard cannot expand; a variable assigned a command's output, which `grouped` splits at `$(` and leaves as `$`, stays as written."""
    while words:
        head = Path(words[0]).name
        if ASSIGNMENT.fullmatch(words[0]):
            name, value = words[0].split("=", 1)
            place.env[name] = f"${name}" if value == "$" else json_field(value, place)
            words = words[1:]
        elif words[0] in KEYWORDS:
            words = words[1:]
        elif head in WRAPPERS or head in OPTIONED:
            replaced = xargs_replace(words[1:]) if head == "xargs" else None
            words = past_options(words[1:], OPTIONED.get(head, set()))
            words = words[1:] if head == "timeout" else words
            if head == "xargs" and words:
                words = [word.replace(replaced, XARGS_INPUT) for word in words] if replaced else [*words, XARGS_INPUT]
        elif verb(words)[:2] == ["drive", "break-glass"] and "--" in words:
            words = words[words.index("--") + 1 :]
        else:
            break
    return words


def xargs_replace(words: list[str]) -> str | None:
    """The string an `xargs` call's `-I`, `-i` or `--replace` names for its input to take the place of, `{}` when `-i` or `--replace` names none; None when its input follows its command's words."""
    rest = iter(words)
    for word in rest:
        if word == "--" or not word.startswith("-"):
            return None
        if word == "-I":
            return next(rest, None)
        if found := XARGS_REPLACE.fullmatch(word):
            return found["string"] or "{}"
        if word in OPTIONED["xargs"]:
            next(rest, None)
    return None


def past_options(words: list[str], valued: set[str]) -> list[str]:
    rest = iter(words)
    for word in rest:
        if word == "--":
            return list(rest)
        if word == "-" or not word.startswith("-"):
            return [word, *rest]
        if word in valued:
            next(rest, None)
    return []


def script_file(words: list[str], place: Place) -> tuple[Path, list[str] | None] | None:
    """The shell script file a command runs, by a shell, `source` or its own path, found from the folder it runs in or, inside a script, beside that script, with the arguments it runs it with, a `source` given none keeping its caller's; None when it runs none, as a shell given `-n` only parses its script, a file started by its path is no shell script, it is not on disk or was read on the way here."""
    name = Path(words[0]).name
    if name in SHELLS:
        operands = past_options(words[1:], {"-o"})
        given = [] if any(PARSE_ONLY.fullmatch(word) for word in words[1 : len(words) - len(operands)]) else operands
    elif name in SOURCES:
        given = words[1:]
    else:
        given = words if "/" in words[0] else []
    if not given:
        return None
    beside = place.script and place.script.parent / Path(given[0]).name
    found = next((candidate for candidate in (place.at(given[0]), beside) if candidate and os.path.isfile(candidate)), None)
    if found is None or found in place.seen or (name not in SHELLS | SOURCES and not shell_script(found)):
        return None
    return found, place.args if name in SOURCES and not given[1:] else given[1:]


def shell_script(path: Path) -> bool:
    """Whether a file started by its path runs as a shell script: its `#!` line names a shell, or it has no `#!` line and its first line holds no NUL byte, so zsh and bash take it for text and run it through `/bin/sh`."""
    with path.open("rb") as file:
        first = file.readline(FIRST_LINE_LIMIT)
    if first.startswith(b"#!"):
        return any(Path(word).name in SHELLS for word in first[2:].decode(errors="replace").split())
    return b"\0" not in first


def bucket_locations(words: list[str]) -> list[str]:
    """The bucket or blob locations a command reads or writes, as `<bucket>/<key>`: each it names through the AWS, Google Cloud or Azure CLI, s5cmd, gsutil, rclone or azcopy, whatever it does there, and the bucket with its key or prefix and the copy source of each s3api operation on objects."""
    tool = Path(words[0]).name
    if tool == "aws" and "s3api" in words:
        operation = next((word for word in words[words.index("s3api") + 1 :] if not word.startswith("-")), "")
        if not S3API_OBJECTS.search(operation):
            return []
        named = "/".join(part for part in (option(words, "--bucket"), option(words, "--key") or option(words, "--prefix")) if part)
        return [location for location in (named, option(words, "--copy-source")) if location]
    service = SERVICES.get(tool, ())
    if tool not in STORAGE_TOOLS or any(name not in words for name in service):
        return []
    if tool == "az":
        return [f"{option(words, '--account-name') or 'azure'}/{option(words, '--container-name') or option(words, '-c') or ''}"]
    after = words[words.index(service[-1]) + 1 :] if service else words[1:]
    return [location for word in after if (location := remote_location(tool, word))]


def namespace_reach(words: list[str], namespaces: frozenset[str]) -> tuple[str, str] | None:
    """The `kubectl` step through which a command reaches a namespace holding a team's data, and where: none when the repository names no such namespace, else a `kubectl proxy`, which serves every namespace, a `--raw` call on a secrets path such a namespace or every namespace holds, or on a path it cannot expand, or, in a namespace that may be one, an exec, attach, copy, debug session or port-forward, a step naming Secret, such as a read of the blobstore's root key, or a get, describe or edit of a kind the guard cannot read, as an unexpanded word or `xargs` input is."""
    if not namespaces or Path(words[0]).name != "kubectl":
        return None
    flags = words[: words.index("--")] if "--" in words else words
    named, rest = [], iter(flags[1:])
    for word in rest:
        if word in KUBECTL_VALUED:
            next(rest, None)
        elif not word.startswith("-"):
            named.append(word)
    if not named:
        return None
    if named[0] == "proxy":
        return "`kubectl proxy`", EVERY_NAMESPACE
    if (raw := option(flags, "--raw")) and (where := raw_secrets(raw, namespaces)):
        return f"`kubectl {named[0]} --raw` on a secrets path, or one the guard cannot read,", where
    if not (where := namespace_where(kubectl_namespace(flags), bool(ALL_NAMESPACES & set(flags)), namespaces)):
        return None
    if named[0] in NAMESPACE_REACHES:
        return f"`kubectl {named[0]}`", where
    if any(names_secret(word) for word in named[1:]):
        return f"`kubectl {named[0]} secret`", where
    kinds = named[1:] if "/" in "".join(named[1:2]) else named[1:2]
    if named[0] in KIND_READS and any(not literal(kind.split("/")[0]) for kind in kinds):
        return f"`kubectl {named[0]}` of a kind the guard cannot read, which may be Secret,", where
    return None


def kubectl_namespace(flags: list[str]) -> str | None:
    """The namespace a `kubectl` command's flags name last, as `-n <ns>`, `-n=<ns>`, `-n<ns>`, `--namespace <ns>` or `--namespace=<ns>`."""
    named, rest = None, iter(flags)
    for word in rest:
        if word in ("-n", "--namespace"):
            named = next(rest, None)
        elif found := KUBECTL_NAMESPACE.fullmatch(word):
            named = found["namespace"]
    return named


def namespace_where(namespace: str | None, every: bool, namespaces: frozenset[str]) -> str | None:
    """Where a `kubectl` step reaches a namespace holding a team's data, as its refusal says it: every namespace, one of namespaces, or one the guard cannot expand, which may be one; None for any other namespace, or none, the context's default."""
    if every:
        return EVERY_NAMESPACE
    if namespace in namespaces:
        return f"in the `{namespace}` namespace"
    if namespace and not literal(namespace):
        return f"in the `{namespace}` namespace, which the guard cannot expand,"
    return None


def raw_secrets(path: str, namespaces: frozenset[str]) -> str | None:
    """Where a `kubectl --raw` API path reaches the secrets of a namespace holding a team's data: the secrets of one `namespace_where` reads as such, or of every namespace when it names none or the guard cannot expand it."""
    segments = path.split("?")[0].strip("/").split("/")
    if literal(path) and "secrets" not in segments:
        return None
    namespace = segments[segments.index("namespaces") + 1] if "namespaces" in segments[:-1] else None
    return namespace_where(namespace, namespace is None, namespaces)


def names_secret(word: str) -> bool:
    """Whether a `kubectl` resource word names Secret, in any case, singular or plural, alone, as `<kind>/<name>`, in `.v1.` form or in a comma list."""
    return any(kind.split(".")[0].lower() in SECRETS for kind in word.split("/")[0].split(","))


def remote_location(tool: str, word: str) -> str | None:
    if word.startswith("-"):
        return None
    if tool == "azcopy":
        return "/".join(found.groups()) if (found := AZURE_URL.fullmatch(word)) else None
    found = (RCLONE_PATH if tool == "rclone" else BUCKET_URI).fullmatch(word)
    return found[1] if found and found[1] else None


def team_data(command: str, cwd: str, drives: list[str]) -> agents.Unjudged | None:
    """Why a drive job's call must not run, refused by its shape alone: it, a script it runs, or a command it starts through `drive break-glass` or `xargs`, reads or writes a team's data, a location the repository's DataProvider calls a team's area or lists among its team buckets, read from the default branch of the folder its session runs in whatever folder the command `cd`s to, or any bucket but its build outputs when that branch holds no such list, or it reaches a namespace holding a team's data through `kubectl`. The folder's providers judge it unless they guard no data, when its drive's do; a refusal naming the error when they fail to load."""
    data = None
    for words in reached(command, Place(Path(cwd))):
        locations = bucket_locations(words)
        if not locations and Path(words[0]).name != "kubectl":
            continue
        if data is None:
            try:
                data = data_guard(cwd, drives)
            except providers.ProviderError as error:
                print(f"bash_guard let this through unjudged: the providers that say whose data it reaches failed to load, and a gate that cannot read its inputs lets the call go: {error}", file=sys.stderr)
                return None
        for location in locations:
            if data.team_area and data.team_area.search(location):
                return agents.Unjudged(data.refusal("team", location=location, rule=data.rule))
            listed = data.buckets()
            if listed is None and location.split("/")[0] not in data.outputs():
                return agents.Unjudged(data.refusal("unlisted", location=location, folder=cwd, rule=data.rule))
            if any(f"{location.rstrip('/')}/".startswith(entry) or entry.startswith(f"{location.rstrip('/')}/") for entry in listed or ()):
                return agents.Unjudged(data.refusal("team", location=location, rule=data.rule))
        if reach := namespace_reach(words, data.namespaces):
            return agents.Unjudged(data.refusal("namespace", step=reach[0], where=reach[1], rule=data.rule))
    return None


def data_guard(cwd: str, drives: list[str]) -> providers.DataProvider:
    """The DataProvider of the repository at the folder's default branch, or of the session's drive when that one guards no data, as a meta job's checkout of a tool repository holding no team's data."""
    found = providers.at(Path(cwd)).data
    if type(found) is providers.DataProvider and drives:
        return providers.of(state.load(drives[0])).data
    return found


def datadog_cli(command: str, cwd: str) -> str | None:
    """Why a drive session's call must not run: it, a script it runs, or a command it starts through `drive break-glass` or `xargs`, reads Datadog through the `pup` command rather than the Datadog MCP."""
    return DATADOG_READ if any(Path(words[0]).name == DATADOG_CLI for words in reached(command, Place(Path(cwd)))) else None


def role_of(session: str | None = None, terminal: str | None = None, alias: str | None = None) -> tuple[dict, str] | None:
    """The drive and role whose recorded session or cci alias this is, or whose session runs in this Orca terminal, as one read of Orca and the process tree finds it."""
    seats = [(data, role, held) for data in map(state.load, state.names()) for role, held in state.bound_sessions(data).items()]
    if terminal:
        found = orca.session_terminals([held["session"] for _, _, held in seats])
        session = next((held["session"] for _, _, held in seats if any(row["handle"] == terminal for row in found[held["session"]])), None)
    for data, role, held in seats:
        project = role.removeprefix("sub-coordinator ") if role.startswith("sub-coordinator ") else None
        if (session and held["session"] == session) or (alias and state.alias(data["name"], role, project) == alias):
            return data, role
    return None


def hidden(held: dict) -> str | None:
    """The command that shows a role's session when every Orca terminal it runs in is one Orca only hides, None when one shows, none runs it or Orca cannot say."""
    try:
        terminals = orca.session_terminals([held["session"]])[held["session"]]
        if not terminals or not all(terminal["orphaned"] for terminal in terminals):
            return None
        checkout = orca.main_checkout(held["cwd"])
    except OrcaError:
        return None
    return f"orca repo set --repo path:{checkout} --external-worktree-visibility show"


def live_resume(found: list[list[str]]) -> str | None:
    for words in found:
        session = option(words, "--resume") or option(words, "-r")
        if Path(words[0]).name != "claude" or not session or "--fork-session" in words:
            continue
        if (pid := orca.live_pid(session)) is None:
            continue
        owner = role_of(session=session)
        if owner and (show := hidden(state.bound_sessions(owner[0])[owner[1]])):
            return (
                f"refused: session {session} still runs as pid {pid}, in a terminal Orca only hides because it shows no"
                f" worktree it did not make; leave it running and show it with `{show}`"
            )
        return (
            f"refused: session {session} still runs as pid {pid}, and a resume while it runs copies it under a new id, so two"
            f" sessions answer for one role; end it, wait until `ps -p {pid}` lists nothing, then resume it"
        )
    return None


def ended_role(words: list[str]) -> tuple[dict, str] | None:
    named = verb(words)
    if named[:3] == ["orca", "terminal", "send"] and option(words, "--text") == "/exit":
        return role_of(terminal=option(words, "--terminal"))
    if named[:3] == ["cci", "session", "type"] and named[4:] == ["/exit"]:
        return role_of(alias=named[3])
    if named[:2] == ["claude", "stop"] and named[2:3]:
        return role_of(session=named[2])
    return None


def destroying(command: str, cwd: str, session: str, drives: list[str]) -> str | None:
    """Why a drive session's own destructive command waits for it to read what came since, by meta defect 735756, where root deleted a writer pod a minute after the urgent stop and withdrawal of its ask, which reach a session only when a tool call returns: a command a `[[destructive]]` table of the repository's .claude/long-running.toml matches, each table naming a `class`, a `match` regex and an optional `env` regex whose first group names the env it acts on, refuses once, by gate.gate, printing each record addressed to the session's lane it has not read and each open record naming that env; an inbox that cannot be read lets it through, warned."""
    rules = next((folder / state.REPO_RULES for folder in [Path(cwd), *Path(cwd).parents] if (folder / state.REPO_RULES).is_file()), None)
    if not drives or rules is None:
        return None
    tables = [table for table in tomllib.loads(rules.read_text()).get("destructive", []) if re.search(table["match"], command)]
    if not tables or not (lane := state.session_lane(data := state.load(drives[0]), session)):
        return None
    envs = {found[1] for table in tables if table.get("env") and (found := re.search(table["env"], command))}
    try:
        unread = reads.run(["cci", "tail", "--drive", data["name"], "--to", lane, "--mail"], Path.cwd()).strip()
        held = reads.run(["cci", "tail", "--drive", data["name"], "--open"], Path.cwd()).splitlines() if envs else []
    except reads.ReadError as error:
        print(f"bash_guard let this {tables[0]['class']} through unread: this lane's records could not be read: {error}", file=sys.stderr)
        return None
    findings = [f"records to {lane} it has not read: {unread}"] if unread else []
    findings += [f"an open record on {env}: {line}" for env in sorted(envs) for line in held if re.search(rf"\b{re.escape(env)}\b", line)]
    return gate.gate(data["name"], f"{tables[0]['class']} {command}", findings, session)


def mid_incident(found: list[list[str]]) -> str | None:
    for words in found:
        if not (owner := ended_role(words)):
            continue
        data, role = owner
        if incidents := state.open_incidents(data, list(data["orders"])):
            named = ", ".join(incidents)
            return (
                f"refused: {named} is an open incident order, and ending the {role} now stops it mid-incident; move it after"
                f" the fix is live, once `drive status {incidents[0]}` reads done"
            )
    return None


def mid_answer(found: list[list[str]]) -> str | None:
    """Why a command ending a communicator's session must not run now: its Claude Code process is mid-turn, or a message of his from the last ten minutes waits for its answer, the check `drive reload` waits out before it types anything, so no lane cuts an answer to him short."""
    for words in found:
        if not (owner := ended_role(words)) or not owner[1].startswith("communicator"):
            continue
        data, role = owner
        held = state.bound_sessions(data)[role]
        status = install.session_status(pid, held["session"]) if (pid := orca.live_pid(held["session"])) else None
        mid = (
            f"the {role} of {data['name']} is mid-answer to him, its turn running or a message of his from the last ten minutes"
            f" unanswered; `drive reload --drive {data['name']}` reloads it once it is idle with nothing owed, and no /exit is needed for new code"
        )
        if (status not in (None, "idle") or state.answering(data["owed_replies"], state.now())) and (refused := gate.gate(data["name"], f"end {role}", [mid])):
            return refused
    return None


def deny_reason(payload: dict) -> str | None:
    """Why a drive session's or drive agent's call must not run: it, or an Orca lane's, reads or writes a team's buckets, blobs or databases where it runs, it reads Datadog through `pup` rather than the Datadog MCP, a designer pushes, a builder, on this Mac or a Sprite, opens its PR while his open cards hold its job, a read takes a stale version's cached skill page, or a session is resumed while it runs, from a shell-ending command, or ended mid-incident, a call retries past a failing check or reruns a build, job or run that did not pass, or a watchdog agent runs anything but its reads and two acts or reruns one that did not fail; why any session's call restarts the hook helper he never approved on a card; and why Claude Code's permission prompt on a drive agent's or an Orca lane's call to any tool gets a refusal rather than an answer nobody gives."""
    call = payload["tool_input"]
    session = payload.get("session_id") or ""
    drives = state.session_drives(session)
    kind = payload.get("agent_type") or ""
    if payload.get("hook_event_name") == "PermissionRequest":
        return UNSEEN_PROMPT if os.environ.get(state.REMOTE_LANE_ENV) or (payload.get("agent_id") and (drives or kind.startswith(AGENTS))) else None
    if payload["tool_name"] != "Bash":
        return cached_page(call.get("file_path") or call.get("path") or "") if drives else None
    found = commands(runnable(call["command"]))
    cwd = payload.get("cwd") or os.getcwd()
    if busy := mid_answer(found + [words for inner in terminal_scripts(found) for words in inner]):
        return busy
    if os.environ.get(state.WATCHDOG_ENV):
        return watchdog_call(found) or reran(found, cwd, watchdog=True)
    if (drives or kind.startswith(AGENTS) or os.environ.get(state.REMOTE_LANE_ENV)) and (touched := team_data(call["command"], cwd, drives)):
        return touched
    if not drives and os.environ.get(state.REMOTE_LANE_ENV):
        return cci_chatter(found) or lane_held(found)
    if not drives and not kind.startswith(AGENTS):
        return helper_install(found, drives)
    scripts = terminal_scripts(found)
    reasons = (
        lambda: datadog_cli(call["command"], cwd),
        lambda: cci_chatter(found),
        lambda: designer_push(found) if kind == DESIGNER else None,
        lambda: bare_pr(found, call["command"]) if kind == BUILDER else None,
        lambda: held_pr(found, payload.get("agent_id") or "", drives) if kind == BUILDER else None,
        lambda: cached_page(call["command"]) if drives else None,
        lambda: shell_after_claude(scripts),
        lambda: live_resume(found + [words for inner in scripts for words in inner]),
        lambda: mid_incident(found),
        lambda: None if payload.get("agent_id") else destroying(call["command"], cwd, session, drives),
        lambda: reran(found, cwd),
        lambda: helper_install(found, drives),
    )
    return next(filter(None, (reason() for reason in reasons)), None)


def foreground(payload: dict) -> dict | None:
    """A drive session's or drive agent's Bash call running `drive job resume`, set to the one shape it runs in: the foreground with `timeout: 20000`, since a send returns in about 5 seconds and a relaunch moves itself to the background at 20; None when it already runs so."""
    call = payload["tool_input"]
    if payload.get("hook_event_name") == "PermissionRequest" or payload["tool_name"] != "Bash":
        return None
    if not call.get("run_in_background") and call.get("timeout") == RESUME_TIMEOUT:
        return None
    if not state.session_drives(payload.get("session_id") or "") and not (payload.get("agent_type") or "").startswith(AGENTS):
        return None
    if not any(verb(words)[:3] == ["drive", "job", "resume"] for words in commands(runnable(call["command"]))):
        return None
    return call | {"run_in_background": False, "timeout": RESUME_TIMEOUT}


def main() -> None:
    try:
        payload = agents.seen(json.load(sys.stdin))
        reason = deny_reason(payload)
        made = foreground(payload)
    except ValueError:
        return
    if reason and payload.get("hook_event_name") == "PermissionRequest":
        decided = {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": reason}}
    elif reason:
        decided = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    elif made:
        decided = {"hookEventName": "PreToolUse", "updatedInput": made}
    else:
        return
    print(json.dumps({"hookSpecificOutput": decided} | agents.ruled(reason or "")))


if __name__ == "__main__":
    main()
