import json
import re
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, github, install, land, orca, state, view
from drive.orca import OrcaError
from drive.shell import runnable, simple_commands

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


def held_pr(found: list[list[str]], agent: str, drives: list[str]) -> str | None:
    """Why a builder's `ccx vcs ship` may not open its PR: his open cards hold its job, since work does not land with a question of his on it unanswered; a ship with `--no-pr` or `--dry-run`, or one whose job already has an open PR it pushes to, opens none and passes."""
    if not any(tuple(verb(words)[:3]) == SHIP and not PUSH_ONLY & set(words) for words in found):
        return None
    for name in drives:
        data = state.load(name)
        if not (job_id := state.agent_job(data, agent)):
            continue
        if any(data["prs"].get(state.pr_key(url), {}).get("state") == "open" for url in data["jobs"][job_id]["prs"]):
            return None
        if cards := land.open_cards(data, job_id, [], view.live_cards(name)):
            return (
                f"refused: {job_id}'s work waits on his answer to {', '.join(cards)}, and this ship would open its PR, which goes"
                " up only once he answers each, since work does not land with a question of his on it unanswered: push the"
                " branch with `ccx vcs ship --no-pr` and return `BUILT <branch>, waits on <cards>`; the watch says FREE when"
                " the last is answered"
            )
    return None

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
            f" with {FLAKE} or a rerun. Read the failed job's own output with `tools/ci bk log <job>`, or `gh run view <run>"
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
    """Why a drive session's or drive agent's call must not run: a designer pushes, a builder opens its PR while his open cards hold its job, a read takes a stale version's cached skill page, or a session is resumed while it runs, from a shell-ending command, or ended mid-incident, a call retries past a failing check; why any session's call restarts the hook helper he never approved on a card; and why Claude Code's permission prompt on a drive agent's command gets a refusal rather than an answer nobody gives."""
    call = payload["tool_input"]
    session = payload.get("session_id") or ""
    drives = state.session_drives(session)
    kind = payload.get("agent_type") or ""
    if payload.get("hook_event_name") == "PermissionRequest":
        return UNSEEN_PROMPT if payload.get("agent_id") and (drives or kind.startswith(AGENTS)) else None
    if payload["tool_name"] != "Bash":
        return cached_page(call.get("file_path") or call.get("path") or "") if drives else None
    found = commands(runnable(call["command"]))
    if not drives and not kind.startswith(AGENTS):
        return helper_install(found, drives)
    scripts = terminal_scripts(found)
    reasons = (
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
