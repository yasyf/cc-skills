"""Each thing that happens, handled in the call that reports it, by his words "everything used to be fast bc it was event based": his message reaches its owner in the call that hears it, and his tap starts the lander in the call that writes it. The supervisor's pass only replays what a source lost."""

import json
from pathlib import Path

from lane import owner as owners
from lane import store


def deliver(drive: str, lane_id: str, text: str) -> str:
    """Put text before the lane's owner now, whatever its process is doing: a running owner gets it as a message, which Claude Code queues for its next tool round when it is inside one and starts a turn when it is between turns; a waiting or parked lane was woken by the write that carried his words, and a pass for that lane alone starts its owner in the same call. What it did."""
    lane = store.load(drive, lane_id)
    if lane["state"] == store.RUNNING and (owner := lane.get("owner")) and owner.get("job"):
        owners.deliver(owner, text)

        def marking(lane: dict) -> None:
            if lane.get("owner"):
                lane["owner"]["nudged"] = store.stamp()

        store.update(drive, lane_id, marking, leaving_unreplied=True)
        return f"delivered to its running owner {owner['job']}"
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return f"started its owner ({store.load(drive, lane_id)['state']})"


def words_for(lane_id: str, words: str, link: str | None, ts: str) -> str:
    return f"His words to lane {lane_id}, just now{', ' + link if link else ''} (ts {ts}), not answered yet; answer them with `lane say --re {ts}` before anything else:\n{words}"


def message(drive: str, lane_id: str, words: str, link: str | None, ts: str) -> str:
    """His message on one lane: written as an ask, then delivered in the same call."""
    from lane import verbs

    if not verbs.message(drive, lane_id, words, link, ts):
        return f"{lane_id}: seen before"
    return f"{lane_id}: 1 new, {deliver(drive, lane_id, words_for(lane_id, words, link, ts))}"


def landing(drive: str, keys: list[str]) -> list[str]:
    """Land the stack holding each PR in this call, by lane-land's land_stack, which holds one it cannot land yet and says why."""
    from lane import cli as lane_land

    lanes = store.load_all(drive)
    return [f"{key}: {lane_land.land_stack(drive, lane_id, key)}" for key in keys for lane_id, lane in lanes.items() if lane["state"] != store.PARKED and any(f"{row['repo']}#{row['number']}" == key for row in lane["prs"])]


def tapped(drive: str, keys: list[str]) -> str:
    """His Approve or Hold written on PRs: each one's stack is landed in the same call, so a green approved stack is submitted within seconds, not at the next pass."""
    return f"his tap on {', '.join(keys)}; " + "; ".join(landing(drive, keys))


CURSOR = "deliveries.cursor"


def deliveries_path() -> Path:
    from drive import receiver

    return receiver.deliveries_path()


def new_deliveries(drive: str) -> list[dict]:
    """GitHub's deliveries the receiver took since this drive last read them, from its log; the drive's cursor moves past them."""
    found = deliveries_path()
    cursor = store.drive_dir(drive) / CURSOR
    end = found.stat().st_size if found.is_file() else 0
    start = int(cursor.read_text()) if cursor.is_file() else end
    if start > end:
        start = 0
    if start == end:
        cursor.write_text(str(end))
        return []
    with found.open("rb") as log:
        log.seek(start)
        chunk = log.read(end - start)
    whole = chunk[: chunk.rfind(b"\n") + 1]
    cursor.write_text(str(start + len(whole)))
    return [json.loads(line) for line in whole.decode().splitlines() if line.strip()]


def named_keys(drive: str, deliveries: list[dict]) -> set[tuple[str, int]]:
    """The lane PRs the deliveries name: by number, by the head a check or status ran at, and by the branch a status is on. A push to a base names none, since every push to the default branch would name every lane PR; the pass's one GitHub read keeps mergeability."""
    from lane import page

    seen = page.view(drive)["prs"]
    rows = {(row["repo"], row["number"]) for lane in store.load_all(drive).values() for row in lane["prs"]}
    keys = set()
    for delivery in deliveries:
        repo = delivery.get("repo")
        mine = {key for key in rows if key[0] == repo}
        keys |= {key for key in mine if key[1] in (delivery.get("numbers") or [])}
        keys |= {key for key in mine if (pull := seen.get(f"{key[0]}#{key[1]}")) and (pull.get("head") == delivery.get("head") or pull.get("branch") in (delivery.get("branches") or []))}
    return keys


def github(drive: str) -> list[str]:
    """Each GitHub delivery since the last, handled now: the lane PRs it names read once, kept on his page, each lane waiting on one of them woken when its fact holds, the queue given each fix they now need, and the lander started on them; what it did."""
    if not (deliveries := new_deliveries(drive)) or not (keys := named_keys(drive, deliveries)):
        return []
    from lane import facts, page, verbs, work

    prs = facts.read_prs(keys, commits=1)
    page.merge_view(drive, prs, store.now())
    said = [f"{len(deliveries)} deliveries named {len(keys)} lane PRs"]
    for lane_id, lane in store.load_all(drive).items():
        if lane["state"] != store.WAITING:
            continue
        held = [event for event in lane["wait"]["on"] if event["kind"] in ("pr_merged", "check") and facts.pr_ref(event["ref"]) in prs and facts.holds(drive, lane, event, prs)]
        if held:
            verbs.wake(drive, lane_id, f"GitHub just said {', '.join(event['kind'] + ' ' + event['ref'] for event in held)}")
            said.append(f"woke {lane_id}")
    said += work.adds(drive)
    said += landing(drive, [f"{repo}#{number}" for (repo, number), pull in prs.items() if pull["state"] == "OPEN" and pull.get("rollup") == "SUCCESS"])
    return said


GITHUB_LOCK = "github-events.lock"


def handle_github(drive: str) -> None:
    """Handle the deliveries in a process of their own, one at a time per drive, so the supervisor's loop never waits on a GitHub read or a landing; a delivery that arrives while one runs is read by the next."""
    import subprocess

    from lane import cli as lane_land

    with lane_land.exclusive(store.drive_dir(drive) / GITHUB_LOCK) as free:
        if not free:
            return
    log = store.drive_dir(drive) / "github-events.log"
    with log.open("a") as out:
        subprocess.Popen([str(store.launcher(drive)), "--drive", drive, "github-events"], stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)


def receiving(drive: str) -> list[str]:
    """Keep GitHub delivering to this Mac for every lane repository: the old drive's receiver kept under launchd behind its Tailscale Funnel path, and a webhook of it on each lane repository this account administers; what it changed."""
    from drive import receiver

    receiver.keep()
    repos = {row["repo"] for lane in store.load_all(drive).values() for row in lane["prs"]}
    return [f"hooked {repo}" for repo in receiver.unhooked(repos) if receiver.hook(repo)]
