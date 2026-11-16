import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))

from drive import agents, orca, render, state, watch


def role_context(session: str) -> str | None:
    """The role's context for the session holding one, which is registered with cci again by its Start or resume line in the drive's checkout, so the subscription that line sets, its `--mute-kinds` included, holds after every start, resume and compaction; a coordinator's session, or a project's sub-coordinator's, also gets the drive's watch started unless it runs; cci's refusal ends the block, which a failed register must not lose."""
    bound = state.session_drives(session)
    if not bound:
        return None
    data = state.load(bound[0])
    role = state.session_role(data, session)
    project = state.session_project(data, session)
    context = render.context(data, role, project)
    agents.hold_rules(bound[0], session)
    if role == "coordinator":
        watch.ensure(bound[0])
    if state.scratch():
        return context
    held = data["subcoordinators"][project] if project else data["roles"][role]
    try:
        agents.register(bound[0], role, project, str(orca.checkout(bound[0], held["cwd"])), session)
    except state.DriveError as error:
        return f"{context}\n\n{error}"
    return context


def respond(payload: dict) -> dict | None:
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id")
    text = role_context(session) if session else None
    if text is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def open_item(handler_env: str) -> dict | None:
    """A pool handler's open item as the drive's rows hold it, its brief and each brief handed to it since, so a wake, resume or compaction that lost its context reads its work again from the rows; None while it rests."""
    name, _, handler = handler_env.partition("/")
    entry = state.saved(name)["handlers"].get(handler)
    if not entry or not entry["brief"]:
        return None
    briefs = "\n\n".join([entry["brief"], *entry["took"], *entry["handed"]])
    text = f"Your open item, as the drive's rows hold it, which you have not finished: handle it by your definition and end on its line.\n\n{briefs}"
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def main() -> None:
    if handler_env := os.environ.get(state.HANDLER_ENV):
        if output := open_item(handler_env):
            print(json.dumps(output))
        return
    payload = json.load(sys.stdin)
    try:
        output = respond(payload)
    except Exception:
        return
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
