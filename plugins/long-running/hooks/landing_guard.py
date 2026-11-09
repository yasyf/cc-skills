import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state
from drive.cli import tap_verdict

GUARDED_OWNER = "forge-ai"
ADMIN_REASON = "Admin merges bypass required review; never on Forge-AI repos"
TAP_HINT = "Draw his PR card with slack_dm_status tap_prs, or record an exemption with drive pr set --exempt"
LANDING = (("gh", "pr", "merge"), ("gh", "stack", "merge"), ("ccx", "vcs", "merge"), ("ci", "trunk", "submit"), ("stack-enqueue",))
LABEL_EDIT = ("gh", "pr", "edit")
VALUE_FLAGS = {
    "-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject", "-A", "--author-email",
    "--match-head-commit", "--add-label", "--remove-label", "--pr", "--base", "-B", "--title", "-T",
}
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


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


def without_env(words: list[str]) -> list[str]:
    while words and ENV_ASSIGNMENT.fullmatch(words[0]):
        words = words[1:]
    return words


def option(args: list[str], *names: str) -> list[str]:
    values = []
    for index, word in enumerate(args):
        for name in names:
            if word == name and index + 1 < len(args):
                values.append(args[index + 1])
            elif word.startswith(f"{name}="):
                values.append(word.split("=", 1)[1])
    return values


def positionals(args: list[str]) -> list[str]:
    found, skip = [], False
    for word in args:
        if skip:
            skip = False
        elif word in VALUE_FLAGS:
            skip = True
        elif not word.startswith("-"):
            found.append(word)
    return found


def starts_with(words: list[str], prefix: tuple[str, ...]) -> bool:
    return (Path(words[0]).name, *words[1 : len(prefix)]) == prefix


def landing_args(words: list[str]) -> list[str] | None:
    for prefix in LANDING:
        if starts_with(words, prefix):
            return words[len(prefix) :]
    if starts_with(words, LABEL_EDIT):
        args = words[len(LABEL_EDIT) :]
        labels = ",".join(option(args, "--add-label")).split(",")
        return args if "merge" in (label.strip() for label in labels) else None
    return None


def repo_name(remote: str) -> str:
    owner, name = re.split(r"[/:]", remote.strip().removesuffix("/").removesuffix(".git"))[-2:]
    return f"{owner}/{name}"


def target_repo(args: list[str], cwd: str) -> str:
    if explicit := option(args, "-R", "--repo"):
        return repo_name(explicit[-1])
    return repo_name(run(["git", "remote", "get-url", "origin"], cwd))


def pr_ref(args: list[str]) -> str | None:
    return next(iter(option(args, "--pr") or positionals(args)), None)


def resolve_key(ref: str | None, repo: str, cwd: str) -> str:
    if ref and ref.isdigit():
        return f"{repo}#{ref}"
    view = ["gh", "pr", "view", ref, "-R", repo] if ref else ["gh", "pr", "view"]
    return state.pr_key(run([*view, "--json", "url", "-q", ".url"], cwd))


def tap_reason(key: str) -> str | None:
    known = [
        pr
        for name in state.names()
        for stored, pr in state.load(name)["prs"].items()
        if stored.lower() == key.lower()
    ]
    if not known:
        return None
    verdicts = [tap_verdict(pr) for pr in known]
    if any(may_land for may_land, _ in verdicts):
        return None
    return f"{key}: {verdicts[0][1]}. {TAP_HINT}"


def landing_reason(args: list[str], cwd: str) -> str | None:
    ref = pr_ref(args)
    key = state.pr_key(ref) if ref and state.PR_URL.search(ref) else None
    repo = key.split("#")[0] if key else target_repo(args, cwd)
    if repo.split("/")[0].lower() != GUARDED_OWNER:
        return None
    if "--admin" in args:
        return ADMIN_REASON
    return tap_reason(key or resolve_key(ref, repo, cwd))


def deny_reason(payload: dict) -> str | None:
    """Why a Bash call must not run: it lands a Forge-AI drive PR without his tap, or merges with --admin."""
    cwd = payload.get("cwd") or os.getcwd()
    for words in map(without_env, segments(payload["tool_input"]["command"])):
        if not words:
            continue
        if words[0] == "cd":
            cwd = str(Path(cwd, Path(words[1]).expanduser())) if len(words) > 1 else str(Path.home())
        elif (args := landing_args(words)) is not None and (reason := landing_reason(args, cwd)):
            return reason
    return None


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
