import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from lane import landing, stager

PRESENCE = "presence.json"


def heard(drive: str) -> datetime | None:
    path = stager.lanes_dir(drive) / PRESENCE
    return datetime.fromisoformat(json.loads(path.read_text())["at"]) if path.exists() else None


def lanes(drive: str) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(stager.lanes_dir(drive).glob("*.json")) if path.name not in (stager.RANK, stager.ENV_LEASE, PRESENCE)]


def land(args: argparse.Namespace) -> None:
    """Print what his Approve lands now in each lane, and in live mode queue each landing unit through the repository's merge queue from the lane's worktree."""
    from drive import land as old_land
    from drive import providers

    moment = datetime.now().astimezone()
    for lane in lanes(args.drive):
        for verdict in landing.verdicts(lane, heard(args.drive), moment):
            print(f"{lane['id']}: {'LANDS ' + ', '.join(verdict.keys) if verdict.lands else 'HOLDS'}; {verdict.why}")
            if verdict.lands and stager.live(args.drive):
                worktree = Path(lane["worktree"])
                script = providers.at(worktree).repo.queue().script
                print(old_land.enqueued(args.drive, worktree, script, list(verdict.keys), {key: {"review_asked": None} for key in verdict.keys}, {}))


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
