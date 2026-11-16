import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state


def main() -> None:
    if pointed := agents.take_pointed(*os.environ[state.JOB_ENV].split("/")[:2]):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": agents.pointed(pointed)}}))


if __name__ == "__main__":
    main()
