import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))

from drive import state


def skill_body(plugin: Path) -> str:
    """A long-running install's skill page, its frontmatter cut."""
    return (plugin / "skills" / "long-running" / "SKILL.md").read_text().split("\n---\n", 1)[1].lstrip("\n")


def respond(payload: dict) -> dict | None:
    """The newest skill page for a drive role's session once it compacts, as a SessionStart output of its own beside the role's block, since Claude Code shows any one hook string over 10,000 characters only as its first 2,000; none when the page the session loaded, the install the hook ran from by installed.py's LOADED_ENV, is the newest, since Claude Code's invoked_skills already brings back that page at a compaction, and a second copy only doubles it."""
    session = payload.get("session_id")
    if payload.get("agent_id") or not session or not state.session_drives(session):
        return None
    if (newest := skill_body(PLUGIN)) == skill_body(Path(os.environ["LONG_RUNNING_LOADED_PLUGIN"])):
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": newest}}


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
