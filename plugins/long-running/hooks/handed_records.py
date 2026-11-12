import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state


def main() -> None:
    name, _, handler = os.environ[state.HANDLER_ENV].partition("/")
    if briefs := agents.take_handed(name, handler):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": agents.handed(briefs)}}))


if __name__ == "__main__":
    main()
