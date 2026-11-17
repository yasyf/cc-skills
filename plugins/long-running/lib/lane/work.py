"""The work queue, by his words "a queue of jobs to be done and then workers (local or mac box or sprite) pulling groups of appropriate work off the queue continosl y": anything a lane needs done is one item in one file under the drive's work folder, a worker with no lane of its own pulls the top item with everything it can finish in the same sitting, and the item's lane is woken the moment it is done. An item is always queued, taken by a live worker, or done, so nothing waits on a session that happened to notice it."""

import fcntl
import hashlib
import json
import os
import secrets
import shlex
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from lane import owner as owners
from lane import page, rank, store
from lane import session as shapes

FOLDER = "work"
WORKERS = "workers"
KINDS = ("rebase", "red", "review", "carry", "ask", "e2e", "staging-before", "staging-after")
URGENCY = ("red", "ask", "carry", "staging-before", "review", "rebase", "e2e", "staging-after")
FIX_KINDS = {"conflict": "rebase", "red": "red", "review": "review"}
QUEUED, TAKEN, DONE, FAILED = "queued", "taken", "done", "failed"
QUIET = timedelta(minutes=30)
WORKERS_DEFAULT = 8
RETRIES = 1
RULES = Path(__file__).with_name("work.md")
LANE = Path(__file__).resolve().parents[2] / "bin" / "lane"


def folder(drive: str) -> Path:
    found = store.drive_dir(drive) / FOLDER
    found.mkdir(parents=True, exist_ok=True)
    return found


def item_id(kind: str, subject: str) -> str:
    """One item per kind and subject, so a second add of the same work, at a new head included, is the same item."""
    return f"w-{hashlib.sha1(f'{kind}:{subject}'.encode()).hexdigest()[:8]}"


def path(drive: str, item: str) -> Path:
    return folder(drive) / f"{item}.json"


@contextmanager
def held(drive: str, item: str) -> Iterator[None]:
    with (folder(drive) / f".{item}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def written(drive: str, item: dict) -> None:
    fresh = path(drive, item["id"]).with_name(f".{item['id']}.{os.getpid()}.tmp")
    fresh.write_text(json.dumps(item, indent=1) + "\n")
    fresh.replace(path(drive, item["id"]))


def load(drive: str, item: str) -> dict | None:
    found = path(drive, item)
    return json.loads(found.read_text()) if found.is_file() else None


def changed[T](drive: str, item: str, change: Callable[[dict | None], T]) -> T:
    with held(drive, item):
        kept = load(drive, item)
        result = change(kept)
        if kept is not None:
            written(drive, kept)
        return result


def items(drive: str) -> list[dict]:
    return [json.loads(found.read_text()) for found in sorted(folder(drive).glob("w-*.json"))]


def add(drive: str, kind: str, lane_id: str, subject: str, words: str, serves: int | None = None, head: str | None = None, touches: list[str] | None = None, runs_on: str = "local", by: str = "owner", again: bool = False) -> dict:
    """Queue one item, or update the one this kind and subject already has: its words and head while it is queued, queued again at a new head once done or failed, or at the same head with again when what failed it is fixed, and never touched while a worker holds it, since that worker's own push moves the head. A worker is started at once when none is free."""
    if kind not in KINDS:
        raise store.Refused(f"a work item's kind is one of {', '.join(KINDS)}, not {kind!r}")
    if kind == "staging-before" and lane_id not in store.config(drive)["premerge_lanes"]:
        raise store.Refused(f"{lane_id} is not one of the drive config's premerge_lanes, so {subject} merges on his Approve and is tested on staging after the merge: queue staging-after once it merges, never staging-before")
    store.load(drive, lane_id)
    found = item_id(kind, subject)
    moment = store.stamp()

    def adding(_: dict | None) -> dict:
        kept = load(drive, found)
        if kept and kept["state"] == TAKEN:
            return kept
        if kept and kept["state"] in (DONE, FAILED) and not again and (head is None or kept.get("head") == head):
            return kept
        fresh = {"id": found, "kind": kind, "lane": lane_id, "subject": subject, "words": words, "serves": serves, "head": head, "touches": touches or [], "runs_on": runs_on, "state": QUEUED, "added": (kept or {}).get("added") or moment, "queued": moment, "by": by, "rounds": (kept or {}).get("rounds", 0) + (1 if kept else 0), "history": (kept or {}).get("history", [])[-10:]}
        if kept and kept["state"] in (DONE, FAILED):
            fresh["history"].append({k: kept.get(k) for k in ("state", "head", "result", "why", "ended", "worker")})
        written(drive, fresh)
        return fresh

    with held(drive, found):
        item = adding(None)
    if item["state"] == QUEUED and store.live(drive):
        staff(drive)
    return item


def related(item: dict, other: dict) -> bool:
    """Whether a worker could take other in the same sitting as item: the same lane, a file or service both touch, or, for staging, the same target."""
    return other["lane"] == item["lane"] or bool(set(item.get("touches") or []) & set(other.get("touches") or []))


def parked_lanes(drive: str) -> set[str]:
    """The lanes he parked, whose items wait in the queue untouched until he unparks them: parked means nothing runs for it."""
    return {lane_id for lane_id, lane in store.load_all(drive).items() if lane["state"] == store.PARKED}


def pull(drive: str) -> dict | None:
    """The top queued item: an incident lane's first, then by URGENCY of its kind, so a red staging promotion never waits behind rebases and after-merge tests, then by its lane's place in the priority queue and when it was queued, with every other queued item a worker could finish beside it; None when nothing is queued."""
    order = {lane: index for index, lane in enumerate(rank.last(drive).get("order", []))}
    lanes = store.load_all(drive)
    parked = parked_lanes(drive)
    queued = sorted((item for item in items(drive) if item["state"] == QUEUED and item["lane"] not in parked), key=lambda item: (not (lanes.get(item["lane"]) or {}).get("incident"), URGENCY.index(item["kind"]), order.get(item["lane"], len(order)), item["queued"]))
    if not queued:
        return None
    top = queued[0]
    return {"top": top, "with": [item for item in queued[1:] if related(top, item)], "rules": str(RULES)}


def take(drive: str, worker: str, ids: list[str]) -> list[str]:
    """Lease the items a worker chose to the worker, each only while still queued; the ids it now holds."""
    moment = store.stamp()
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not worker_path(drive, worker).is_file():
        worker_path(drive, worker).write_text(json.dumps({"id": worker, "job": None, "session": session, "started": moment}) + "\n")
    taken = []
    for found in ids:

        def taking(item: dict | None, found: str = found) -> bool:
            if item is None:
                raise store.Refused(f"no work item {found}")
            if item["state"] != QUEUED:
                return False
            item.update(state=TAKEN, worker=worker, session=session, taken=moment)
            return True

        if changed(drive, found, taking):
            taken.append(found)
    return taken


def done(drive: str, found: str, result: str, failed: bool = False) -> dict:
    """Finish one item with what came of it, or why it failed, and wake its lane in the same call: a lane waiting on work:<id> is woken, a running one reads it before its next step."""

    def finishing(item: dict | None) -> dict:
        if item is None:
            raise store.Refused(f"no work item {found}")
        if item["state"] in (DONE, FAILED):
            raise store.Refused(f"work item {found} is already {item['state']}: {item.get('result') or item.get('why')}")
        item.update(state=FAILED if failed else DONE, ended=store.stamp(), **({"why": result} if failed else {"result": result}))
        if failed and item.get("retries", 0) < RETRIES:
            item["history"] = [*item.get("history", []), {"state": FAILED, "why": result, "worker": item.get("worker"), "at": item["ended"]}][-10:]
            item.update(state=QUEUED, queued=store.stamp(), worker=None, session=None, taken=None, retries=item.get("retries", 0) + 1, words=f"{item['words']}\n\nAn earlier worker failed this item: {result}. Take a different approach to what stopped it, or fail it with why it cannot be done.")
        return dict(item)

    item = changed(drive, found, finishing)
    if item["state"] == QUEUED:
        told(drive, item, f"Work item {found} ({item['kind']} {item['subject']}) failed once and is queued again with why: {result}")
        staff(drive)
    else:
        told(drive, item, f"Work item {found} ({item['kind']} {item['subject']}) {'failed twice; re-plan it, with `lane work add` for a different approach or `lane block` naming what stops it' if failed else 'is done'}: {result}")
    return item


def told(drive: str, item: dict, text: str) -> None:
    from lane import verbs

    lane = store.load(drive, item["lane"])
    if lane["state"] == store.WAITING:
        verbs.wake(drive, item["lane"], text)
    elif lane["state"] == store.RUNNING:
        with store.note_path(drive, item["lane"]).open("a") as note:
            note.write(f"{text}\n\n")


def holding(drive: str, lane_id: str) -> set[str]:
    """The subjects a worker holds for the lane, which the lander leaves alone until the item ends."""
    return {item["subject"] for item in items(drive) if item["lane"] == lane_id and item["state"] == TAKEN}


def worker_path(drive: str, worker: str) -> Path:
    found = folder(drive) / WORKERS
    found.mkdir(exist_ok=True)
    return found / f"{worker}.json"


def workers(drive: str) -> list[dict]:
    found = folder(drive) / WORKERS
    return [json.loads(kept.read_text()) for kept in sorted(found.glob("*.json"))] if found.is_dir() else []


def alive(worker: dict) -> bool:
    """Whether a worker still holds one of the drive's `workers` slots: a job that ended, or reported itself blocked after sending its items back, frees its slot at once, so a worker waiting on something only he can clear never keeps queued work from being staffed."""
    job = owners.record(owners.config_dir() / "jobs" / worker["job"] / "state.json") if worker.get("job") else None
    if job is not None and job.get("state") in ("done", "stopped", "failed", "blocked"):
        return False
    active = owners.active_at({"session": worker["session"]}) if worker.get("session") else None
    return active is None and store.now() - store.parse(worker["started"]) < QUIET or active is not None and store.now() - active < QUIET


def expire(drive: str) -> list[str]:
    """Return to the queue each item whose worker ended or has written nothing for 30 minutes, and forget the worker; what went back."""
    gone = {worker["id"] for worker in workers(drive) if not alive(worker)}
    back = []
    for item in items(drive):
        if item["state"] == TAKEN and item.get("worker") in gone:

            def returning(kept: dict | None) -> bool:
                if not kept or kept["state"] != TAKEN:
                    return False
                kept["history"] = [*kept.get("history", []), {"state": "returned", "worker": kept.get("worker"), "at": store.stamp()}][-10:]
                kept.update(state=QUEUED, queued=store.stamp(), worker=None, session=None, taken=None)
                return True

            if changed(drive, item["id"], returning):
                back.append(f"{item['id']} back in the queue: its worker {item.get('worker')} ended or went quiet")
    for worker in gone:
        worker_path(drive, worker).unlink(missing_ok=True)
    return back


def brief(drive: str, worker: str) -> str:
    lane = f"{LANE} --drive {drive}"
    return (
        f"You are work-queue worker {worker} of drive {drive}, with no lane of your own. Read {RULES} first; it is his rules for what to take together. "
        f"Then loop: `{lane} work pull` shows the top item and what can go with it; take what you will finish in this sitting with `{lane} work take <id>... --worker {worker}`; do each item in a worktree of your own, never in this checkout; "
        f"finish each with `{lane} work done <id> --result \"<one line: what you pushed or found, with links>\"`, or `--failed \"<why>\"`; then pull again. "
        "Post nothing to Slack; the item's lane owner tells him. End when pull shows nothing."
    )


def launch(drive: str, cwd: Path) -> dict:
    """Start one worker as its own background session; its row."""
    worker = f"worker-{secrets.token_hex(3)}"
    from lane import fixes

    own = {"env": {"BASH_MAX_TIMEOUT_MS": str(owners.TOOL_SECONDS * 1000), "MCP_TOOL_TIMEOUT": str(owners.TOOL_SECONDS * 1000), "LANE_WORKER": worker, "LANE_DRIVE": drive}, "permissions": {"deny": fixes.DENY}, "enabledPlugins": {name: False for name in owners.old_drive_plugins()}, "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"{shlex.quote(str(store.launcher(drive)))} hook bash"}]}]}}
    argv = shapes.argv(store.config(drive), "builder", cwd, own, ["--bg"], ["--permission-mode", "bypassPermissions", "-n", worker, brief(drive, worker)])
    env = {name: value for name, value in os.environ.items() if name not in ("CLAUDE_CODE_SESSION_ID", "LANE_OWNER", "LONG_RUNNING_LANE", "LANE_TOKEN", "LANE_TOKEN_FILE", "LONG_RUNNING_JOB", "CLAUDE_LONG_RUNNING_LANE")}
    started = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=120)
    if started.returncode:
        raise store.Refused(f"claude --bg exited {started.returncode}: {(started.stderr or started.stdout).strip()[:300]}")
    job, session = owners.launched(started.stdout)
    row = {"id": worker, "job": job, "session": session, "started": store.stamp(), "cwd": str(cwd)}
    worker_path(drive, worker).write_text(json.dumps(row) + "\n")
    return row


def staff(drive: str) -> list[str]:
    """Start a worker for each queued item no free worker can take, up to the drive's `workers` count; what started."""
    if not store.live(drive):
        return []
    parked = parked_lanes(drive)
    queued = [item for item in items(drive) if item["state"] == QUEUED and item["lane"] not in parked]
    running = [worker for worker in workers(drive) if alive(worker)]
    idle = len(running) - len({item.get("worker") for item in items(drive) if item["state"] == TAKEN})
    room = min(len(queued) - max(idle, 0), store.config(drive).get("workers", WORKERS_DEFAULT) - len(running))
    started = []
    for item in queued[: max(room, 0)]:
        row = launch(drive, owners.cwd(drive, store.load(drive, item["lane"])))
        started.append(f"worker {row['id']} (job {row['job']}) for {item['id']}")
    return started


def adds(drive: str) -> list[str]:
    """The pass's reconcile of what GitHub shows into the queue: each PR he approved, or that a card asks him about at its head, with a conflict, a red check or no review at its head, as one item; skipped while a fix job from before the queue still works on it."""
    from lane import fixes

    seen = page.view(drive)["prs"]
    added = []
    for lane_id, lane in store.load_all(drive).items():
        if lane["state"] == store.PARKED:
            continue
        fixing = {fix["key"] for fix in fixes.running(lane)}
        for row in lane["prs"]:
            key = f"{row['repo']}#{row['number']}"
            pull_row = seen.get(key)
            tapped = row.get("tapped_head") or row.get("approved_head")
            if pull_row and pull_row["state"] == "OPEN" and tapped and pull_row["head"] != tapped and (row.get("carried") or {}).get("head") != pull_row["head"] and key not in fixing:
                item = add(drive, "carry", lane_id, key, carry_brief(drive, key, tapped, pull_row), head=pull_row["head"], touches=[row["repo"]], by="supervisor")
                added.append(f"{item['id']} carry {key} at {pull_row['head'][:7]}")
            if key in fixing or not fixes.wanted(row, pull_row) or not (kind_of := fixes.kind(row, pull_row)):
                continue
            before = load(drive, item_id(FIX_KINDS[kind_of], key))
            item = add(drive, FIX_KINDS[kind_of], lane_id, key, fixes.brief(kind_of, row["repo"], row["number"], pull_row), head=pull_row["head"], touches=[row["repo"]], by="supervisor")
            if not before or before.get("head") != item.get("head") or before["state"] != item["state"]:
                added.append(f"{item['id']} {item['kind']} {key} at {pull_row['head'][:7]}")
    return added


def carry_brief(drive: str, key: str, approved: str, pull: dict) -> str:
    return (
        f"His Approve of {key} was his tap at {approved[:12]}, the head he saw; its head is now {pull['head'][:12]} (branch {pull['branch']}). Judge, by his words \"the approve carries if its semantically similar, as determined by the model (if its going to make a semantically incompatible change it needs to revoke the approval)\", whether what changed between the two is what he approved: "
        f"`git fetch origin {approved} {pull['head']} {pull['base']}`, then `git range-diff origin/{pull['base']}...{approved} origin/{pull['base']}...{pull['head']}` and read every hunk that differs. A clean rebase or a fix that keeps the same behavior is compatible; a conflict resolved by changing behavior, new behavior, or a dropped change is not. "
        f"Record it with `{LANE} --drive {drive} carry {key} --head {pull['head']} --compatible \"<one line why>\"` or `--incompatible \"<what changed, in his terms>\"`, then finish this item with the same line. Push nothing."
    )


def listed(drive: str) -> list[dict]:
    """Every item not finished more than a day ago, for his page and `lane work list`."""
    cut = store.now() - timedelta(days=1)
    return [{k: item.get(k) for k in ("id", "kind", "lane", "subject", "state", "worker", "queued", "taken", "ended", "result", "why", "serves")} for item in items(drive) if item["state"] in (QUEUED, TAKEN) or store.parse(item.get("ended") or item["queued"]) > cut]
