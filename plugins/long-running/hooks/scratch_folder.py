import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, brief, state

AGENTS = "long-running:"


def named_job(payload: dict) -> tuple[str, dict, str, str] | None:
    """The drive, its state, the job its brief's Drive line names, in its prompt or the brief file the prompt names, and that brief, when the drive knows the job."""
    text = brief.carried(payload["tool_input"].get("prompt") or "")
    match = brief.BRIEF_JOB.search(text)
    names = state.session_drives(payload["session_id"]) if match and payload.get("session_id") else []
    if not names:
        return None
    data = state.load(names[0])
    return (names[0], data, match[1], text) if match[1] in data["jobs"] else None


def deny_reason(payload: dict) -> str | None:
    """Why a drive session may not start this agent: the job its brief names is on an order another session of the drive holds, which for a builder serving drive tooling started by the meta session names the brief that gives it the order, or its brief lacks what the job's kind needs."""
    if (named := named_job(payload)) is None:
        return None
    name, data, job_id, text = named
    order_id = data["jobs"][job_id]["order"]
    role = state.session_role(data, payload["session_id"])
    held = state.held_elsewhere(data, order_id, role)
    if held and role == "meta" and brief.SERVES_TOOLING.search(text):
        held = f"{order_id} is the {state.title(data['orders'][order_id]['owner'])}'s order, but a builder serving drive tooling is the meta session's alone: {brief.gives_tooling(job_id, order_id)}, then start it again"
    refused = held or brief.brief_refusal(name, data, job_id, text)
    return f"refused, no agent started: {refused}" if refused else None


def scratch_folder(payload: dict) -> Path | None:
    """The folder a drive agent's prompt names as its Scratch, which must exist before the agent's first write."""
    tool_input = agents.shaped(payload)
    if not str(tool_input.get("subagent_type") or "").startswith(AGENTS):
        return None
    return agents.scratch(tool_input.get("prompt") or "")


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    if reason := deny_reason(payload):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))
        return
    if folder := scratch_folder(payload):
        folder.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
