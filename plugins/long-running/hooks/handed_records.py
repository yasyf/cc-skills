import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state


def main() -> None:
    if started := os.environ.get(state.JOB_ENV):
        if pointed := agents.take_pointed(*started.split("/")[:2]):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": agents.pointed(pointed)}}))
        return
    tool = json.load(sys.stdin)["tool_name"]
    name, _, handler = os.environ[state.HANDLER_ENV].partition("/")
    briefs, entry = agents.take_handed(name, handler)
    late, overdue = agents.unposted(entry) or (None, False)
    if not (context := "\n".join(filter(None, [agents.handed(briefs) if briefs else None, late]))):
        return
    said = {"permissionDecision": "deny", "permissionDecisionReason": context} if overdue and tool not in agents.FIRST_LINE_TOOLS else {"additionalContext": context}
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"} | said}))


if __name__ == "__main__":
    main()
