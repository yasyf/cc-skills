import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache, reduce
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, github, install, land, limits, orca, state, view
from drive.orca import OrcaError
from drive.shell import expanded, runnable, simple_commands, templates

AGENTS = "long-running:"
DESIGNER = "long-running:designer"
BUILDER = "long-running:builder"
SHELLS = {"sh", "bash", "zsh", "dash", "fish"}
WRAPPERS = {"command", "exec", "nohup", "env", "time"}
VALUED = {"-C", "-R", "--repo", "--drive"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
SHIP = ("ccx", "vcs", "ship")
PUSHES = (("gh", "pr", "create"), ("git", "push"), ("jj", "git", "push"), SHIP, ("gt", "submit"))
PUSH_ONLY = {"--no-pr", "--dry-run"}
CACHED_PAGE = re.compile(r"plugins/cache/([^/\s'\"]+)/long-running/([^/\s'\"]+)/skills/long-running/reference/")
RERUNS = (("ci", "bk", "rerun"), ("bk", "job", "retry"), ("bk", "build", "rebuild"), ("gh", "run", "rerun"))
FLAKE = "--flake"
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
TEAM_BUCKETS = "origin/dev:release/team-buckets.generated.json"
STORAGE_TOOLS = {"aws", "s5cmd", "gsutil", "gcloud", "rclone", "azcopy", "az"}
SERVICES = {"aws": ("s3",), "gcloud": ("storage",), "az": ("storage",)}
S3API_OBJECTS = re.compile(r"object|upload|part")
BUCKET_URI = re.compile(r"(?:s3|gs)://([^\s]*)")
AZURE_URL = re.compile(r"https://([^/.\s]+)\.(?:blob|dfs)\.core\.windows\.net/([^?\s]*)")
RCLONE_PATH = re.compile(r"[\w.-]*:(?!//)([^\s]*)")
KEYWORDS = {"!", "{", "if", "then", "elif", "else", "do", "while", "until"}
OPTIONED = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "exec": {"-a"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-P", "-n", "-I", "-L", "-d", "-s", "-a", "-E", "--max-procs", "--max-args", "--replace", "--max-lines", "--delimiter", "--max-chars", "--arg-file", "--eof"},
}
SOURCES = {"source", "."}
FOLLOWED_DEPTH = 4
SCRIPT_FOLDER = re.compile(r'\$\(cd "\$\(dirname "(?:\$0|\$\{BASH_SOURCE\[0\]\})"\)([^"]*)" && pwd\)')
JSON_FIELD = re.compile(r"\$\(jq -r '?((?:\.\w+)+)'? (\S+)\)")
TEAM_AREA = re.compile(r"iris/teams/([^/\s]+)/([^/\s]+)")
BOX_ROUTE = (
    "Run the step on a pass box of the team's env where Forge-AI/monorepo grants that box the step, as"
    " `PASS_BOX_OPERATOR_ENVS` in `infra/rows/identity/pass-box-operator.ts` and `PASS_BOX_OPERATOR_RULES` in"
    " `infra/rows/sandsql/workload.ts` do for a SandSQL namespace and `kubectl auth can-i` on the box confirms: launch it"
    " with `AWS_PROFILE=<env profile> tools/ci pass-box launch --purpose <job> --type <instance type> --delete-after"
    " <YYYY-MM-DD>`, run the script there with `AWS_PROFILE=<env profile> tools/ci pass-box run --purpose <job> <script>`,"
    " print counts and summaries and copy no team data back; docs/pass-boxes.md in Forge-AI/monorepo has each step. A pass"
    f" no box is granted {limits.CLUSTER_JOB}, and any other read or write {limits.UNMADE}."
)
TEAM_DATA = (
    "refused: this reads or writes `{location}` where the job runs, and " + limits.TEAM_DATA_RULE + ". " + BOX_ROUTE + " {route}"
    + " It holds a team's data, as its key is under `iris/teams/` or Forge-AI/monorepo's `release/team-buckets.generated.json`"
    " at origin/dev, which infra writes from each bucket's grants and rows, lists it or its folder. A box's binary goes"
    " under `pass-box/<purpose>/` in its env's MicroVM artifacts bucket, which that list leaves out, and the box fetches"
    " it through a presigned URL"
)
UNLISTED = (
    "refused: this reads or writes `{location}` where the job runs, and the checkout it runs in holds no"
    " `release/team-buckets.generated.json` at origin/dev to say whether that bucket holds a team's data, so it is refused"
    " as one that does. Run it from a Forge-AI/monorepo checkout, after `git fetch origin dev` in one fetched before that"
    " list landed: the list names each bucket and folder holding a team's data"
)
SANDSQL_NAMESPACES = {"sandsql", "sandsql-system", "sandsql-inbox"}
SANDSQL_REACHES = {"exec", "port-forward", "attach", "cp", "debug"}
SECRETS = {"secret", "secrets"}
KUBECTL_VALUED = {"-n", "--namespace", "--context", "--cluster", "--user", "--kubeconfig", "-c", "--container", "-o", "--output", "-l", "--selector", "-f", "--filename", "--address", "-s", "--server", "--as"}
SANDSQL_DATA = (
    "refused: `{step}` in the `{namespace}` namespace opens a team's SandSQL databases where the job runs, and with them"
    " every read and write of their rows, and " + limits.TEAM_DATA_RULE + ". " + BOX_ROUTE
)
RUNS_ROUTE = (
    "A team's `iris/teams/<team>/runs/` is read on the box under a `runs_read` grant, which a plat-prod box's script loads"
    ' first with `eval "$(./ci runs-reader grant --team {team})"`; a grant that refuses returns `BLOCKED` naming the'
    " prefix, the team and its refusal."
)
NO_ROUTE = (
    "No service account reads `{prefix}`, as the runs reader's grant reaches `iris/teams/<team>/runs/` alone: return"
    " `BLOCKED` naming the prefix and the team, which leaves him to pick the service account that reads it, never a"
    " person's grant such as `tools/ci pass-box-creds`, which mints under your own sand session."
)

DATADOG_CLI = "pup"
DATADOG_READ = (
    "refused: a drive session reads Datadog only through the Datadog MCP's tools, whose `execute_code` reaches any Datadog"
    " API, never the `pup` command, whose OAuth grant keeps the scopes its `pup auth login` was granted while later pup"
    " releases add commands needing more, such as profiling's `continuous_profiler_read`. Load them with ToolSearch"
    " `select:mcp__plugin_datadog_mcp__execute_code,mcp__plugin_datadog_mcp__search_datadog_sdk` and make the same read"
    " through `execute_code`, or on a Sprite lane, which has none of them, through `drive datadog execute_code -` with its"
    " JSON arguments on stdin"
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


def bare_pr(found: list[list[str]]) -> str | None:
    if not any(tuple(verb(words)[:3]) == ("gh", "pr", "create") for words in found):
        return None
    return (
        "refused: a builder opens its PR the repository's way, never a bare `gh pr create`: its own submit wrapper, otherwise"
        " the `open-pr` skill, which ships it with `ccx vcs ship --pr-title-file <title> --pr-body-file <body>` and keeps"
        " the branch, stack and PR text the repository expects"
    )


def opens_pr(found: list[list[str]]) -> bool:
    """Whether a call runs a `ccx vcs ship` that may open a PR: one with neither `--no-pr` nor `--dry-run`."""
    return any(tuple(verb(words)[:3]) == SHIP and not PUSH_ONLY & set(words) for words in found)


def exempted(found: list[list[str]]) -> str | None:
    """The exempt class a `drive pr add` in the same call registers the shipped PR under."""
    return next((option(words, "--exempt") for words in found if verb(words)[:3] == ["drive", "pr", "add"]), None)


def held(job_id: str, cards: list[str]) -> str:
    return (
        f"refused: {job_id}'s work waits on his answer to {', '.join(cards)}, and this ship would open its PR, which goes"
        " up only once he answers each, since work does not land with a question of his on it unanswered: push the"
        " branch with `ccx vcs ship --no-pr` and return `BUILT <branch>, waits on <cards>`; the watch says FREE when"
        " the last is answered. A flake fix, which no design card holds, goes up in one call with its registration:"
        ' `ccx vcs ship ... && drive pr add "$(gh pr view --json url -q .url)" --job <your job> --exempt "flake fix"`'
    )


def held_pr(found: list[list[str]], agent: str, drives: list[str]) -> str | None:
    """Why a builder's `ccx vcs ship` on this Mac may not open its PR: his open cards hold its job."""
    if not opens_pr(found):
        return None
    for name in drives:
        data = state.load(name)
        if (job_id := state.agent_job(data, agent)) and (cards := land.ship_held(data, job_id, view.live_cards(name), exempted(found))):
            return held(job_id, cards)
    return None


def lane_held(found: list[list[str]]) -> str | None:
    """Why a Sprite lane's `ccx vcs ship` may not open its PR: `drive job held`, which the Sprite's `drive` asks of the coordinator's Mac, names his open cards holding the lane's job."""
    if not opens_pr(found):
        return None
    try:
        answer = land.lane_held(*(f"--exempt={exempt}" for exempt in [exempted(found)] if exempt))
    except state.DriveError as error:
        return f"refused: `drive job held` could not say whether his open cards hold this lane's PR: {error}; ship again once it answers"
    return held(answer["id"], answer["cards"]) if answer["cards"] else None


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


def retried(found: list[list[str]]) -> str | None:
    """Why a call retries past a failing check: `stack-enqueue --flake` requeues a PR past its failed verification, and a rerun runs the failed job again unchanged."""
    for words in found:
        named = verb(words)
        if named[0] == "stack-enqueue" and any(word == FLAKE or word.startswith(f"{FLAKE}=") for word in words):
            step = f"stack-enqueue {FLAKE}"
        elif not (step := next((" ".join(rerun) for rerun in RERUNS if tuple(named[: len(rerun)]) == rerun), None)):
            continue
        return (
            f"refused: `{step}` retries past a failing check, and a check that fails is fixed at its cause, never retried past"
            f" with {FLAKE} or a rerun. Read the failed job's own output with `tools/ci bk log '<job URL>'`, or `gh run view <run>"
            " --log-failed` on GitHub Actions, fix the cause and push the fix; a test that flakes gets a flake fix of its own"
        )
    return None


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
    """Where a script's commands run: the folder it is in, the variables it set, the script file being read and those read on the way to it."""

    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    script: Path | None = None
    seen: frozenset[Path] = frozenset()


def reached(text: str, place: Place, depth: int = 0) -> Iterator[list[str]]:
    """The words of each command shell text runs and of each command it starts: an `sh -c` script, a script file a shell, `source` or its own path runs, the command after `drive break-glass … --` and the one `xargs` runs, each word expanded with the variables set before it."""
    for written in parsed(text):
        yield from started([expanded(word, place.env) for word in written], place, depth)


def parsed(text: str) -> list[list[str]]:
    """The commands of shell text, line by line where the whole text does not parse, so one unclosed quote hides no other line."""
    try:
        return templates(runnable(text))
    except ValueError:
        return [group for line in text.splitlines() if line.strip() != text.strip() for group in parsed(line)]


def started(words: list[str], place: Place, depth: int) -> Iterator[list[str]]:
    words = unwrapped(words, place)
    if not words:
        return
    if words[0] == "cd":
        place.cwd = place.cwd / Path(words[1]).expanduser() if words[1:] else Path.home()
        return
    if words[0] == "export":
        place.env |= dict(word.split("=", 1) for word in words[1:] if ASSIGNMENT.fullmatch(word))
        return
    yield words
    if Path(words[0]).name in SHELLS and "-c" in words[:-1]:
        yield from reached(words[words.index("-c") + 1], Place(place.cwd, dict(place.env), place.script, place.seen), depth + 1)
    elif depth < FOLLOWED_DEPTH and (script := script_file(words, place)):
        yield from reached(located(script), Place(place.cwd, dict(place.env), script, place.seen | {script}), depth + 1)


def located(script: Path) -> str:
    """A script's text with each `$(cd "$(dirname "$0")/<path>" && pwd)` written as the folder it names, since the shell words split a substitution nested in quotes at its parentheses."""
    return SCRIPT_FOLDER.sub(lambda found: str((script.parent / found[1].lstrip("/")).resolve()), script.read_text(errors="replace"))


def json_field(value: str, place: Place) -> str:
    """The string a `$(jq -r '.<field>' <file>)` value reads from that JSON file, such as `tools/artifact.sh`'s bucket from `tools/artifacts.json`; any other value, or one the file holds no string for, as written."""
    if not (found := JSON_FIELD.fullmatch(value)) or not (path := place.cwd / found[2]).is_file():
        return value
    try:
        read = reduce(lambda node, key: node[key], found[1].split(".")[1:], json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError):
        return value
    return read if isinstance(read, str) else value


def unwrapped(words: list[str], place: Place) -> list[str]:
    """A command's own words, past the assignments it makes, shell keywords, wrappers and their options, and `drive break-glass` up to its `--`; a variable assigned a command's output, which `templates` splits at `$(` and leaves as `$`, stays as written."""
    while words:
        head = Path(words[0]).name
        if ASSIGNMENT.fullmatch(words[0]):
            name, value = words[0].split("=", 1)
            place.env[name] = f"${name}" if value == "$" else json_field(value, place)
            words = words[1:]
        elif words[0] in KEYWORDS:
            words = words[1:]
        elif head in WRAPPERS or head in OPTIONED:
            words = past_options(words[1:], OPTIONED.get(head, set()))
            words = words[1:] if head == "timeout" else words
        elif verb(words)[:2] == ["drive", "break-glass"] and "--" in words:
            words = words[words.index("--") + 1 :]
        else:
            break
    return words


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


def script_file(words: list[str], place: Place) -> Path | None:
    """The shell script file a command runs, by a shell, `source` or its own path, found from the folder it runs in or, inside a script, beside that script; None when it runs none, it is not on disk or was read on the way here."""
    name = Path(words[0]).name
    if name in SHELLS:
        named = next(iter(past_options(words[1:], {"-o"})), None)
    elif name in SOURCES:
        named = words[1] if words[1:] else None
    else:
        named = words[0] if "/" in words[0] else None
    if named is None:
        return None
    path = Path(named).expanduser()
    found = next((candidate for candidate in (place.cwd / path, place.script and place.script.parent / path.name) if candidate and candidate.is_file()), None)
    if found is None or found in place.seen or (name not in SHELLS | SOURCES and not shell_shebang(found)):
        return None
    return found


def shell_shebang(path: Path) -> bool:
    with path.open(errors="replace") as file:
        first = file.readline()
    return first.startswith("#!") and any(Path(word).name in SHELLS for word in first[2:].split())


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


def sandsql_reach(words: list[str]) -> tuple[str, str] | None:
    """The `kubectl` step and SandSQL namespace through which a command reaches a team's SandSQL databases: an exec, attach, copy, debug session or port-forward into a SandSQL fleet's namespace, or a read of a secret there, such as the blobstore's root key."""
    if Path(words[0]).name != "kubectl" or (namespace := option(words, "-n") or option(words, "--namespace")) not in SANDSQL_NAMESPACES:
        return None
    named, rest = [], iter(words[1:])
    for word in rest:
        if word == "--":
            break
        if word in KUBECTL_VALUED:
            next(rest, None)
        elif not word.startswith("-"):
            named.append(word)
    if named[:1] and named[0] in SANDSQL_REACHES:
        return f"kubectl {named[0]}", namespace
    if any(word.split("/")[0] in SECRETS for word in named[1:]):
        return f"kubectl {named[0]} secret", namespace
    return None


def remote_location(tool: str, word: str) -> str | None:
    if word.startswith("-"):
        return None
    if tool == "azcopy":
        return "/".join(found.groups()) if (found := AZURE_URL.fullmatch(word)) else None
    found = (RCLONE_PATH if tool == "rclone" else BUCKET_URI).fullmatch(word)
    return found[1] if found and found[1] else None


def team_data(command: str, cwd: str) -> agents.Unjudged | None:
    """Why a drive job's call must not run, refused by its shape alone: it, a script it runs, or a command it starts through `drive break-glass` or `xargs`, reads or writes a team's data, a key under `iris/teams/`, or a bucket or folder Forge-AI/monorepo's `release/team-buckets.generated.json` lists at origin/dev of the checkout it runs in or a location holding one, any bucket when that checkout holds no such list, or it opens a team's SandSQL databases through `kubectl`."""
    place = Place(Path(cwd))
    for words in reached(command, place):
        for location in bucket_locations(words):
            if TEAM_AREA.search(location):
                return agents.Unjudged(TEAM_DATA.format(location=location, route=team_route(location)))
            if (listed := team_buckets(place.cwd)) is None:
                return agents.Unjudged(UNLISTED.format(location=location))
            if any(f"{location.rstrip('/')}/".startswith(entry) or entry.startswith(f"{location.rstrip('/')}/") for entry in listed):
                return agents.Unjudged(TEAM_DATA.format(location=location, route=team_route(location)))
        if reach := sandsql_reach(words):
            return agents.Unjudged(SANDSQL_DATA.format(step=reach[0], namespace=reach[1]))
    return None


@cache
def team_buckets(cwd: Path) -> tuple[str, ...] | None:
    done = subprocess.run(["git", "-C", str(cwd), "show", TEAM_BUCKETS], capture_output=True, text=True, check=False)
    return None if done.returncode else tuple(json.loads(done.stdout))


def team_route(location: str) -> str:
    """The grant a pass box reads a team's bucket prefix under: `runs_read` for a team's `runs/`, and none, so `BLOCKED`, for every other prefix."""
    found = TEAM_AREA.search(location)
    if found and found[2] == "runs":
        return RUNS_ROUTE.format(team=found[1])
    return NO_ROUTE.format(prefix=f"{found[0]}/" if found else location)


def datadog_cli(command: str, cwd: str) -> str | None:
    """Why a drive session's call must not run: it, a script it runs, or a command it starts through `drive break-glass` or `xargs`, reads Datadog through the `pup` command rather than the Datadog MCP."""
    return DATADOG_READ if any(Path(words[0]).name == DATADOG_CLI for words in reached(command, Place(Path(cwd)))) else None


def role_of(session: str | None = None, terminal: str | None = None, alias: str | None = None) -> tuple[dict, str] | None:
    """The drive and role whose recorded session, terminal or cci alias this is."""
    for name in state.names():
        data = state.load(name)
        for role, held in state.bound_sessions(data).items():
            project = role.removeprefix("sub-coordinator ") if role.startswith("sub-coordinator ") else None
            if (session and held["session"] == session) or (terminal and held["terminal"] == terminal) or (alias and state.alias(name, role, project) == alias):
                return data, role
    return None


def hidden(held: dict) -> str | None:
    """The command that shows a role's terminal Orca only hides, None when it shows or is gone."""
    try:
        if not held["terminal"] or not orca.terminal(held["terminal"])["orphaned"]:
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


def deny_reason(payload: dict) -> str | None:
    """Why a drive session's or drive agent's call must not run: it, or an Orca lane's, reads or writes a team's bucket, blobs or SandSQL databases where it runs, it reads Datadog through `pup` rather than the Datadog MCP, a designer pushes, a builder, on this Mac or a Sprite, opens its PR while his open cards hold its job, a read takes a stale version's cached skill page, or a session is resumed while it runs, from a shell-ending command, or ended mid-incident, a call retries past a failing check, or a watchdog agent runs anything but its reads and two acts; why any session's call restarts the hook helper he never approved on a card; and why Claude Code's permission prompt on a drive agent's or an Orca lane's call to any tool gets a refusal rather than an answer nobody gives."""
    call = payload["tool_input"]
    session = payload.get("session_id") or ""
    drives = state.session_drives(session)
    kind = payload.get("agent_type") or ""
    if payload.get("hook_event_name") == "PermissionRequest":
        return UNSEEN_PROMPT if os.environ.get(state.REMOTE_LANE_ENV) or (payload.get("agent_id") and (drives or kind.startswith(AGENTS))) else None
    if payload["tool_name"] != "Bash":
        return cached_page(call.get("file_path") or call.get("path") or "") if drives else None
    found = commands(runnable(call["command"]))
    if os.environ.get(state.WATCHDOG_ENV):
        return watchdog_call(found)
    if (drives or kind.startswith(AGENTS) or os.environ.get(state.REMOTE_LANE_ENV)) and (touched := team_data(call["command"], payload.get("cwd") or os.getcwd())):
        return touched
    if not drives and os.environ.get(state.REMOTE_LANE_ENV):
        return lane_held(found)
    if not drives and not kind.startswith(AGENTS):
        return helper_install(found, drives)
    scripts = terminal_scripts(found)
    reasons = (
        lambda: datadog_cli(call["command"], payload.get("cwd") or os.getcwd()),
        lambda: designer_push(found) if kind == DESIGNER else None,
        lambda: bare_pr(found) if kind == BUILDER else None,
        lambda: held_pr(found, payload.get("agent_id") or "", drives) if kind == BUILDER else None,
        lambda: cached_page(call["command"]) if drives else None,
        lambda: shell_after_claude(scripts),
        lambda: live_resume(found + [words for inner in scripts for words in inner]),
        lambda: mid_incident(found),
        lambda: retried(found),
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
