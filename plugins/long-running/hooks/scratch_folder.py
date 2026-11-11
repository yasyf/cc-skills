import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import brief, state

AGENTS = "long-running:"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")


def deny_reason(payload: dict) -> str | None:
    """Why a drive session may not start this agent: its brief's Drive line names a job whose order refuses that brief or model."""
    tool_input = payload["tool_input"]
    match = brief.BRIEF_JOB.search(tool_input.get("prompt") or "")
    names = state.session_drives(payload["session_id"]) if match and payload.get("session_id") else []
    if not names:
        return None
    data = state.load(names[0])
    if match[1] not in data["jobs"]:
        return None
    refused = brief.brief_refusal(names[0], data, match[1], tool_input["prompt"], tool_input.get("model"))
    return f"refused, no agent started: {refused}" if refused else None


def scratch_folder(payload: dict) -> Path | None:
    """The folder a drive agent's prompt names as its Scratch, which must exist before the agent's first write."""
    tool_input = payload["tool_input"]
    if not str(tool_input.get("subagent_type") or "").startswith(AGENTS):
        return None
    match = SCRATCH.search(tool_input.get("prompt") or "")
    return Path(match.group(1).rstrip(".,;")).expanduser() if match else None


def main() -> None:
    payload = json.load(sys.stdin)
    if reason := deny_reason(payload):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
        return
    if folder := scratch_folder(payload):
        folder.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
