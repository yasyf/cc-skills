import contextlib
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))

from drive import reads, render, state, watch


def role_context(session: str) -> str | None:
    """The role's context for the session holding one; a coordinator's session also gets the watch started unless it runs, and its cci channel pointed at the watch's records."""
    bound = state.session_drives(session)
    if not bound:
        return None
    data = state.load(bound[0])
    role = state.session_role(data, session)
    if role == "coordinator":
        watch.ensure(bound[0])
        with contextlib.suppress(reads.ReadError):
            watch.listen(bound[0], session)
    return render.context(data, role)


def skill_body() -> str:
    """The skill page of the long-running running this hook, the newest installed, its frontmatter cut: a compaction brings back the page the session loaded at its start."""
    return (PLUGIN / "skills" / "long-running" / "SKILL.md").read_text().split("\n---\n", 1)[1].lstrip("\n")


def respond(payload: dict) -> dict | None:
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id")
    text = role_context(session) if session else None
    if text is None:
        return None
    if payload.get("source") == "compact":
        text = f"{text}\n\n{skill_body()}"
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def main() -> None:
    try:
        output = respond(json.load(sys.stdin))
    except Exception:
        return
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
