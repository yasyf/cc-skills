import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state
from drive.shell import runnable, simple_commands

LANE_CI = ("tools/ci", "./tools/ci")
CI_WORD = re.compile(r"(?<![\w./-])(?:\./)?tools/ci(?![\w./-])")
CI_SOURCES = ("go/ci", "tools/ci")
SPRITE_TIP = Path.home() / ".ci-tip"
SPRITE_TIP_FRESH = 600
SPRITE_TIP_PATHS = ("tools", "go/ci", "go/llmclient", "infra/ci")
TIP_REF = "refs/ci-tip/head"


def git(lane: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(lane), *args], capture_output=True, text=True, check=False)


def checked(lane: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(lane), *args], capture_output=True, text=True, check=True).stdout.strip()


def changes_ci(lane: Path, tip: Path) -> bool:
    """Whether the lane's tree, committed or not, changes tools/ci or the go/ci it builds since it parted from the tip, or from the base branch a shallow Sprite clone was cut at when its history never meets the tip's; a lane whose fork point neither finds counts as changing them, so it keeps its own copy."""
    head = git(tip, "rev-parse", "HEAD").stdout.strip()
    bases = (git(lane, "merge-base", "HEAD", ref).stdout.strip() for ref in (head, f"refs/remotes/{base_branch(lane)}"))
    if not (base := next(filter(None, bases), "")):
        return True
    return git(lane, "diff", "--quiet", base, "--", *CI_SOURCES).returncode != 0


def base_branch(lane: Path) -> str:
    """The branch lanes of the lane's repository are cut from, as its .orca.json names it, such as origin/dev."""
    return json.loads((lane / ".orca.json").read_text())["baseBranch"]


def sprite_tip(lane: Path) -> Path:
    """~/.ci-tip, a worktree of the lane's clone detached at its base branch's head holding only what tools/ci reads from its own tree, the go/ci and llmclient sources its binary key hashes, tools and infra/ci; fetched and moved there when it is older than SPRITE_TIP_FRESH, one process at a time, so every lane on the Sprite runs the ci the base branch builds."""
    stamp = SPRITE_TIP.with_name(".ci-tip.fetched")
    with SPRITE_TIP.with_name(".ci-tip.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        if stamp.is_file() and time.time() - stamp.stat().st_mtime < SPRITE_TIP_FRESH:
            return SPRITE_TIP
        remote, _, branch = base_branch(lane).partition("/")
        checked(lane, "fetch", "-q", "--depth", "1", remote, f"+refs/heads/{branch}:{TIP_REF}")
        if not (SPRITE_TIP / ".git").exists():
            checked(lane, "worktree", "add", "-q", "--no-checkout", "--detach", str(SPRITE_TIP), TIP_REF)
        checked(SPRITE_TIP, "reset", "-q", "--soft", TIP_REF)
        checked(SPRITE_TIP, "checkout", "-q", "HEAD", "--", *SPRITE_TIP_PATHS)
        stamp.touch()
    return SPRITE_TIP


def tip_ci(command: str, cwd: str, drive: str) -> str | None:
    """The command with each `tools/ci` it runs from the lane's tree pointed at the drive's checkout, which the drive keeps at the default branch's head, or on a Sprite at sprite_tip, so a lane cut days ago runs the ci the release pipeline runs; None when it runs no such word, the lane holds no drive checkout of its repository, or the lane itself changes tools/ci or go/ci, whose own copy it is testing."""
    if not any(words and words[0] in LANE_CI for words in simple_commands(runnable(command))):
        return None
    lane = Path(cwd)
    if not (lane / "tools" / "ci").is_file():
        return None
    tip = sprite_tip(lane) if os.environ.get(state.REMOTE_LANE_ENV) else state.drive_checkout(drive, cwd)
    if tip.resolve() == lane.resolve() or not (tip / "tools" / "ci").is_file() or changes_ci(lane, tip):
        return None
    return CI_WORD.sub(str(tip / "tools" / "ci"), command)


def main() -> None:
    payload = json.load(sys.stdin)
    call = payload["tool_input"]
    drive = (os.environ.get(state.JOB_ENV) or "").split("/")[0]
    if not (made := tip_ci(call.get("command") or "", payload.get("cwd") or os.getcwd(), drive)):
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": call | {"command": made}}}))


if __name__ == "__main__":
    main()
