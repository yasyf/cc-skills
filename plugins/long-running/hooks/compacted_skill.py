import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))

from drive import state


def skill_body() -> str:
    """The skill page of the long-running running this hook, the newest installed, its frontmatter cut: a compaction brings back the page the session loaded at its start."""
    return (PLUGIN / "skills" / "long-running" / "SKILL.md").read_text().split("\n---\n", 1)[1].lstrip("\n")


def respond(payload: dict) -> dict | None:
    """The newest skill page for a drive role's session once it compacts, as a SessionStart output of its own beside the role's block, since Claude Code shows any one hook string over 10,000 characters only as its first 2,000."""
    session = payload.get("session_id")
    if payload.get("agent_id") or not session or not state.session_drives(session):
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": skill_body()}}


def main() -> None:
    if os.environ.get(state.HANDLER_ENV):
        return
    try:
        output = respond(json.load(sys.stdin))
    except Exception:
        return
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
