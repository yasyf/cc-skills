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
    json.load(sys.stdin)
    name, _, handler = os.environ[state.HANDLER_ENV].partition("/")
    briefs, entry = agents.take_handed(name, handler)
    late, _ = agents.unposted(entry) or (None, False)
    if not (context := "\n".join(filter(None, [agents.handed(briefs) if briefs else None, late]))):
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}))


if __name__ == "__main__":
    main()
