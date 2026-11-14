import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state


def main() -> None:
    agents.bind_handler(json.load(sys.stdin), os.environ[state.HANDLER_ENV])


if __name__ == "__main__":
    main()
