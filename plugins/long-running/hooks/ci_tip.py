import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state
from drive.shell import runnable, simple_commands

LANE_CI = ("tools/ci", "./tools/ci")
CI_WORD = re.compile(r"(?<![\w./-])(?:\./)?tools/ci(?![\w./-])")
CI_SOURCES = ("go/ci", "tools/ci")


def git(lane: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(lane), *args], capture_output=True, text=True, check=False)


def changes_ci(lane: Path, tip: Path) -> bool:
    """Whether the lane's tree, committed or not, changes tools/ci or the go/ci it builds since it parted from the tip the drive's checkout holds."""
    head = git(tip, "rev-parse", "HEAD").stdout.strip()
    base = git(lane, "merge-base", "HEAD", head).stdout.strip() or head
    return git(lane, "diff", "--quiet", base, "--", *CI_SOURCES).returncode != 0


def tip_ci(command: str, cwd: str, drive: str) -> str | None:
    """The command with each `tools/ci` it runs from the lane's tree pointed at the drive's checkout, which the drive keeps at the default branch's head, so a lane cut days ago runs the ci the release pipeline runs; None when it runs no such word, the lane holds no drive checkout of its repository (a Sprite), or the lane itself changes tools/ci or go/ci, whose own copy it is testing."""
    if not any(words and words[0] in LANE_CI for words in simple_commands(runnable(command))):
        return None
    lane = Path(cwd)
    if not (lane / "tools" / "ci").is_file():
        return None
    tip = state.drive_checkout(drive, cwd)
    if tip.resolve() == lane.resolve() or not (tip / "tools" / "ci").is_file() or changes_ci(lane, tip):
        return None
    return CI_WORD.sub(str(tip / "tools" / "ci"), command)


def main() -> None:
    payload = json.load(sys.stdin)
    call = payload["tool_input"]
    drive = os.environ[state.JOB_ENV].split("/")[0]
    if not (made := tip_ci(call.get("command") or "", payload.get("cwd") or os.getcwd(), drive)):
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": call | {"command": made}}}))


if __name__ == "__main__":
    main()
