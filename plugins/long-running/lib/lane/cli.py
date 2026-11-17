import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import timedelta
from pathlib import Path

from lane import fixes, landing, releases, rows, stager, store, supervisor, verbs

LANDING = "landing"
CONFLICTS = "conflicts"
REVIEWS = "reviews"
UNIT_SECONDS = 300
UNITS_AT_ONCE = 8
PASS_SECONDS = 600
LANDERS = "landers"
CONFLICT = re.compile(r"CONFLICTING|conflicts with origin/")


def unit_lock(drive: str, key: str) -> Path:
    """The lock a lander holds while it lands the unit whose bottom PR is key, which the supervisor takes too before it starts a fix job on that PR, so the two never push one branch."""
    return store.drive_dir(drive) / LANDING / f"{key.replace('/', '_').replace('#', '-')}.lock"


def hold(drive: str, lane_id: str, text: str) -> str:
    store.update(drive, lane_id, lambda lane: store.note(lane, text))
    return text


def queue_cli(drive: str) -> list[str]:
    """The words, run from the drive's checkout, that read and submit to the repository's merge queue, from the drive config's queue_cli."""
    return store.config(drive)["queue_cli"]


def queue_repo(checkout: Path) -> str:
    """The repository whose merge queue the drive's checkout serves, read from its origin remote."""
    from drive import github

    return github.repo_name(subprocess.run(["git", "-C", str(checkout), "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout)


def queued(drive: str, checkout: Path, numbers: list[str]) -> dict[str, dict]:
    """Each PR's merge queue row by number, read in one call: its state, its head and whether the queue holds it now."""
    if not numbers:
        return {}
    done = subprocess.run([*queue_cli(drive), "status", *numbers], cwd=checkout, capture_output=True, text=True, check=True, timeout=UNIT_SECONDS)
    return json.loads(done.stdout)


def dequeued(drive: str, checkout: Path, keys: list[str]) -> list[str]:
    """Take out of the merge queue each PR it holds that waits on a staging proof before merge, so a PR submitted before a ruling or a premerge_lanes entry named it never merges untested."""
    repo = queue_repo(checkout)
    numbers = [key.rpartition("#")[2] for key in keys if key.rpartition("#")[0] == repo]
    said = []
    for number, row in queued(drive, checkout, numbers).items():
        if row.get("isCurrentlySubmittedToQueue"):
            done = subprocess.run([*queue_cli(drive), "dequeue", number], cwd=checkout, capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
            said.append(f"dequeued {repo}#{number}: it waits on a staging proof" if done.returncode == 0 else f"not dequeued {repo}#{number}: {' '.join((done.stdout + done.stderr).split())[:300]}")
    return said


def merged(drive: str, lane: dict, keys: list[str], checkout: Path, held: dict[str, dict], dry_run: bool) -> str:
    """Tell the merge queue to take one landing unit, as a person would: one submit of the unit's top PR, which the queue lands with every PR of its native stack below, and return; no worktree and no local build, since the queue's own CI grades the merge. A unit the queue already holds is left there, and its head is asked of the review bot once when the queue reads it notReady. Whether a submitted unit merged, failed or was ejected is read by the next pass, which submits it again while it stays ready."""
    repo, _, top = keys[-1].rpartition("#")
    if repo != queue_repo(checkout):
        if dry_run:
            return f"would merge {', '.join(keys)} through GitHub, as {repo} has no merge queue the drive's checkout serves"
        done = subprocess.run(["gh", "pr", "merge", top, "-R", repo, "--squash"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
        said = " ".join((done.stdout + done.stderr).split())[:600]
        return hold(drive, lane["id"], f"merged through GitHub: {', '.join(keys)}" if done.returncode == 0 else f"not landed: gh pr merge refused {keys[-1]}: {said}")
    if (row := held.get(top) or {}).get("isCurrentlySubmittedToQueue"):
        state = (row.get("queueEntry") or {}).get("state") or row.get("state")
        if state == "notReady":
            reviewed(drive, keys[-1], row["sha"])
        return f"{lane['id']}: the merge queue holds {', '.join(keys)} ({state})"
    if dry_run:
        return f"would submit {', '.join(keys)} through #{top}"
    done = subprocess.run([*queue_cli(drive), "submit", top], cwd=checkout, capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    said = " ".join((done.stdout + done.stderr).split())[:600]
    if done.returncode == 0:
        return hold(drive, lane["id"], f"queued for merge: {', '.join(keys)} through #{top}")
    if CONFLICT.search(said):
        conflicted(drive, lane, keys, lambda: said)
    return hold(drive, lane["id"], f"not landed: the merge queue refused #{top}: {said}")


def reviewed(drive: str, key: str, head: str) -> None:
    """Ask the repository's review bot, once per head, for a head the merge queue reads notReady, as #53092's ai-review never started on its head and the queue waited on it."""
    from drive import land as old_land
    from drive import providers

    marker = store.drive_dir(drive) / LANDING / REVIEWS / f"{key.rpartition('#')[2]}-{head[:10]}"
    if marker.exists():
        return
    repo, _, number = key.rpartition("#")
    if not old_land.ask_review(providers.at(Path(store.config(drive)["checkout"])).repo.review_ask, repo, number, head):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def conflicted(drive: str, lane: dict, keys: list[str], said: Callable[[], str]) -> None:
    """Wake the lane's owner on a unit that conflicts with the default branch, once per set of heads, with what conflicts in full: a blocker on the lane, which fires its wait at once, and a handover entry a running owner sees at its next call, since the lane log wakes no one and only its owner can rebase."""
    heads = {landing.key(row): row["head"] for row in lane["prs"]}
    marker = store.drive_dir(drive) / LANDING / CONFLICTS / hashlib.sha256(json.dumps([[key, heads.get(key)] for key in keys]).encode()).hexdigest()[:16]
    if marker.exists():
        return
    what = f"{', '.join(keys)} conflicts with the default branch: {said()}. Rebase it with `ccx vcs stack submit` from its worktree; his Approve carries across your own commits, and the next landing pass queues it."
    verbs.blocked(drive, lane["id"], what, "lane-land")
    verbs.handover(drive, lane["id"], f"- {what}", "lane-land")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def conflict_said(checkout: Path, row: dict) -> str:
    """What a conflicting PR conflicts in, as its owner reads it: the paths by conflicts(), or GitHub's word alone when the read could not be made."""
    from drive import github

    repo = row["repo"]
    try:
        paths = conflicts(checkout, github.default_branch(repo), str(row["number"]))
    except subprocess.SubprocessError as error:
        return f"GitHub reads it CONFLICTING, and the files could not be read: {error}"
    return f"in {', '.join(paths)}" if paths else "GitHub reads it CONFLICTING"


def conflicts(checkout: Path, default: str, number: str) -> list[str]:
    """The paths a PR conflicts in with the default branch, by git merge-tree in the drive's checkout after fetching both; empty when it merges cleanly."""
    subprocess.run(["git", "-C", str(checkout), "fetch", "-q", "--no-tags", "origin", f"+refs/heads/{default}:refs/remotes/origin/{default}", f"+refs/pull/{number}/head:refs/lane-land/{number}"], capture_output=True, text=True, check=True, timeout=UNIT_SECONDS)
    done = subprocess.run(["git", "-C", str(checkout), "merge-tree", "--write-tree", "--name-only", "--no-messages", f"origin/{default}", f"refs/lane-land/{number}"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    return [path for path in done.stdout.splitlines()[1:] if path] if done.returncode == 1 else []


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
    """One landing pass, never two at once on one install: a pass that finds another on its own install running ends at once, so the supervisor's start each pass never piles landers up, while its lock is named by the install, so a pass of an older install, which ends before its next unit once a newer one is installed, never holds new code back, and a dead pass's lock goes with its process. It starts the stager when a lane waits on a staging test and the env is free, by stager.start, writes each waited release's result file by releases.write, reads every lane PR's state by rows.view, uses each head's verdict the judge worker already wrote, never judging inside the pass, prints what his Approve lands now in each lane, reads the merge queue's row for every unit in one call, and in live mode submits every unit the queue does not hold by merged(), UNITS_AT_ONCE at a time, and exits; the next pass reads what came of each."""
    print(f"pass {os.getpid()} starts at {store.stamp()}")

    here = Path(__file__).resolve().parents[2]
    with exclusive(store.drive_dir(args.drive) / LANDING / f"pass-{here.name}{'-' + '-'.join(args.lane) if args.lane else ''}.lock") as mine:
        if not mine:
            print("another landing pass runs; this one ends")
            return
        stager.start(args.drive)
        releases.write(args.drive)
        read = rows.view(args.drive)
        rule = rows.rule_key(args.drive)
        checkout = Path(store.config(args.drive)["checkout"])
        units = []
        unstaged = []
        for lane in [rows.seen(lane, read, rule, store.config(args.drive)["premerge_lanes"]) for lane in store.load_all(args.drive).values() if not args.lane or lane["id"] in args.lane]:
            unstaged += [landing.key(row) for stack in landing.stacks(lane) if not landing.proven(stack) for row in stack if not row.get("merged") and landing.judged(row) and landing.dangerous(row)]
            for verdict in landing.verdicts(lane):
                print(f"{lane['id']}: {'LANDS ' + ', '.join(verdict.keys) if verdict.lands else 'HOLDS'}; {verdict.why}")
                if verdict.lands and (stager.live(args.drive) or args.dry_run):
                    units.append((lane, list(verdict.keys)))
            if stager.live(args.drive) and not args.dry_run:
                for row in lane["prs"]:
                    if row.get("mergeable") == "CONFLICTING" and not row.get("merged"):
                        conflicted(args.drive, lane, [landing.key(row)], lambda row=row: conflict_said(checkout, row))
        if stager.live(args.drive) and not args.dry_run:
            for said in dequeued(args.drive, checkout, unstaged):
                print(said)
        if not units:
            return
        if supervisor.newest_install() != here:
            print(f"a newer install than {here.name} landed; this pass ends and the next pass, on the new code, lands its units")
            return
        held = queued(args.drive, checkout, [keys[-1].rpartition("#")[2] for _, keys in units if keys[-1].rpartition("#")[0] == queue_repo(checkout)])

        def one(unit: tuple[dict, list[str]]) -> str:
            lane, keys = unit
            if fixing := [key for key in keys if key in fixes.held_keys(store.load(args.drive, lane["id"]))]:
                return f"{lane['id']}: a fix job is working on {', '.join(fixing)}; this pass leaves {keys[0]} for the next"
            with exclusive(unit_lock(args.drive, keys[0])) as mine:
                if not mine:
                    return f"{lane['id']}: another lander is landing {keys[0]}; this pass skips it"
                return merged(args.drive, lane, keys, checkout, held, args.dry_run or not stager.live(args.drive))

        with ThreadPoolExecutor(max_workers=UNITS_AT_ONCE) as pool:
            for said in pool.map(one, units):
                print(said)


def judge(args: argparse.Namespace) -> None:
    """Write each unjudged head's danger verdict and repository mark, by rows.judge, as a worker of its own beside the landing pass, never inside it: a pass reads the verdicts already written, and a head judged after the pass read it lands on the next."""
    with exclusive(store.drive_dir(args.drive) / LANDING / "judge.lock") as mine:
        if not mine:
            print("another judge runs; this one ends")
            return
        print(f"judged {rows.judge(args.drive, rows.view(args.drive))} verdicts")


def verdicts(args: argparse.Namespace) -> None:
    read = rows.view(args.drive)
    rule = rows.rule_key(args.drive)
    for lane in [rows.seen(lane, read, rule, store.config(args.drive)["premerge_lanes"]) for lane in store.load_all(args.drive).values()]:
        for verdict in landing.verdicts(lane):
            print(f"{lane['id']}: {'LANDS' if verdict.lands else 'HOLDS'} {', '.join(verdict.keys)}; {verdict.why}")


def spawn(drive: str) -> None:
    """Start one detached lane-land land and one lane-land judge for the drive, the supervisor's landing step each pass once live, each under its own lock so neither piles up, after reap() ends the landers it started that an install has superseded or that ran past PASS_SECONDS; its reservations keep two from queueing one unit, and each start is recorded under landing/landers so the supervisor that started it can end it."""
    if not stager.live(drive):
        return
    reap(drive)
    here = Path(__file__).resolve().parents[2]
    folder = store.drive_dir(drive) / LANDING / LANDERS
    folder.mkdir(parents=True, exist_ok=True)
    for verb in ("land", "judge"):
        with (folder / f"{verb}.log").open("a") as said:
            started = subprocess.Popen([str(here / "bin" / "lane-land"), "--drive", drive, verb], stdout=said, stderr=subprocess.STDOUT, start_new_session=True, env=os.environ | {"PYTHONUNBUFFERED": "1"})
        (folder / f"{started.pid}.json").write_text(json.dumps({"pid": started.pid, "install": here.name, "at": store.stamp(), "verb": verb}) + "\n")


def reap(drive: str) -> list[int]:
    """End each lander the supervisor started whose install a newer one superseded or that ran past PASS_SECONDS, its whole process group with its queue script, after checking its command is this drive's lane-land land; forget each that already ended. Its units requeue on the next pass. The pids ended."""
    folder = store.drive_dir(drive) / LANDING / LANDERS
    newest = supervisor.newest_install().name
    ended = []
    for record in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        started = json.loads(record.read_text())
        command = subprocess.run(["ps", "-o", "command=", "-p", str(started["pid"])], capture_output=True, text=True, check=False).stdout
        if f"lane-land --drive {drive} {started.get('verb', 'land')}" not in command:
            record.unlink()
            continue
        if started["install"] == newest and store.now() - store.parse(started["at"]) <= timedelta(seconds=PASS_SECONDS):
            continue
        with suppress(ProcessLookupError):
            os.killpg(started["pid"], signal.SIGTERM)
        record.unlink()
        ended.append(started["pid"])
    return ended


def stage(args: argparse.Namespace) -> None:
    print(stager.start(args.drive) or "no stager started: every lane's staging ask is already taken, or the drive is in shadow")


def want(args: argparse.Namespace) -> None:
    print(stager.want(args.drive, args.lane))


def candidates(args: argparse.Namespace) -> None:
    print(json.dumps(stager.candidates(args.drive, args.reach)[: args.limit], indent=2))


def decide(args: argparse.Namespace) -> None:
    print(stager.decide(args.drive, args.cut, json.loads(Path(args.file).read_text() if args.file != "-" else input())))


def targets(args: argparse.Namespace) -> None:
    print(json.dumps(stager.targets(args.drive, args.target)))


def block(args: argparse.Namespace) -> None:
    print(stager.block(args.drive, args.lane, args.why))


def result(args: argparse.Namespace) -> None:
    print(stager.result(args.drive, args.cut, json.loads(Path(args.file).read_text())))


def main() -> int:
    parser = argparse.ArgumentParser(prog="lane-land", description="His Approve merges and the staging tester's verbs over the lane files; shadow until cutover writes the live marker.")
    parser.add_argument("--drive", required=True)
    verbs = parser.add_subparsers(required=True)
    verb = verbs.add_parser("land", help="what his Approve lands now, queued only once live")
    verb.add_argument("--dry-run", action="store_true", help="run the merge queue's --check for each landing unit and queue nothing")
    verb.add_argument("--lane", action="append", help="land only this lane's units, beside any whole-drive pass; repeatable")
    verb.set_defaults(run=land)
    verb = verbs.add_parser("judge", help="write each unjudged head's danger verdict and repository mark, as its own worker beside the landing pass")
    verb.set_defaults(run=judge)
    verb = verbs.add_parser("verdicts", help="each lane stack's verdict now, read from GitHub, with no pass lock, no judging and no merge queue call")
    verb.set_defaults(run=verdicts)
    verb = verbs.add_parser("stage", help="start the drive's stager now when a lane waits on a staging test and the env is free")
    verb.set_defaults(run=stage)
    verb = verbs.add_parser("want", help="ask the stager for a staging test of the lane's pending stack; prints the cut id to wait on as staging_result:<id>")
    verb.add_argument("--lane", required=True)
    verb.set_defaults(run=want)
    verb = verbs.add_parser("candidates", help="dangerous stacks waiting on a proof, best ranked first")
    verb.add_argument("--reach", action="store_true", help="add changed files and release select's reach for the first few")
    verb.add_argument("--limit", type=int, default=30)
    verb.set_defaults(run=candidates)
    verb = verbs.add_parser("decide", help="keep the batch the tester chose, from a JSON file")
    verb.add_argument("--cut", required=True)
    verb.add_argument("file")
    verb.set_defaults(run=decide)
    verb = verbs.add_parser("targets", help="set the drive's staging targets, each '<env>' or '<env> <SandSQL generation>', refused when one does not exist on the default branch")
    verb.add_argument("target", nargs="+")
    verb.set_defaults(run=targets)
    verb = verbs.add_parser("block", help="end a lane's staging ask the stager will not run, with its why in full, which wakes the lane")
    verb.add_argument("--lane", required=True)
    verb.add_argument("--why", required=True)
    verb.set_defaults(run=block)
    verb = verbs.add_parser("result", help="write each stack's result for a cut, which wakes its lane")
    verb.add_argument("--cut", required=True)
    verb.add_argument("file")
    verb.set_defaults(run=result)
    args = parser.parse_args()
    args.run(args)
    return 0
