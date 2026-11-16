"""The install train: the board under the long-running state root where each lander boards the plugins a merge changes rather than installing them at once, and the departure that installs the newest boarded sha of each at most every EVERY, run by the first watch of any drive on this Mac to find it due, since each install moves every watch tree of every drive onto it and rereads each drive cold, about 196 a day on Nov 15, 8 an hour and 16 at peaks."""

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from drive import state

BOARD = "train.json"
LOCK = "train.lock"
LOG = "train.log"
EVERY = timedelta(minutes=30)
WAIT_SECONDS = 20


def board_path() -> Path:
    return state.root() / BOARD


def empty() -> dict:
    return {"boarded": {}, "left": None, "carried": {}}


def read() -> dict:
    """The board as its last write left it, read with no lock, since each write replaces it whole: each plugin boarded with its newest sha, its repository and every merge boarded with it, when the train last left, and what it carried then with the pid of each install it started."""
    path = board_path()
    return json.loads(path.read_text()) if path.exists() else empty()


@contextmanager
def boarding() -> Iterator[dict]:
    """The board under its lock, the one every lander boarding a merge and every departure takes, so a merge boarded while a train leaves either leaves with it or waits for the next; written whole as the block ends."""
    path = board_path()
    with state.flocked(state.root() / LOCK):
        board = read()
        yield board
        written = path.with_name(f"{BOARD}.{os.getpid()}")
        written.write_text(json.dumps(board))
        os.replace(written, path)


def leaves(board: dict) -> datetime:
    """When the train leaves next: EVERY after it last left, or at once when it never left."""
    return state.parse_time(board["left"]) + EVERY if board["left"] else state.now()


def due(board: dict, moment: datetime) -> bool:
    return bool(board["boarded"]) and moment >= leaves(board)


def board(plugin: str, repo: str, key: str, sha: str) -> str:
    """Board a merge's install of one plugin, its sha the newest the plugin boarded, since a lander boards each merge as it reads it merged; the line saying when the train leaves."""
    with boarding() as held:
        seat = held["boarded"].setdefault(plugin, {"merges": {}})
        seat.update(sha=sha, repo=repo, merges=seat["merges"] | {key: sha})
        leaving = leaves(held)
    return f"{plugin} boarded the install train, which leaves by {leaving.strftime('%-I:%M %p')}"


def alighted(plugin: str, sha: str) -> None:
    """Take a plugin off the board once `drive install --sha` installed the sha it boarded with, as an install by hand does ahead of the train, so the train installs it no second time."""
    if read()["boarded"].get(plugin, {}).get("sha") != sha:
        return
    with boarding() as held:
        if held["boarded"].get(plugin, {}).get("sha") == sha:
            del held["boarded"][plugin]


def depart(name: str, moment: datetime) -> list[str]:
    """Send the train once it is due: under the board's lock, so one watch alone sends it, start `drive install <plugin> --sha <newest> --repo <repo>` for each plugin boarded, detached on the drive as the lander's install ran, move what it carries off the board with the pid of each install, and stamp when it left; a line for each plugin it carries."""
    if not due(read(), moment):
        return []
    with boarding() as held:
        if not due(held, moment):
            return []
        carried = {plugin: seat | {"pid": state.detach(name, ["install", plugin, "--sha", seat["sha"], "--repo", seat["repo"]], LOG)} for plugin, seat in held["boarded"].items()}
        held.update(boarded={}, left=state.stamp(moment), carried=carried)
    return [f"the install train left with {plugin} at {seat['sha'][:12]}, carrying {', '.join(seat['merges'])}" for plugin, seat in carried.items()]


def holds(merges: dict[str, str]) -> list[str]:
    """Each merge the train still holds: boarded, or carried by an install it started that still runs, so a proof of it waits for that install rather than installing the plugin early beside it."""
    held = read()
    seats = [*held["boarded"].values(), *(seat for seat in held["carried"].values() if state.alive(seat["pid"]))]
    return [key for key, sha in merges.items() if any(seat["merges"].get(key) == sha for seat in seats)]


def await_departure(merges: dict[str, str]) -> None:
    """Wait, reading the board every WAIT_SECONDS, until the train holds none of the merges, which its next departure ends within EVERY of the one before, saying once on stderr what it waits on."""
    if not (waiting := holds(merges)):
        return
    print(f"drive job prove: {', '.join(waiting)} rides the install train, which leaves by {leaves(read()).strftime('%-I:%M %p')}: waiting for its install", file=sys.stderr, flush=True)
    while holds(merges):
        time.sleep(WAIT_SECONDS)
