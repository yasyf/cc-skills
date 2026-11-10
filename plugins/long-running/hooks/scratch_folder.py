import json
import re
import sys
from pathlib import Path

AGENTS = "long-running:"
SCRATCH = re.compile(r"Scratch:\s*(\S+)")


def scratch_folder(payload: dict) -> Path | None:
    """The folder a drive agent's prompt names as its Scratch, which must exist before the agent's first write."""
    tool_input = payload["tool_input"]
    if not str(tool_input.get("subagent_type") or "").startswith(AGENTS):
        return None
    match = SCRATCH.search(tool_input.get("prompt") or "")
    return Path(match.group(1).rstrip(".,;")).expanduser() if match else None


def main() -> None:
    folder = scratch_folder(json.load(sys.stdin))
    if folder:
        folder.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
