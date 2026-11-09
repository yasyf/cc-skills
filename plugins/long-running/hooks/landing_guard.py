import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state
from drive.cli import tap_verdict

GUARDED_OWNER = "forge-ai"
MONOREPO = "Forge-AI/monorepo"
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


@dataclass(frozen=True)
class Shell:
    cwd: str
    env: dict[str, str]


@dataclass(frozen=True)
class Landing:
    path: tuple[str, ...]
    valued: frozenset[str]
    shorts: dict[str, str] = field(default_factory=dict)
    every_pr: bool = False
    repo: str | None = None
    label: str | None = None
    reads: tuple[str, ...] = ()


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
        label="merge",
    ),
    Landing(("gh", "stack", "merge"), frozenset({"repo", "merge-method"}), {"R": "repo"}),
    Landing(("ccx", "vcs", "merge"), frozenset({"repo", "match-head-commit", "red-on-main"}), {"R": "repo"}),
    Landing(("ci", "trunk", "submit"), frozenset({"priority"}), every_pr=True, repo=MONOREPO),
    Landing(("stack-enqueue",), frozenset(), every_pr=True, repo=MONOREPO, reads=("check", "status", "dequeue")),
)


def run(args: list[str], cwd: str) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, timeout=20).stdout.strip()


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command.replace("\n", ";"), posix=True, punctuation_chars=True)
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


def landing_of(words: list[str]) -> tuple[Landing, list[str], dict[str, list[str]]] | None:
    for landing in LANDINGS:
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
    if landing.label:
        labels = ",".join(options.get("add-label", [])).split(",")
        return landing.label in (label.strip() for label in labels)
    return True


def repo_name(remote: str) -> str:
    owner, name = re.split(r"[/:]", remote.strip().removesuffix("/").removesuffix(".git"))[-2:]
    return f"{owner}/{name}"


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


def judged_by(key: str) -> dict | None:
    """The drive's entry for a PR, from the newest-started drive that knows it."""
    known = [
        (state.parse_time(data["driver"]["started"]), pr)
        for data in map(state.load, state.names())
        for stored, pr in data["prs"].items()
        if stored.lower() == key.lower()
    ]
    return max(known, key=lambda pair: pair[0])[1] if known else None


def tap_reason(key: str) -> str | None:
    may_land, why = tap_verdict(judged_by(key))
    return None if may_land else f"{key}: {why}. {TAP_HINT}"


def landing_reason(landing: Landing, refs: list[str], options: dict[str, list[str]], shell: Shell) -> str | None:
    if not lands(landing, options):
        return None
    named = refs if landing.every_pr else refs[:1] or [None]
    repo = target_repo(landing, next(iter(named), None), options, shell)
    if repo.split("/")[0].lower() != GUARDED_OWNER:
        return None
    if switched_on(options, "admin"):
        return ADMIN_REASON
    selected = selected_repo(landing, options, shell)
    keys = (resolve_key(ref, repo, selected, shell.cwd) for ref in named)
    return next(filter(None, map(tap_reason, keys)), None)


def script_reason(script: str, shell: Shell) -> str | None:
    for segment in segments(script):
        words, run_in = command_words(segment, shell)
        if not words:
            continue
        reason = None
        if words[0] == "cd":
            shell = replace(shell, cwd=moved(shell.cwd, words[1]) if len(words) > 1 else str(Path.home()))
        elif words[0] == "export":
            shell = assigned(shell, words[1:])
        elif (inner := shell_script(words)) is not None:
            reason = script_reason(inner, run_in)
        elif found := landing_of(words):
            reason = landing_reason(*found, run_in)
        if reason:
            return reason
    return None


def deny_reason(payload: dict) -> str | None:
    """Why a Bash call must not run: it lands a Forge-AI drive PR without his tap, or merges with --admin."""
    env = {REPO_VARIABLE: os.environ[REPO_VARIABLE]} if REPO_VARIABLE in os.environ else {}
    return script_reason(payload["tool_input"]["command"], Shell(payload.get("cwd") or os.getcwd(), env))


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
        reason = deny_reason(json.load(sys.stdin))
    except Exception:
        return
    if reason:
        print(json.dumps(denial(reason)))


if __name__ == "__main__":
    main()
