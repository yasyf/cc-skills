import json
import re
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, github, install, orca, settled, state
from drive.orca import OrcaError
from drive.shell import runnable, simple_commands

AGENTS = "long-running:"
DESIGNER = "long-running:designer"
BUILDER = "long-running:builder"
SHELLS = {"sh", "bash", "zsh", "dash", "fish"}
WRAPPERS = {"command", "exec", "nohup", "env", "time"}
VALUED = {"-C", "-R", "--repo", "--drive"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
PUSHES = (("gh", "pr", "create"), ("git", "push"), ("jj", "git", "push"), ("ccx", "vcs", "ship"), ("gt", "submit"))
CACHED_PAGE = re.compile(r"plugins/cache/([^/\s'\"]+)/long-running/([^/\s'\"]+)/skills/long-running/reference/")
RERUNS = (("ci", "bk", "rerun"), ("bk", "job", "retry"), ("bk", "build", "rebuild"), ("gh", "run", "rerun"))
FLAKE = "--flake"
HELPER = "capt-hook"
HELPER_REPO = "yasyf/captain-hook"
HELPER_RUNS = re.compile(r"helper v(\S+)")
HELPER_ASKED = "Captain-hook update, your Mac: install {release} now? It restarts the hook helper once"
INSTALL_NOW = "Install now"
EVERY_INSTALL = "Installs never need me"
UNSEEN_PROMPT = (
    "refused: Claude Code asked for permission to run this command, and nobody watches a drive agent's terminal, so the"
    " prompt would hold your job unseen for hours. Bypass permissions mode still asks when Claude Code's parser cannot"
    " read a command, such as one with a zsh expansion flag like `${(s:,:)tables}`: write the command to a script in"
    " your brief's Scratch folder, with the Write tool or a quoted heredoc `cat > <scratch>/<name>.sh <<'EOF'` … `EOF`,"
    " and run it as `zsh <scratch>/<name>.sh`. When the command removes files outside your worktree or Scratch folder,"
    " run nothing and return BLOCKED naming it"
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


def background_resume(found: list[list[str]]) -> str | None:
    if not any(verb(words)[:3] == ["drive", "job", "resume"] for words in found):
        return None
    return (
        "refused: `drive job resume` runs in the foreground with `timeout: 20000`, because a send returns in about 5"
        " seconds and a relaunch backgrounds itself at 20; run it again without run_in_background"
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
            f" with {FLAKE} or a rerun. Read the failed job's own output with `tools/ci bk log <job>`, or `gh run view <run>"
            " --log-failed` on GitHub Actions, fix the cause and push the fix; a test that flakes gets a flake fix of its own"
        )
    return None


def helper_install(found: list[list[str]], drives: list[str]) -> str | None:
    """Why a call may not run `capt-hook helper install`, which installs captain-hook's newest release and restarts the hook helper every session on this Mac shares: no answer of his picks Install now on the card asking to install that release, or the helper already runs it, so the call is a second restart he never approved."""
    if not any(named[index + 1 : index + 3] == ["helper", "install"] and re.split(r"[@=<>]", named[index])[0] == HELPER for words in found for named in [verb(words)] for index in range(len(named))):
        return None
    try:
        release = github.read("captain-hook's newest release", ["gh", "release", "view", "-R", HELPER_REPO, "--json", "tagName", "-q", ".tagName"], max_age="5m").strip().removeprefix("v")
        answers = json.loads(settled.ccn(["ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"], "", state.load(drives[0])["roles"]["coordinator"]["cwd"], "could not read his recorded answers"))
        runs = HELPER_RUNS.search(subprocess.run([HELPER, "helper", "status"], capture_output=True, text=True, timeout=30, check=False).stdout)
    except (github.GithubError, state.DriveError, OSError, subprocess.TimeoutExpired) as error:
        return f"refused: `capt-hook helper install` restarts the hook helper every session on this Mac shares, and whether he approved this one could not be read: {error}"
    if runs and runs[1] == release:
        return (
            f"refused: the hook helper already runs captain-hook {release}, the newest release, so `capt-hook helper install`"
            " only restarts it again, a restart every session on this Mac shares that he never approved; leave it running"
        )
    for answer in answers:
        picked = answer["body"].partition("\n")[0]
        if EVERY_INSTALL in picked or (INSTALL_NOW in picked and re.search(rf"\binstall {re.escape(release)} now\b", answer["title"])):
            return None
    return (
        f"refused: `capt-hook helper install` installs captain-hook {release}, the newest release, and restarts the hook"
        " helper every session on this Mac shares; he approves each such restart on its own card, and no answer of his picks"
        f' {INSTALL_NOW} for {release}. Ask him with `drive job ask`: "{HELPER_ASKED.format(release=release)}", options {INSTALL_NOW} (recommended),'
        f" {EVERY_INSTALL} and Not now, and run it once `drive answer` records his pick"
    )


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
        running = orca.parents()
        pid = next((pid for pid in orca.claude_pids(session) if pid in running), None)
        if pid is None:
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
    """Why a drive session's or drive agent's call must not run: a designer pushes, a read takes a stale version's cached skill page, `drive job resume` runs in the background, or a session is resumed while it runs, from a shell-ending command, or ended mid-incident, a call retries past a failing check, or installs the hook helper he never approved on a card; and why Claude Code's permission prompt on a drive agent's command gets a refusal rather than an answer nobody gives."""
    call = payload["tool_input"]
    session = payload.get("session_id") or ""
    drives = state.session_drives(session)
    kind = payload.get("agent_type") or ""
    if payload.get("hook_event_name") == "PermissionRequest":
        return UNSEEN_PROMPT if payload.get("agent_id") and (drives or kind.startswith(AGENTS)) else None
    if payload["tool_name"] != "Bash":
        return cached_page(call.get("file_path") or call.get("path") or "") if drives else None
    if not drives and not kind.startswith(AGENTS):
        return None
    text = runnable(call["command"])
    found = commands(text)
    scripts = terminal_scripts(found)
    reasons = (
        lambda: designer_push(found) if kind == DESIGNER else None,
        lambda: bare_pr(found) if kind == BUILDER else None,
        lambda: cached_page(call["command"]) if drives else None,
        lambda: background_resume(found) if call.get("run_in_background") else None,
        lambda: shell_after_claude(scripts),
        lambda: live_resume(found + [words for inner in scripts for words in inner]),
        lambda: mid_incident(found),
        lambda: retried(found),
        lambda: helper_install(found, drives) if drives else None,
    )
    return next(filter(None, (reason() for reason in reasons)), None)


def main() -> None:
    try:
        payload = agents.seen(json.load(sys.stdin))
        reason = deny_reason(payload)
    except ValueError:
        return
    if not reason:
        return
    if payload.get("hook_event_name") == "PermissionRequest":
        decided = {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": reason}}
    else:
        decided = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    print(json.dumps({"hookSpecificOutput": decided}))


if __name__ == "__main__":
    main()
