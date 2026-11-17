import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import timedelta
from pathlib import Path

from lane import fixes, landing, page, releases, rows, stager, store, supervisor, verbs, work

LANDING = "landing"
CONFLICTS = "conflicts"
REVIEWS = "reviews"
UNIT_SECONDS = 300
UNITS_AT_ONCE = 8
PASS_SECONDS = 600
FREEZE_HOURS = 24
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
    """Tell the merge queue to take one landing unit, as a person would: one submit of the unit's top PR, which the queue lands with every PR of its native stack below, and return; no worktree and no local build, since the queue's own CI grades the merge. A unit the queue already holds is left there, and its head is asked of the review bot once when the queue reads it notReady; a unit the queue failed at its current head is left out and its owner told once, by ejected(), until a new head. Whether a submitted unit merged, failed or was ejected is read by the next pass, which submits it again while it stays ready."""
    repo, _, top = keys[-1].rpartition("#")
    if repo != queue_repo(checkout):
        if dry_run:
            return f"would merge {', '.join(keys)} through GitHub, as {repo} has no merge queue the drive's checkout serves"
        done = subprocess.run(["gh", "pr", "merge", top, "-R", repo, "--squash"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
        said = " ".join((done.stdout + done.stderr).split())[:600]
        if done.returncode == 0:
            return hold(drive, lane["id"], f"merged through GitHub: {', '.join(keys)}")
        return refused(drive, lane, keys, f"gh pr merge refused {keys[-1]}: {said}")
    if (row := held.get(top) or {}).get("isCurrentlySubmittedToQueue"):
        state = (row.get("queueEntry") or {}).get("state") or row.get("state")
        if state == "notReady":
            reviewed(drive, keys[-1], row["sha"])
        return f"{lane['id']}: the merge queue holds {', '.join(keys)} ({state})"
    if (row.get("queueEntry") or {}).get("state") == "failed" and row["sha"] == next(pr["head"] for pr in lane["prs"] if landing.key(pr) == keys[-1]):
        return ejected(drive, lane, keys, row) if not dry_run else f"would leave {', '.join(keys)} out: the merge queue failed it at {row['sha'][:12]}"
    if (incident := lane.get("incident")) and not incident.get("fix_live"):
        return f"would merge {', '.join(keys)} outside the merge queue and hotfix it, as the fix of an open incident" if dry_run else hotfixed(drive, lane, keys, checkout, incident)
    if dry_run:
        return f"would submit {', '.join(keys)} through #{top}"
    done = subprocess.run([*queue_cli(drive), "submit", top], cwd=checkout, capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    said = " ".join((done.stdout + done.stderr).split())[:600]
    if done.returncode == 0:
        return hold(drive, lane["id"], f"queued for merge: {', '.join(keys)} through #{top}")
    if CONFLICT.search(said):
        conflicted(drive, lane, keys, lambda: said)
        return hold(drive, lane["id"], f"not landed: the merge queue refused #{top}: {said}")
    return refused(drive, lane, keys, f"the merge queue refused #{top}: {said}")


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
    """Rebase each PR of a unit that conflicts with the default branch in this call with no model session, by replayed(), and queue a rebase item for a queue worker only for a PR whose commits do not replay cleanly; tell the lane's owner in one line, once per set of heads, which was done, so the owner does no rebase and waits on nothing for it."""
    checkout = Path(store.config(drive)["checkout"])
    rows_of = {landing.key(row): row for row in lane["prs"]}
    seen = page.view(drive)["prs"]
    done, queued_keys = [], []
    for key in keys:
        if (row := rows_of.get(key)) and row.get("branch") and (head := replayed(checkout, lane, row)):
            done.append(f"{key} to {head[:12]}")
        elif pull := seen.get(key):
            repo, _, number = key.rpartition("#")
            work.add(drive, "rebase", lane["id"], key, fixes.brief("conflict", repo, int(number), pull), head=pull["head"], touches=[repo], by="lane-land")
            queued_keys.append(key)
    what = "; ".join(part for part in (f"rebased mechanically {', '.join(done)}" if done else "", f"a rebase item is queued for a worker for {', '.join(queued_keys)}" if queued_keys else "") if part)
    woken(drive, lane, keys, "conflicts", lambda: f"{', '.join(keys)} conflicts with the default branch {said()}; {what or 'it could not be read to rebase'}, and the next landing pass queues it once rebased, so it needs nothing from you.", block=False)


def replayed(checkout: Path, lane: dict, row: dict) -> str | None:
    """Rebase one PR onto its base with git replay, no worktree and no model, pushing the new head over the one read only if the branch has not moved since: its commits after the newest merged PR of its lane it still carries, or after its merge base when it carries none, as a squash merge below it leaves them. The new head, or None when its commits do not replay cleanly or the push is refused."""
    git = ["git", "-C", str(checkout)]
    number = row["number"]
    fetched = subprocess.run([*git, "fetch", "-q", "--no-tags", "origin", f"+refs/heads/{row['base']}:refs/remotes/origin/{row['base']}", f"+refs/pull/{number}/head:refs/lane-land/{number}"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    if fetched.returncode:
        return None
    below = [other["head"] for other in lane["prs"] if other.get("merged") and other.get("head") and subprocess.run([*git, "merge-base", "--is-ancestor", other["head"], f"refs/lane-land/{number}"], capture_output=True, check=False).returncode == 0]
    upstream = below[-1] if below else subprocess.run([*git, "merge-base", f"origin/{row['base']}", f"refs/lane-land/{number}"], capture_output=True, text=True, check=True).stdout.strip()
    replay = subprocess.run([*git, "replay", f"--onto=origin/{row['base']}", f"{upstream}..refs/lane-land/{number}"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    if replay.returncode or not (lines := replay.stdout.split()):
        return None
    head = lines[2]
    pushed = subprocess.run([*git, "push", "-q", f"--force-with-lease=refs/heads/{row['branch']}:{row['head']}", "origin", f"{head}:refs/heads/{row['branch']}"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    return head if pushed.returncode == 0 else None


def frozen(drive: str, lanes: list[dict]) -> list[str]:
    """Freeze, for each open incident whose fix names its components under hotfix_only, every other release of those components until the fix is live, through the release provider's incident_freeze, which lets the incident's own hotfix through; and lift that freeze once the incident records its fix live. The freeze's id is kept on the incident. What was frozen or lifted."""
    from drive import providers

    release = providers.at(Path(store.config(drive)["checkout"])).release
    said = []
    for lane in lanes:
        incident = lane.get("incident") or {}
        if incident.get("hotfix_only") and not incident.get("fix_live") and not incident.get("freeze"):
            until = store.stamp(store.now() + timedelta(hours=FREEZE_HOURS))
            freeze = release.incident_freeze(incident["hotfix_only"].split(","), f"incident {incident['link']}: the broken service ships only its fix until the fix is live", until)
            store.update(drive, lane["id"], lambda kept, freeze=freeze, until=until: keep_freeze(kept, freeze, f"froze {kept['incident']['hotfix_only']} but the incident's fix until {until}: {freeze}"))
            said.append(f"{lane['id']}: froze {incident['hotfix_only']} but the incident's fix, {freeze}")
        elif incident.get("fix_live") and incident.get("freeze"):
            release.run(release.unfreeze(incident["freeze"]))
            store.update(drive, lane["id"], lambda kept: keep_freeze(kept, None, f"lifted the incident freeze {kept['incident']['freeze']}: the fix is live"))
            said.append(f"{lane['id']}: lifted the incident freeze {incident['freeze']}")
    return said


def keep_freeze(lane: dict, freeze: str | None, text: str) -> None:
    store.note(lane, text)
    lane["incident"]["freeze"] = freeze


def hotfixed(drive: str, lane: dict, keys: list[str], checkout: Path, incident: dict) -> str:
    """Land an open incident's fix as the incident rule says: merge it with `gh pr merge --squash`, skipping the merge queue, then start the drive config's hotfix_cli on it with the components the incident names under hotfix_only as --only, and tell the owner what started, with the release tool's own words. With no components named, the owner is told once to name them, since a hotfix with no --only ships every component the release tool pairs with the fix."""
    repo, _, top = keys[-1].rpartition("#")
    if not store.config(drive)["hotfix_cli"]:
        return refused(drive, lane, keys, "it is an open incident's fix, and the drive config names no hotfix_cli to release it with")
    done = subprocess.run(["gh", "pr", "merge", top, "-R", repo, "--squash"], capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    if done.returncode:
        return refused(drive, lane, keys, f"gh pr merge refused the incident fix {keys[-1]}: {' '.join((done.stdout + done.stderr).split())[:600]}")
    hold(drive, lane["id"], f"merged the incident fix {', '.join(keys)} outside the merge queue")
    if not (only := incident.get("hotfix_only")):
        woken(drive, lane, keys, "hotfix", lambda: f"the incident fix {', '.join(keys)} merged outside the merge queue; name the components its hotfix releases with `lane incident --hotfix-only <a,b>`, and the lander starts the hotfix of each fix it merges from then on. Start this one with `{' '.join(store.config(drive)['hotfix_cli'])} {top} --only <a,b>`.", block=True)
        return f"{lane['id']}: merged the incident fix {', '.join(keys)}; its hotfix waits on the components its owner names"
    started = subprocess.run([*store.config(drive)["hotfix_cli"], top, "--only", only, "--incident", incident["link"]], cwd=checkout, capture_output=True, text=True, check=False, timeout=UNIT_SECONDS)
    said = " ".join((started.stdout + started.stderr).split())[:600]
    what = f"the hotfix of the incident fix {', '.join(keys)} with --only {only} started: {said}" if started.returncode == 0 else f"the hotfix of the incident fix {', '.join(keys)} with --only {only} was refused: {said}"
    woken(drive, lane, keys, "hotfix", lambda: f"{what}. Watch it to live and record the proof with `lane incident --fix-live <link>`.", block=True)
    return hold(drive, lane["id"], what)


def refused(drive: str, lane: dict, keys: list[str], why: str) -> str:
    """Note a landing the merge queue or GitHub refused for any reason but a conflict and tell the lane's owner once per set of heads, in this call, with the refusal in full, so no refused PR sits with no one told."""
    said = hold(drive, lane["id"], f"not landed: {why}")
    woken(drive, lane, keys, "refused", lambda: f"{', '.join(keys)} was not landed: {why}. Fix what the refusal names, or say why it cannot land; each landing pass submits it again while it stays ready.", block=True)
    return said


def ejected(drive: str, lane: dict, keys: list[str], row: dict) -> str:
    """Wake the lane's owner on a unit the merge queue failed at its current head, once per set of heads, and leave it out of the queue until a new head, since a resubmit of the same head fails the same way."""
    woken(drive, lane, keys, "ejected", lambda: f"the merge queue failed {', '.join(keys)} at {row['sha'][:12]}: read its run at {row['htmlUrl']}, fix it with a push, and the next landing pass queues the new head.", block=True)
    return f"{lane['id']}: the merge queue failed {', '.join(keys)} at {row['sha'][:12]}; it waits on a new head"


def woken(drive: str, lane: dict, keys: list[str], kind: str, what: Callable[[], str], block: bool) -> None:
    """Tell the lane's owner what holds a unit, once per set of heads: a handover entry a running owner sees at its next call and its next brief prints, and, with block, a blocker on the lane, which fires its wait at once, for a hold only the owner can clear."""
    heads = {landing.key(row): row["head"] for row in lane["prs"]}
    marker = store.drive_dir(drive) / LANDING / CONFLICTS / hashlib.sha256(json.dumps([kind, *[[key, heads.get(key)] for key in keys]]).encode()).hexdigest()[:16]
    if marker.exists():
        return
    said = what()
    if block:
        verbs.blocked(drive, lane["id"], said, "lane-land")
    verbs.handover(drive, lane["id"], f"- {said}", "lane-land")
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
    """Hold a non-blocking lock for the block's span, whether it holds it, and remove its file as it lets go, so locks live only while held, by his words "lock the most granular thing for the shortest time". A lock whose file was removed or replaced between its open and its lock is not held, since another holder took the path's new file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            same = path.stat().st_ino == os.fstat(held.fileno()).st_ino
        except FileNotFoundError:
            same = False
        if not same:
            yield False
            return
        try:
            yield True
        finally:
            path.unlink()


def land(args: argparse.Namespace) -> None:
    """One landing pass, never two at once on one install: a pass that finds another on its own install running ends at once, so the supervisor's start each pass never piles landers up, while its lock is named by the install, so a pass of an older install, which ends before its next unit once a newer one is installed, never holds new code back, and a dead pass's lock goes with its process. It writes each waited release's result file by releases.write, reads every lane PR's state by rows.view, takes out of the merge queue each PR that waits on a staging proof by dequeued(), puts every staging test the lanes owe on the work queue by stager.queue, uses each head's verdict the judge worker already wrote, never judging inside the pass, prints what his Approve lands now in each lane, reads the merge queue's row for every unit in one call, and in live mode submits every unit the queue does not hold by merged(), UNITS_AT_ONCE at a time, and exits; the next pass reads what came of each."""
    print(f"pass {os.getpid()} starts at {store.stamp()}")

    here = Path(__file__).resolve().parents[2]
    with exclusive(store.drive_dir(args.drive) / LANDING / f"pass-{here.name}{'-' + '-'.join(args.lane) if args.lane else ''}.lock") as mine:
        if not mine:
            print("another landing pass runs; this one ends")
            return
        releases.write(args.drive)
        read = rows.view(args.drive)
        rule = rows.rule_key(args.drive)
        checkout = Path(store.config(args.drive)["checkout"])
        units = []
        unstaged: dict[str, str] = {}
        seen_lanes = [rows.seen(lane, read, rule, store.config(args.drive)["premerge_lanes"]) for lane in store.load_all(args.drive).values() if (not args.lane or lane["id"] in args.lane) and lane["state"] != store.PARKED]
        for lane in seen_lanes:
            unstaged |= {landing.key(row): lane["id"] for stack in landing.stacks(lane) if not landing.proven(stack) for row in stack if not row.get("merged") and landing.judged(row) and landing.dangerous(row)}
            for verdict in landing.verdicts(lane):
                print(f"{lane['id']}: {'LANDS ' + ', '.join(verdict.keys) if verdict.lands else 'HOLDS'}; {verdict.why}")
                if verdict.lands and (stager.live(args.drive) or args.dry_run):
                    units.append((lane, list(verdict.keys)))
            if stager.live(args.drive) and not args.dry_run:
                for row in lane["prs"]:
                    if row.get("mergeable") == "CONFLICTING" and not row.get("merged") and fixes.landable(row, row["head"]):
                        conflicted(args.drive, lane, [landing.key(row)], lambda row=row: conflict_said(checkout, row))
        if stager.live(args.drive) and not args.dry_run:
            for said in dequeued(args.drive, checkout, list(unstaged)):
                print(said)
            if not args.lane:
                print(f"staging items queued or kept: {len(stager.queue(args.drive, seen_lanes))}")
            for said in frozen(args.drive, seen_lanes):
                print(said)
        if not units:
            return
        if supervisor.newest_install() != here:
            print(f"a newer install than {here.name} landed; this pass ends and the next pass, on the new code, lands its units")
            return
        held = queued(args.drive, checkout, [keys[-1].rpartition("#")[2] for _, keys in units if keys[-1].rpartition("#")[0] == queue_repo(checkout)])

        def one(unit: tuple[dict, list[str]]) -> str:
            lane, keys = unit
            if fixing := [key for key in keys if key in fixes.held_keys(args.drive, store.load(args.drive, lane["id"]))]:
                return f"{lane['id']}: a fix job or queue worker is working on {', '.join(fixing)}; this pass leaves {keys[0]} for the next"
            with exclusive(unit_lock(args.drive, keys[0])) as mine:
                if not mine:
                    return f"{lane['id']}: another lander is landing {keys[0]}; this pass skips it"
                return merged(args.drive, lane, keys, checkout, held, args.dry_run or not stager.live(args.drive))

        with ThreadPoolExecutor(max_workers=UNITS_AT_ONCE) as pool:
            for said in pool.map(one, units):
                print(said)


def land_stack(drive: str, lane_id: str, key: str) -> str:
    """Land the one stack of lane_id holding the PR key, in the caller's call, for an event handler: his Approve tap, a check finishing at the head of an approved or tooling PR, or a passed staging result. It reads that lane's PRs alone, takes the stack's verdict as a pass would, and submits it by merged() when it lands, so a green stack is in the merge queue within the event's own call; what it did, or why the stack waits."""
    lane = rows.seen(store.load(drive, lane_id), rows.view(drive, [lane_id]), rows.rule_key(drive), store.config(drive)["premerge_lanes"])
    stack = next(stack for stack in landing.stacks(lane) if key in [landing.key(row) for row in stack])
    verdict = landing.stack_verdict(stack)
    if not verdict.lands:
        return f"{lane_id}: HOLDS; {verdict.why}"
    keys = list(verdict.keys)
    checkout = Path(store.config(drive)["checkout"])
    if fixing := [unit for unit in keys if unit in fixes.held_keys(drive, store.load(drive, lane_id))]:
        return f"{lane_id}: a fix job or queue worker is working on {', '.join(fixing)}; it lands when that work is done"
    repo, _, top = keys[-1].rpartition("#")
    held = queued(drive, checkout, [top] if repo == queue_repo(checkout) else [])
    with exclusive(unit_lock(drive, keys[0])) as mine:
        if not mine:
            return f"{lane_id}: another lander is landing {keys[0]}"
        return merged(drive, lane, keys, checkout, held, not stager.live(drive))


def judge(args: argparse.Namespace) -> None:
    """Write each unjudged head's danger verdict and repository mark, by rows.judge, as a worker of its own beside the landing pass, never inside it: a pass reads the verdicts already written, and a head judged after the pass read it lands on the next."""
    with exclusive(store.drive_dir(args.drive) / LANDING / "judge.lock") as mine:
        if not mine:
            print("another judge runs; this one ends")
            return
        print(f"judged {rows.judge(args.drive, rows.view(args.drive))} verdicts")


def premerge(args: argparse.Namespace) -> None:
    """Print each open premerge-lane PR's answer to the drive config's premerge_question at its head, read now and written nowhere, as moves when a clear no lets it merge before its staging test and held otherwise."""
    read = rows.view(args.drive)
    key = rows.rule_key(args.drive)
    premerge = store.config(args.drive)["premerge_lanes"]
    candidates = [landing.key(row) for lane_id, lane in store.load_all(args.drive).items() if lane_id in premerge for row in rows.seen(lane, read, key, premerge)["prs"] if row["head"] and not row.get("merged")]
    answers = rows.premerge_work(args.drive, read, candidates)
    for pr in candidates:
        p_yes = answers.get(pr, {}).get("p_yes")
        print(f"{pr} {'unanswered' if p_yes is None else p_yes} {'moves' if p_yes is not None and p_yes <= rows.CLEARLY_NOT_PREMERGE else 'held'} {read[pr].get('title') or ''}")


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


def want(args: argparse.Namespace) -> None:
    print(stager.want(args.drive, args.lane))


def candidates(args: argparse.Namespace) -> None:
    print(json.dumps(stager.candidates(args.drive, args.reach)[: args.limit], indent=2))


def decide(args: argparse.Namespace) -> None:
    print(stager.decide(args.drive, args.cut, json.loads(Path(args.file).read_text() if args.file != "-" else input())))


def targets(args: argparse.Namespace) -> None:
    print(json.dumps(stager.targets(args.drive, args.target)))


def break_glass(args: argparse.Namespace) -> int:
    """Run one command under the repository's access grant for a stack, for one lane, through its AccessProvider with the reason, holding only the credentials the grant mints, which end with the grant: the grant is recorded the moment it starts on the lane's log and in breakglass.jsonl, with the lane, stack, reason, command and grant link, so every use of production access has a record. The command's exit code."""
    from drive import providers, reads

    command = args.command

    def started(link: str) -> None:
        record = {"at": store.stamp(), "lane": args.lane, "stack": args.stack, "reason": args.reason, "command": command, "grant": link}
        with (store.drive_dir(args.drive) / "breakglass.jsonl").open("a") as kept:
            kept.write(json.dumps(record) + "\n")
        store.update(args.drive, args.lane, lambda lane: store.note(lane, f"break-glass grant {link} on {args.stack} for: {shlex.join(command)}; reason: {args.reason}"))

    try:
        with providers.at(Path(store.config(args.drive)["checkout"])).access.grant(args.stack, args.reason, f"{args.drive} {args.lane}", started) as granted:
            print(f"break-glass grant {granted.link} passed; running {command[0]} {reads.held(granted)}", file=sys.stderr, flush=True)
            return reads.run_granted(command, granted)
    except (reads.ReadError, providers.ProviderError) as error:
        raise store.Refused(f"break-glass: {error}") from None


def release(args: argparse.Namespace) -> None:
    words = args.words[1:] if args.words[:1] == ["--"] else args.words
    print(releases.start(args.drive, args.lane, words, args.ask or [], args.leaves_out or [], args.reporter or [], args.also or []))


def result(args: argparse.Namespace) -> None:
    print(stager.result(args.drive, args.cut, json.loads(Path(args.file).read_text())))


def main() -> int:
    from drive import reads

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
    verb = verbs.add_parser("premerge", help="ask the drive config's premerge_question of every open PR of a premerge lane now and print each answer and whether it would take its staging test before merge, writing nothing")
    verb.set_defaults(run=premerge)
    verb = verbs.add_parser("want", help="queue the staging test of the lane's pending stack as a work item; prints the event to wait on, work:<id>, or after_merge")
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
    verb = verbs.add_parser("targets", help="set the drive's staging targets, each '<env>' or '<env> <generation>', refused when one does not exist on the default branch")
    verb.add_argument("target", nargs="+")
    verb.set_defaults(run=targets)
    verb = verbs.add_parser("break-glass", help="run one command under the repository's access grant for a stack, for one lane, with a grant record on the lane and in breakglass.jsonl")
    verb.add_argument("--lane", required=True)
    reads.break_glass_options(verb)
    verb.set_defaults(run=break_glass)
    verb = verbs.add_parser("release", help="start one release for a lane through the release provider, refused when its plan pulls in a component not named with --with or an open ask of the lane is neither shipped with --ask nor left out with --leaves-out; prints the release to wait on")
    verb.add_argument("--lane", required=True)
    verb.add_argument("--ask", action="append", help="the ts of an ask of the lane this release ships; repeatable")
    verb.add_argument("--leaves-out", action="append", help="\"<ts>: <why>\" for an open ask this release leaves out; repeatable")
    verb.add_argument("--reporter", action="append", help="the thread of a person who reported what this release fixes, told once it is live; repeatable")
    verb.add_argument("--with", dest="also", action="append", help="a component the plan pulls in that the release means to carry; repeatable")
    verb.add_argument("words", nargs=argparse.REMAINDER, help="the release start's own words after --, such as its targets and --env")
    verb.set_defaults(run=release)
    verb = verbs.add_parser("result", help="write each stack's result for a cut, which wakes its lane")
    verb.add_argument("--cut", required=True)
    verb.add_argument("file")
    verb.set_defaults(run=result)
    args = parser.parse_args()
    return args.run(args) or 0
