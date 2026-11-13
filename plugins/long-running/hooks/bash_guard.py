import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, github, install, land, orca, state, view
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
LANE_HELD_SECONDS = 540
CACHED_PAGE = re.compile(r"plugins/cache/([^/\s'\"]+)/long-running/([^/\s'\"]+)/skills/long-running/reference/")
RERUNS = (("ci", "bk", "rerun"), ("bk", "job", "retry"), ("bk", "build", "rebuild"), ("gh", "run", "rerun"))
FLAKE = "--flake"
BREW_RESTARTS = {"upgrade", "reinstall"}
RESUME_TIMEOUT = 20000
UNSEEN_PROMPT = (
    "refused: Claude Code asked for permission to run this command, and nobody watches a drive agent's terminal, so the"
    " prompt would hold your job unseen for hours. Bypass permissions mode still asks when Claude Code's parser cannot"
    " read a command, such as one with a zsh expansion flag like `${(s:,:)tables}`: write the command to a script in"
    " your brief's Scratch folder, with the Write tool or a quoted heredoc `cat > <scratch>/<name>.sh <<'EOF'` … `EOF`,"
    " and run it as `zsh <scratch>/<name>.sh`. When the command removes files outside your worktree or Scratch folder,"
    " run nothing and return BLOCKED naming it"
)
FORGE_LOCATIONS = (
    "frg-core-usw2-artifacts-internal-release-artifacts/",
    "frg-core-usw2-audit-datadog-log-archive/",
    "frg-core-usw2-root-pulumi-state/",
    "frg-plat-usw2-prod-microvm-artifacts/pass-box/",
    "frg-plat-usw2-staging-microvm-artifacts/pass-box/",
)
COPIES = {"cp", "mv", "sync", "rsync", "copy", "copyto", "move", "moveto"}
LISTS = {
    "ls", "cat", "du", "head", "stat", "hash", "presign", "signurl", "sign-url", "objects", "lsd", "lsf", "lsl", "lsjson",
    "ncdu", "size", "tree", "mount", "serve", "md5sum", "sha1sum", "hashsum", "check", "list", "download", "download-batch", "show",
}
SERVICES = {"aws": ("s3",), "gcloud": ("storage",), "az": ("storage", "blob")}
S3API_READS = re.compile(r"(?:get|head|select)-object|list-object")
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
TEAM_AREA = re.compile(r"iris/teams/([^/\s]+)/([^/\s]+)")
TEAM_READ = (
    "refused: this reads `{location}` where the job runs, and a drive job reads a team's data only with a service account"
    ' on a pass box, never on this Mac, a Sprite or after `drive break-glass`, by his words "it shouldnt be reading iris'
    ' blobs at all locally only with a service account on a pass box". {route} Only {forge} read where a job runs, since'
    " they hold no team's data: a box's binary goes under `pass-box/<purpose>/` in its env's MicroVM artifacts bucket, and"
    " the box fetches it through a presigned URL"
)
RUNS_ROUTE = (
    "The one such read is `iris/teams/<team>/runs/` of a team the plat-prod cluster serves: launch a box with"
    " `AWS_PROFILE=plat-prod tools/ci pass-box launch --purpose <job> --type <instance type> --delete-after <YYYY-MM-DD>`,"
    " then run the read and the work that needs it there with `AWS_PROFILE=plat-prod aws ssm send-command --region"
    " us-west-2 --instance-ids <id> --document-name AWS-RunShellScript --parameters file://<scratch>/<name>.json`, whose"
    ' commands load the runs reader\'s grant first, `eval "$(./ci runs-reader grant --team {team})"`, and copy no team data'
    " back; docs/pass-boxes.md in Forge-AI/monorepo has each step. A team another cluster serves, a staging read, or a"
    " `./ci runs-reader grant` that refuses, as it does until Forge-AI/monorepo#55460 and #55533 are live, has no route:"
    " return `BLOCKED` naming the prefix, the team and its refusal."
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
    " through `execute_code`"
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


def held(job_id: str, cards: list[str]) -> str:
    return (
        f"refused: {job_id}'s work waits on his answer to {', '.join(cards)}, and this ship would open its PR, which goes"
        " up only once he answers each, since work does not land with a question of his on it unanswered: push the"
        " branch with `ccx vcs ship --no-pr` and return `BUILT <branch>, waits on <cards>`; the watch says FREE when"
        " the last is answered"
    )


def held_pr(found: list[list[str]], agent: str, drives: list[str]) -> str | None:
    """Why a builder's `ccx vcs ship` on this Mac may not open its PR: his open cards hold its job."""
    if not opens_pr(found):
        return None
    for name in drives:
        data = state.load(name)
        if (job_id := state.agent_job(data, agent)) and (cards := land.ship_held(data, job_id, view.live_cards(name))):
            return held(job_id, cards)
    return None


def lane_held(found: list[list[str]]) -> str | None:
    """Why a Sprite lane's `ccx vcs ship` may not open its PR: `drive job held`, which the Sprite's `drive` asks of the coordinator's Mac, names his open cards holding the lane's job."""
    if not opens_pr(found):
        return None
    try:
        done = subprocess.run(["drive", "job", "held", "--json"], capture_output=True, text=True, timeout=LANE_HELD_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"refused: `drive job held` could not say whether his open cards hold this lane's PR: {error}; ship again once it answers"
    if done.returncode:
        return f"refused: `drive job held` could not say whether his open cards hold this lane's PR: {(done.stderr or done.stdout).strip()}; ship again once it answers"
    answer = json.loads(done.stdout)
    return held(answer["id"], answer["cards"]) if answer["cards"] else None


def cached_page(text: str) -> str | None:
    if not (cached := CACHED_PAGE.search(text)):
        return None
    if (current := install.installed(f"long-running@{cached[1]}")["version"]) == cached[2]:
        return None
    return (
        f"refused: this reads the skill's pages from long-running {cached[2]}'s copy in Claude Code's plugin cache, and"
        f" {current} is installed now; a cached copy goes stale with each install. Read a page's rows with"
        " `drive manual <page> <row>`, which prints the installed rows"
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
        f" {card}; run it once `drive answer` records his pick"
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
        yield from reached(script.read_text(errors="replace"), Place(place.cwd, dict(place.env), script, place.seen | {script}), depth + 1)


def unwrapped(words: list[str], place: Place) -> list[str]:
    """A command's own words, past the assignments it makes, shell keywords, wrappers and their options, and `drive break-glass` up to its `--`."""
    while words:
        head = Path(words[0]).name
        if ASSIGNMENT.fullmatch(words[0]):
            name, value = words[0].split("=", 1)
            place.env[name] = value
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


def bucket_reads(words: list[str]) -> list[str]:
    """The bucket or blob locations a command reads, as `<bucket>/<key>`: each it lists, prints, presigns or downloads and each source it copies from, through the AWS, Google Cloud or Azure CLI, s5cmd, gsutil, rclone or azcopy."""
    tool = Path(words[0]).name
    if tool == "aws" and "s3api" in words:
        operation = next((word for word in words[words.index("s3api") + 1 :] if not word.startswith("-")), "")
        named = "--copy-source" if operation in {"copy-object", "upload-part-copy"} else "--bucket" if S3API_READS.match(operation) else None
        return [location for location in [named and option(words, named)] if location]
    service = SERVICES.get(tool, ())
    if tool not in {"aws", "s5cmd", "gsutil", "gcloud", "rclone", "azcopy", "az"} or any(name not in words for name in service):
        return []
    after = words[words.index(service[-1]) + 1 :] if service else words[1:]
    action = next((word for word in after if word in COPIES | LISTS), None)
    if action is None:
        return []
    if tool == "az":
        return [f"{option(words, '--account-name') or 'azure'}/{option(words, '--container-name') or option(words, '-c') or ''}"]
    named = [(index, location) for index, word in enumerate(after) if (location := remote_location(tool, word))]
    if action in COPIES:
        named = [(index, location) for index, location in named if any(word == "-" or not word.startswith("-") for word in after[index + 1 :])]
    return [location for _, location in named]


def remote_location(tool: str, word: str) -> str | None:
    if word.startswith("-"):
        return None
    if tool == "azcopy":
        return "/".join(found.groups()) if (found := AZURE_URL.fullmatch(word)) else None
    found = (RCLONE_PATH if tool == "rclone" else BUCKET_URI).fullmatch(word)
    return found[1] if found and found[1] else None


def team_read(command: str, cwd: str) -> str | None:
    """Why a drive job's call must not run: it, a script it runs, or a command it starts through `drive break-glass` or `xargs`, reads a bucket or blob outside a Forge build, state or log bucket and the `pass-box/` folder of a pass box env's MicroVM artifacts bucket, where a job puts the binary its box fetches through a presigned URL, so a team's data, a customer's own bucket included, whose name no list here holds."""
    for words in reached(command, Place(Path(cwd))):
        if location := next((found for found in bucket_reads(words) if not f"{found}/".startswith(FORGE_LOCATIONS)), None):
            forge = [f"`{place}`" for place in FORGE_LOCATIONS]
            return TEAM_READ.format(location=location, route=team_route(location), forge=f"{', '.join(forge[:-1])} and {forge[-1]}")
    return None


def team_route(location: str) -> str:
    """The route his words leave a read of a team's data: the runs reader on a plat-prod pass box for a team's `runs/`, and `BLOCKED` for every other prefix."""
    found = TEAM_AREA.search(location)
    if found and found[2] == "runs":
        return RUNS_ROUTE.format(team=found[1])
    return NO_ROUTE.format(prefix=f"{found[0]}/" if found else location)


def datadog_cli(command: str, cwd: str) -> str | None:
    """Why a drive session's call must not run: it, a script it runs, or a command it starts through `drive break-glass` or `xargs`, reads Datadog through the `pup` command rather than the Datadog MCP."""
    return DATADOG_READ if any(Path(words[0]).name == DATADOG_CLI for words in reached(command, Place(Path(cwd)))) else None


def role_of(session: str | None = None, terminal: str | None = None) -> tuple[dict, str] | None:
    """The drive and role whose recorded session or terminal this is."""
    for name in state.names():
        data = state.load(name)
        for role, held in state.bound_sessions(data).items():
            if (session and held["session"] == session) or (terminal and held["terminal"] == terminal):
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
    """Why a drive session's or drive agent's call must not run: it, or an Orca lane's, reads a team's bucket or blobs where it runs, it reads Datadog through `pup` rather than the Datadog MCP, a designer pushes, a builder, on this Mac or a Sprite, opens its PR while his open cards hold its job, a read takes a stale version's cached skill page, or a session is resumed while it runs, from a shell-ending command, or ended mid-incident, a call retries past a failing check; why any session's call restarts the hook helper he never approved on a card; and why Claude Code's permission prompt on a drive agent's or an Orca lane's command gets a refusal rather than an answer nobody gives."""
    call = payload["tool_input"]
    session = payload.get("session_id") or ""
    drives = state.session_drives(session)
    kind = payload.get("agent_type") or ""
    if payload.get("hook_event_name") == "PermissionRequest":
        return UNSEEN_PROMPT if os.environ.get(state.REMOTE_LANE_ENV) or (payload.get("agent_id") and (drives or kind.startswith(AGENTS))) else None
    if payload["tool_name"] != "Bash":
        return cached_page(call.get("file_path") or call.get("path") or "") if drives else None
    found = commands(runnable(call["command"]))
    if (drives or kind.startswith(AGENTS) or os.environ.get(state.REMOTE_LANE_ENV)) and (read := team_read(call["command"], payload.get("cwd") or os.getcwd())):
        return read
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
    print(json.dumps({"hookSpecificOutput": decided}))


if __name__ == "__main__":
    main()
