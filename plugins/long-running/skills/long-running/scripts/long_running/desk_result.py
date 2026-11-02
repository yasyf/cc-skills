"""The one result record of a desk job: `result.json` in the job's directory.

`write` is the only writer and `read` the only reader. A record that parses is the verdict. A job that ends without one, on this machine or a Sprite, gets a `lost` failure from its wrapper, and a record still missing once the wrapper is gone reads as the same failure, so the runner fails and requeues the pass at once and never waits on it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

RESULT = "result.json"
LOST = "no_result"


def stage(directory: Path, text: str) -> Path:
    staged = directory / f".{RESULT}.{os.getpid()}"
    with staged.open("w") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    return staged


def write(directory: Path, text: str) -> None:
    stage(directory, text).replace(directory / RESULT)


def parse(text: str) -> dict | None:
    try:
        outcome = json.loads(text)
    except json.JSONDecodeError:
        return None
    return outcome if isinstance(outcome, dict) else None


def lost(directory: Path, why: str) -> None:
    staged = stage(directory, json.dumps({"error": why, LOST: True}) + "\n")
    try:
        os.link(staged, directory / RESULT)
    except FileExistsError:
        pass
    finally:
        staged.unlink()


def adopt(directory: Path, text: str) -> None:
    if parse(text) is not None:
        write(directory, text)


def read(directory: Path, wrapper_gone: Callable[[], bool]) -> dict | None:
    """The record, or None while the wrapper still runs and has written none. Asks whether the wrapper is gone before it reads, so a record written just before the wrapper exits is never missed."""
    gone = wrapper_gone()
    path = directory / RESULT
    if not path.is_file():
        return {"error": "the job exited and wrote no result", LOST: True} if gone else None
    outcome = parse(path.read_text())
    return {"error": f"its {RESULT} is empty or does not parse", LOST: True} if outcome is None else outcome


if __name__ == "__main__":
    verb, directory = sys.argv[1], Path(sys.argv[2])
    if verb == "lost":
        lost(directory, sys.argv[3])
    else:
        adopt(directory, sys.stdin.read())
