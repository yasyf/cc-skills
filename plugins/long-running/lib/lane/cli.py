import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lane import landing, releases, rows, stager, store, supervisor, verbs

LANDING = "landing"
LANDINGS = "landings"
CONFLICTS = "conflicts"
REVIEWS = "reviews"
UNREVIEWED = re.compile(r"\bnot started on head\b")
WATCH_SECONDS = 300
CONFLICT = re.compile(r"CONFLICTING|conflicts with origin/")
SKIP_MERGE_PREFLIGHT = "STACK_ENQUEUE_SKIP_MERGE_PREFLIGHT"
GONE_BASE = re.compile(r"couldn't find remote ref refs/heads/(?P<branch>\S+)")


def hold(drive: str, lane_id: str, text: str) -> str:
    store.update(drive, lane_id, lambda lane: store.note(lane, text))
    return text


def merged(drive: str, lane: dict, keys: list[str], checkout: Path, sha: str, script: str, dry_run: bool) -> str:
    """Queue one landing unit through the repository's merge queue from the pass's one worktree at the queue's base, skipping the script's local merge preflight build since every unit it lands is green at its head and the merge queue's CI grades the merge: one preflight, `--check`, retried once after retargeting onto the default branch a bottom PR whose base branch is gone, as #57004's j4413-reader-first was once its parent merged; then `--watch`, which queues it."""
    from drive import land as old_land

    numbers = [key.rpartition("#")[2] for key in keys]
    trusted = os.environ | {old_land.MISE_TRUST: str(checkout), SKIP_MERGE_PREFLIGHT: "1"}
    checked = subprocess.run([str(checkout / script), "--check", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if (gone := GONE_BASE.search(checked.stdout + checked.stderr)) and retargeted(keys[0], gone["branch"]):
        checked = subprocess.run([str(checkout / script), "--check", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if (blocked := old_land.BLOCKED_LINE.search(checked.stdout)) and (cut := numbers.index(blocked["number"]) if blocked["number"] in numbers else 0):
        numbers, keys = numbers[:cut], keys[:cut]
        checked = subprocess.run([str(checkout / script), "--check", *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
    if refused := reviews_asked(drive, keys[0].rpartition("#")[0], checkout, checked.stdout):
        return hold(drive, lane["id"], refused)
    if dry_run:
        return f"would queue {', '.join(keys)} at base {sha[:12]}; --check exited {checked.returncode}: {old_land.said(checked.stdout + checked.stderr)}"
    if failed := old_land.unfinished(drive, "--check", keys, checked):
        return hold(drive, lane["id"], failed)
    done = bounded([str(checkout / script), "--watch", *numbers], checkout, trusted)
    if done is None:
        return hold(drive, lane["id"], f"not landed: {', '.join(keys)} was not queued within {WATCH_SECONDS // 60} minutes of --watch at base {sha[:12]}, so the watch ended and the next pass queues it again")
    if done.returncode == 0:
        return hold(drive, lane["id"], f"{old_land.QUEUED}: {', '.join(keys)} at base {sha[:12]}")
    if CONFLICT.search(said := done.stdout + done.stderr):
        conflicted(drive, lane, keys, old_land.said(said))
    return hold(drive, lane["id"], old_land.unfinished(drive, "--watch", keys, done) or f"not landed: --watch exited {done.returncode}: {old_land.said(said)}")


def reviews_asked(drive: str, repo: str, checkout: Path, checked: str) -> str | None:
    """Ask the repository's review bot, once per head, to review each head --check names BLOCKED on a review it has not started, as #53092's ai-review never started and its --watch waited on it for 29 minutes; why the unit holds when the ask was refused, None otherwise."""
    from drive import land as old_land
    from drive import providers

    for line in old_land.BLOCKED_LINE.finditer(checked):
        marker = store.drive_dir(drive) / LANDING / REVIEWS / f"{line['number']}-{line['head']}"
        if not (old_land.OLDER_HEAD.search(line["gaps"]) or UNREVIEWED.search(line["gaps"])) or marker.exists():
            continue
        if refused := old_land.ask_review(providers.at(checkout).repo.review_ask, repo, line["number"], line["head"]):
            return refused
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    return None


def bounded(argv: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess | None:
    """Run a --watch in its own process group for at most WATCH_SECONDS; None when it ran out, its whole group ended so no script outlives it, since a watch that has not queued its unit by then waits on something no pass should sit behind, as #53092's sat 29 minutes."""
    watch = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = watch.communicate(timeout=WATCH_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(watch.pid, signal.SIGTERM)
        watch.communicate()
        return None
    return subprocess.CompletedProcess(argv, watch.returncode, out, err)


def conflicted(drive: str, lane: dict, keys: list[str], said: str) -> None:
    """Hand the lane's owner a note that the unit conflicts with the default branch, once per set of heads, since the lane log wakes no one and only its owner can rebase."""
    heads = {landing.key(row): row["head"] for row in lane["prs"]}
    marker = store.drive_dir(drive) / LANDING / CONFLICTS / hashlib.sha256(json.dumps([[key, heads.get(key)] for key in keys]).encode()).hexdigest()[:16]
    if marker.exists():
        return
    verbs.handover(drive, lane["id"], f"- lane-land could not queue {', '.join(keys)}: it conflicts with the default branch: {said}. Rebase it with `ccx vcs stack submit` from its worktree; his Approve carries across your own commits, and the next landing pass queues it.", "lane-land")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


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
    """One landing pass, never two at once on one install: a pass that finds another on its own install running ends at once, so the supervisor's start each pass never piles landers up, while its lock is named by the install, so a pass of an older install, which ends before its next unit once a newer one is installed, never holds new code back, and a dead pass's lock goes with its process. It starts the stager when a lane waits on a staging test and the env is free, by stager.start, writes each waited release's result file by releases.write, reads every lane PR's state by rows.view, writes each unjudged head's verdict by rows.judge, prints what his Approve lands now in each lane, and in live mode queues each landing unit by merged() from one fresh worktree for the whole pass."""
    from drive import github, providers
    from drive import land as old_land

    here = Path(__file__).resolve().parents[2]
    with exclusive(store.drive_dir(args.drive) / LANDING / f"pass-{here.name}{'-' + '-'.join(args.lane) if args.lane else ''}.lock") as mine:
        if not mine:
            print("another landing pass runs; this one ends")
            return
        stager.start(args.drive)
        releases.write(args.drive)
        read = rows.view(args.drive)
        rule = rows.rule_key(args.drive)
        rows.judge(args.drive, read)
        units = []
        for lane in [rows.seen(lane, read, rule) for lane in store.load_all(args.drive).values() if not args.lane or lane["id"] in args.lane]:
            for verdict in landing.verdicts(lane):
                print(f"{lane['id']}: {'LANDS ' + ', '.join(verdict.keys) if verdict.lands else 'HOLDS'}; {verdict.why}")
                if verdict.lands and (stager.live(args.drive) or args.dry_run):
                    units.append((lane, list(verdict.keys)))
        if not units:
            return
        checkout_of = Path(store.config(args.drive)["checkout"])
        repo = github.repo_of(units[0][1][0])
        script = providers.at(checkout_of).repo.queue().script
        with old_land.fresh(args.drive, checkout_of, repo, github.default_branch(repo), store.drive_dir(args.drive) / LANDINGS) as (checkout, sha):
            for lane, keys in units:
                if supervisor.newest_install() != here:
                    print(f"a newer install than {here.name} landed; this pass ends before {keys[0]} and the next pass, on the new code, lands the rest")
                    return
                with exclusive(store.drive_dir(args.drive) / LANDING / f"{keys[0].replace('/', '_').replace('#', '-')}.lock") as mine:
                    if not mine:
                        print(f"{lane['id']}: another lander is landing {keys[0]}; this pass skips it")
                        continue
                    print(merged(args.drive, lane, keys, checkout, sha, script, args.dry_run or not stager.live(args.drive)))


def verdicts(args: argparse.Namespace) -> None:
    read = rows.view(args.drive)
    rule = rows.rule_key(args.drive)
    for lane in [rows.seen(lane, read, rule) for lane in store.load_all(args.drive).values()]:
        for verdict in landing.verdicts(lane):
            print(f"{lane['id']}: {'LANDS' if verdict.lands else 'HOLDS'} {', '.join(verdict.keys)}; {verdict.why}")


def spawn(drive: str) -> None:
    """Start one detached lane-land land for the drive, the supervisor's landing step each pass once live; its reservations keep two from queueing one unit."""
    if stager.live(drive):
        subprocess.Popen([str(Path(__file__).resolve().parents[2] / "bin" / "lane-land"), "--drive", drive, "land"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stage(args: argparse.Namespace) -> None:
    print(stager.start(args.drive) or "no stager started: every lane's staging ask is already taken, or the drive is in shadow")


def want(args: argparse.Namespace) -> None:
    print(stager.want(args.drive, args.lane))


def candidates(args: argparse.Namespace) -> None:
    print(json.dumps(stager.candidates(args.drive, args.reach)[: args.limit], indent=2))


def decide(args: argparse.Namespace) -> None:
    print(stager.decide(args.drive, args.cut, json.loads(Path(args.file).read_text() if args.file != "-" else input())))


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
