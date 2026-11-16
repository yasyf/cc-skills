import argparse
import fcntl
import json
import os
import subprocess
from datetime import datetime, timedelta
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lane import landing, rows, stager, store

LANDING = "landing"


def heard(drive: str) -> datetime | None:
    path = store.drive_dir(drive) / store.PRESENCE
    return store.parse(json.loads(path.read_text())["at"]) if path.exists() else None


def hold(drive: str, lane_id: str, text: str) -> str:
    store.update(drive, lane_id, lambda lane: store.note(lane, text))
    return text


def merged(drive: str, lane: dict, keys: list[str], dangerous: bool) -> str:
    """Queue one landing unit through the repository's merge queue from a fresh worktree at the queue's base: one preflight, `--check`, then, for a unit holding a dangerous PR, his presence read again at that moment, since a preflight can outlast the 15 minutes his word or tap allows, and a lapsed one holds the unit and says so in its lane, never merging; then `--watch`, which queues it."""
    from drive import github, providers
    from drive import land as old_land

    worktree = Path(lane["worktree"])
    script = providers.at(worktree).repo.queue().script
    repo = github.repo_of(keys[0])
    numbers = [key.rpartition("#")[2] for key in keys]
    with old_land.fresh(drive, worktree, repo, github.default_branch(repo)) as (checkout, sha):
        trusted = os.environ | {old_land.MISE_TRUST: str(checkout)}
        checked = subprocess.run([str(checkout / script), "--check", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
        if failed := old_land.unfinished(drive, "--check", keys, checked):
            return hold(drive, lane["id"], failed)
        if dangerous and not landing.present(heard(drive), store.now()):
            return hold(drive, lane["id"], f"not landed: {', '.join(keys)} is dangerous and his presence lapsed during the preflight at {sha[:12]}, so it lands at his next word or tap")
        done = subprocess.run([str(checkout / script), "--watch", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if done.returncode == 0:
        return hold(drive, lane["id"], f"{old_land.QUEUED}: {', '.join(keys)} at base {sha[:12]}")
    return hold(drive, lane["id"], old_land.unfinished(drive, "--watch", keys, done) or f"not landed: --watch exited {done.returncode}: {old_land.said(done.stdout + done.stderr)}")


@contextmanager
def reserved(drive: str, keys: list[str]) -> Iterator[bool]:
    """Hold a landing unit's reservation, a non-blocking lock named by its bottom PR, for the life of one lander, so a second lane-land land started while the first still runs queues nothing for it; whether this lander holds it."""
    folder = store.drive_dir(drive) / LANDING
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{keys[0].replace('/', '_').replace('#', '-')}.lock").open("a") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def land(args: argparse.Namespace) -> None:
    """Read every lane PR's state by rows.view, write each unjudged head's verdict by rows.judge, then print what his Approve lands now in each lane, and in live mode queue each landing unit by merged() under its reservation."""
    read = rows.view(args.drive)
    rows.judge(args.drive, read)
    moment = store.now()
    for lane in [rows.seen(lane, read) for lane in store.load_all(args.drive).values()]:
        by_key = {landing.key(row): row for row in lane["prs"]}
        for verdict in landing.verdicts(lane, heard(args.drive), moment):
            print(f"{lane['id']}: {'LANDS ' + ', '.join(verdict.keys) if verdict.lands else 'HOLDS'}; {verdict.why}")
            if verdict.lands and stager.live(args.drive):
                with reserved(args.drive, list(verdict.keys)) as mine:
                    print(merged(args.drive, lane, list(verdict.keys), any(landing.dangerous(by_key[key]) for key in verdict.keys)) if mine else f"{verdict.keys[0]}: another lander holds it")


def spawn(drive: str) -> None:
    """Start one detached lane-land land for the drive, the supervisor's landing step each pass once live; its reservations keep two from queueing one unit."""
    if stager.live(drive):
        subprocess.Popen([str(Path(__file__).resolve().parents[2] / "bin" / "lane-land"), "--drive", drive, "land"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def candidates(args: argparse.Namespace) -> None:
    print(json.dumps(stager.candidates(args.drive, args.reach)[: args.limit], indent=2))


def take(args: argparse.Namespace) -> None:
    moment = datetime.now().astimezone()
    print(json.dumps(stager.take(args.drive, args.holder, moment + timedelta(minutes=args.minutes), moment)))


def release(args: argparse.Namespace) -> None:
    stager.release(args.drive, args.holder)


def decide(args: argparse.Namespace) -> None:
    print(stager.decide(args.drive, args.cut, json.loads(Path(args.file).read_text() if args.file != "-" else input())))


def result(args: argparse.Namespace) -> None:
    print(stager.result(args.drive, args.cut, json.loads(Path(args.file).read_text())))


def main() -> int:
    parser = argparse.ArgumentParser(prog="lane-land", description="His Approve merges and the staging tester's verbs over the lane files; shadow until cutover writes the live marker.")
    parser.add_argument("--drive", required=True)
    verbs = parser.add_subparsers(required=True)
    verb = verbs.add_parser("land", help="what his Approve lands now, queued only once live")
    verb.set_defaults(run=land)
    verb = verbs.add_parser("candidates", help="dangerous stacks waiting on a proof, best ranked first")
    verb.add_argument("--reach", action="store_true", help="add changed files and release select's reach for the first few")
    verb.add_argument("--limit", type=int, default=30)
    verb.set_defaults(run=candidates)
    verb = verbs.add_parser("take", help="take the staging env's lease")
    verb.add_argument("--holder", required=True)
    verb.add_argument("--minutes", type=int, required=True)
    verb.set_defaults(run=take)
    verb = verbs.add_parser("release", help="give the staging env's lease back")
    verb.add_argument("--holder", required=True)
    verb.set_defaults(run=release)
    verb = verbs.add_parser("decide", help="keep the batch the tester chose, from a JSON file")
    verb.add_argument("--cut", required=True)
    verb.add_argument("file")
    verb.set_defaults(run=decide)
    verb = verbs.add_parser("result", help="write each stack's result for a cut, which wakes its lane")
    verb.add_argument("--cut", required=True)
    verb.add_argument("file")
    verb.set_defaults(run=result)
    args = parser.parse_args()
    args.run(args)
    return 0
