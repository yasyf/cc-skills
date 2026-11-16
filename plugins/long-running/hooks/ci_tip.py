import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import providers, state
from drive.providers import LaneTool
from drive.shell import runnable, simple_commands


def git(lane: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(lane), *args], capture_output=True, text=True, check=False)


def changes_tool(lane: Path, tip: Path, tool: LaneTool) -> bool:
    """Whether the lane's tree, committed or not, changes the tool's sources since it parted from the tip the drive's checkout holds."""
    head = git(tip, "rev-parse", "HEAD").stdout.strip()
    base = git(lane, "merge-base", "HEAD", head).stdout.strip() or head
    return git(lane, "diff", "--quiet", base, "--", *tool.sources).returncode != 0


def tip_ci(command: str, cwd: str, drive: str, tool: LaneTool) -> str | None:
    """The command with each run of the repository's lane tool from the lane's tree pointed at the drive's checkout, which the drive keeps at the default branch's head, so a lane cut days ago runs the tool the release pipeline runs; None when it runs no such word, the lane holds no drive checkout of its repository (a Sprite, whose repository hook runs it instead), or the lane itself changes the tool's sources, whose own copy it is testing."""
    if not any(words and words[0] in tool.words for words in simple_commands(runnable(command))):
        return None
    lane = Path(cwd)
    if not (lane / tool.path).is_file():
        return None
    tip = state.drive_checkout(drive, cwd)
    if tip.resolve() == lane.resolve() or not (tip / tool.path).is_file() or changes_tool(lane, tip, tool):
        return None
    return tool.word.sub(str(tip / tool.path), command)


def runs_tree_tool(command: str, cwd: str) -> bool:
    """Whether the command runs a file of the lane's own tree by a relative path, which the repository's lane tool may be."""
    return any(words and "/" in words[0] and not words[0].startswith("/") and (Path(cwd) / words[0]).is_file() for words in simple_commands(runnable(command)))


def main() -> None:
    payload = json.load(sys.stdin)
    call = payload["tool_input"]
    drive = os.environ[state.JOB_ENV].split("/")[0]
    cwd = payload.get("cwd") or os.getcwd()
    command = call.get("command") or ""
    if not runs_tree_tool(command, cwd):
        return
    try:
        providers.default_head(Path(cwd))
    except providers.ProviderError:
        return
    try:
        tool = providers.at(Path(cwd)).repo.lane_tool()
    except providers.ProviderError as error:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": f"refused: this repository's providers failed to load, so the lane tool's checkout is unknown: {error}"}}))
        return
    if tool is None or not (made := tip_ci(command, cwd, drive, tool)):
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": call | {"command": made}}}))


if __name__ == "__main__":
    main()
