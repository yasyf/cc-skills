import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import brief, state

DESIGNER = "long-running:designer"
BUILDER = "long-running:builder"
SCOPED = re.compile(r"^(?:Serves|Handoff):", re.MULTILINE)


def deny_reason(payload: dict) -> str | None:
    """Why a drive's coordinator or communicator may not start this agent: a named agent joins the agent team and never wakes on events, Fable runs only the coordinator and the design lane, and a builder whose brief says no scope builds work no order asked for."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    role = state.session_role(state.load(names[0]), session)
    call = payload["tool_input"]
    kind = call.get("subagent_type") or ""
    if call.get("name"):
        return (
            f"refused: the {role} starts no named agent, since agent teams are on and a named agent becomes a teammate that"
            " never wakes on events; start it again without `name`"
        )
    if "fable" in (call.get("model") or "").lower() and kind != DESIGNER:
        return (
            f"refused: Fable runs only the coordinator and {DESIGNER}; start {kind or 'this agent'} on its own default"
            " model, or `sonnet` for a one-fact lookup"
        )
    if kind == BUILDER and not SCOPED.search(brief.carried(call.get("prompt") or "")):
        return (
            "refused: a builder's brief, in its prompt or the brief file the prompt names, carries a `Serves:` line, `port of"
            " #N`, `drive tooling` or `his order`, or a `Handoff:` line for a PR event, so work no order asked for is caught"
            " before it starts; add the line, or start no builder when none of the three fits"
        )
    return None


def main() -> None:
    if reason := deny_reason(json.load(sys.stdin)):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
