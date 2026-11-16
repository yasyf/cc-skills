import argparse
import fcntl
import json
import os
import re
import subprocess
from datetime import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lane import landing, releases, rows, stager, store

LANDING = "landing"
GONE_BASE = re.compile(r"couldn't find remote ref refs/heads/(?P<branch>\S+)")


def heard(drive: str) -> datetime | None:
    path = store.drive_dir(drive) / store.PRESENCE
    return store.parse(json.loads(path.read_text())["at"]) if path.exists() else None


def hold(drive: str, lane_id: str, text: str) -> str:
    store.update(drive, lane_id, lambda lane: store.note(lane, text))
    return text


def merged(drive: str, lane: dict, keys: list[str], guarded: bool, checkout: Path, sha: str, script: str, dry_run: bool) -> str:
    """Queue one landing unit through the repository's merge queue from the pass's one worktree at the queue's base: one preflight, `--check`, retried once after retargeting onto the default branch a bottom PR whose base branch is gone, as #57004's j4413-reader-first was once its parent merged; then, for a unit the presence classes mark, his presence read again at that moment, a lapsed one holding the unit with a note in its lane, never merging; then `--watch`, which queues it."""
    from drive import land as old_land

    numbers = [key.rpartition("#")[2] for key in keys]
    trusted = os.environ | {old_land.MISE_TRUST: str(checkout)}
    checked = subprocess.run([str(checkout / script), "--check", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if (gone := GONE_BASE.search(checked.stdout + checked.stderr)) and retargeted(keys[0], gone["branch"]):
        checked = subprocess.run([str(checkout / script), "--check", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if dry_run:
        return f"would queue {', '.join(keys)} at base {sha[:12]}; --check exited {checked.returncode}: {old_land.said(checked.stdout + checked.stderr)}"
    if failed := old_land.unfinished(drive, "--check", keys, checked):
        return hold(drive, lane["id"], failed)
    if guarded and not landing.present(heard(drive), store.now()):
        return hold(drive, lane["id"], f"not landed: {', '.join(keys)} is dangerous and his presence lapsed during the preflight at {sha[:12]}, so it lands at his next word or tap")
    done = subprocess.run([str(checkout / script), "--watch", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if done.returncode == 0:
        return hold(drive, lane["id"], f"{old_land.QUEUED}: {', '.join(keys)} at base {sha[:12]}")
    return hold(drive, lane["id"], old_land.unfinished(drive, "--watch", keys, done) or f"not landed: --watch exited {done.returncode}: {old_land.said(done.stdout + done.stderr)}")


def retargeted(key: str, branch: str) -> bool:
    """Retarget a PR whose base is the gone branch onto its repository's default branch; whether it did, false for a PR based elsewhere."""
    from drive import github

    repo, _, number = key.rpartition("#")
    base = subprocess.run(["gh", "pr", "view", number, "--repo", repo, "--json", "baseRefName", "-q", ".baseRefName"], capture_output=True, text=True, check=False, timeout=60).stdout.strip()
    if base != branch:
        return False
    return subprocess.run(["gh", "pr", "edit", number, "--repo", repo, "--base", github.default_branch(repo)], capture_output=True, text=True, check=False, timeout=60).returncode == 0


@contextmanager
def exclusive(path: Path) -> Iterator[bool]:
    """Hold a non-blocking lock for this process's life; whether it holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def land(args: argparse.Namespace) -> None:
    """One landing pass, never two at once: a pass that finds another running ends at once, so the supervisor's start each pass never piles landers up. It starts the stager when a lane waits on a staging test and the env is free, by stager.start, writes each waited release's result file by releases.write, reads every lane PR's state by rows.view, writes each unjudged head's verdict by rows.judge, prints what his Approve lands now in each lane, and in live mode queues each landing unit by merged() from one fresh worktree for the whole pass."""
    from drive import github, providers
    from drive import land as old_land

    with exclusive(store.drive_dir(args.drive) / LANDING / "pass.lock") as mine:
        if not mine:
            print("another landing pass runs; this one ends")
            return
        stager.start(args.drive)
        releases.write(args.drive)
        read = rows.view(args.drive)
        rows.judge(args.drive, read)
        moment = store.now()
        units = []
        for lane in [rows.seen(lane, read) for lane in store.load_all(args.drive).values()]:
            by_key = {landing.key(row): row for row in lane["prs"]}
            for verdict in landing.verdicts(lane, heard(args.drive), moment):
                print(f"{lane['id']}: {'LANDS ' + ', '.join(verdict.keys) if verdict.lands else 'HOLDS'}; {verdict.why}")
                if verdict.lands and (stager.live(args.drive) or args.dry_run):
                    units.append((lane, list(verdict.keys), any(landing.guarded(by_key[key]) for key in verdict.keys)))
        if not units:
            return
        checkout_of = Path(store.config(args.drive)["checkout"])
        repo = github.repo_of(units[0][1][0])
        script = providers.at(checkout_of).repo.queue().script
        with old_land.fresh(args.drive, checkout_of, repo, github.default_branch(repo)) as (checkout, sha):
            for lane, keys, guarded in units:
                print(merged(args.drive, lane, keys, guarded, checkout, sha, script, args.dry_run or not stager.live(args.drive)))


def spawn(drive: str) -> None:
    """Start one detached lane-land land for the drive, the supervisor's landing step each pass once live; its reservations keep two from queueing one unit."""
    if stager.live(drive):
        subprocess.Popen([str(Path(__file__).resolve().parents[2] / "bin" / "lane-land"), "--drive", drive, "land"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stage(args: argparse.Namespace) -> None:
    print(stager.start(args.drive) or "no stager started: no lane waits, the stager already holds staging-env, the queue said wait, or the drive is in shadow")


def want(args: argparse.Namespace) -> None:
    print(stager.want(args.drive, args.lane))


def candidates(args: argparse.Namespace) -> None:
    print(json.dumps(stager.candidates(args.drive, args.reach)[: args.limit], indent=2))


def take(args: argparse.Namespace) -> None:
    print(json.dumps(stager.take(args.drive, args.holder, args.lane, args.minutes)))


def renew(args: argparse.Namespace) -> None:
    print(stager.renew(args.drive, args.holder, args.minutes))


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
    verb.add_argument("--dry-run", action="store_true", help="run the merge queue's --check for each landing unit and queue nothing")
    verb.set_defaults(run=land)
    verb = verbs.add_parser("stage", help="start the drive's stager now when a lane waits on a staging test and the env is free")
    verb.set_defaults(run=stage)
    verb = verbs.add_parser("want", help="ask the stager for a staging test of the lane's pending stack; prints the cut id to wait on as staging_result:<id>")
    verb.add_argument("--lane", required=True)
    verb.set_defaults(run=want)
    verb = verbs.add_parser("candidates", help="dangerous stacks waiting on a proof, best ranked first")
    verb.add_argument("--reach", action="store_true", help="add changed files and release select's reach for the first few")
    verb.add_argument("--limit", type=int, default=30)
    verb.set_defaults(run=candidates)
    verb = verbs.add_parser("take", help="ask lane-queue for the staging env, for the lane of the cut's first stack")
    verb.add_argument("--lane", required=True)
    verb.add_argument("--holder", required=True)
    verb.add_argument("--minutes", type=int, required=True)
    verb.set_defaults(run=take)
    verb = verbs.add_parser("renew", help="extend the staging env's grant")
    verb.add_argument("--holder", required=True)
    verb.add_argument("--minutes", type=int, required=True)
    verb.set_defaults(run=renew)
    verb = verbs.add_parser("release", help="give the staging env back")
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
