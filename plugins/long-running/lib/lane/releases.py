"""The release result files a lane's `release` wait reads, releases/<ref>.json, written from the release tool's own record of each release and the Slack post that announces it, never its build page."""

import json
import os
from pathlib import Path

from lane import store

FOLDER = "releases"
STARTED = ("queued", "running", "held")
RESULTS = {"converged": "live", "partial": "failed", "failed": "failed"}


def waited(lanes: dict[str, dict]) -> list[str]:
    """Each release a waiting lane's wait names, by its ref, the release's name or build."""
    return list(dict.fromkeys(event["ref"] for lane in lanes.values() if lane["state"] == store.WAITING for event in (lane.get("wait") or {}).get("on", []) if event["kind"] == "release"))


def write(drive: str) -> int:
    """Write releases/<ref>.json for each release a lane waits on whose record moved: its state, started while the tool queues, runs or holds it, else its result, live once it converged on every target it carries and failed when it failed or converged on only some, with its failures, its commit and the permalink of its Slack post; a release whose result is kept is read no more. The count written."""
    from drive import providers

    checkout = store.config(drive)["checkout"]
    folder = store.drive_dir(drive) / FOLDER
    pending = [ref for ref in waited(store.load_all(drive)) if not ((path := folder / f"{ref}.json").is_file() and "result" in json.loads(path.read_text()))]
    if not checkout or not pending:
        return 0
    release = providers.at(Path(checkout)).release
    count = 0
    for ref in pending:
        if not (record := release.status(ref)):
            continue
        path = folder / f"{ref}.json"
        kept = json.loads(path.read_text()) if path.is_file() else {}
        body = kept | {"ref": ref, "name": record.get("name"), "status": record["status"], "commit": record.get("commit"), "carries": record.get("carries"), "failures": record.get("failures"), "post": release.post(ref), "at": store.stamp()}
        body |= {"state": "started"} if record["status"] in STARTED else {"state": RESULTS[record["status"]], "result": RESULTS[record["status"]]}
        if kept and {field: value for field, value in kept.items() if field != "at"} == {field: value for field, value in body.items() if field != "at"}:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        fresh = path.with_name(f".{ref}.{os.getpid()}.tmp")
        fresh.write_text(json.dumps(body, indent=1) + "\n")
        fresh.replace(path)
        if "result" in body and kept.get("lane"):
            finished(drive, body)
            lived(drive, body)
        count += 1
    return count


def start(drive: str, lane_id: str, words: list[str], asks: list[str], leaves_out: list[str], reporters: list[str], also: list[str]) -> str:
    """Start one release for a lane through the repository's release provider, refusing it unless it is the release the lane means: its plan, read first and timed, pulls in no component the lane did not name with also, so a release applies only the stacks it names; and every open ask of the lane is either one it ships, named in asks by ts, or one it leaves out, named in leaves_out as `<ts>: <why>`, so the release says beforehand what it leaves out. The start's record, releases/<ref>.json, keeps the lane, the asks it ships and leaves out, the reporters' threads to tell once it finishes, the plan with its holds for his approve card, and how long the plan took; what started, or a store.Refused."""
    import shlex
    import time

    from drive import providers

    checkout = Path(store.config(drive)["checkout"])
    found = providers.at(checkout)
    began = time.monotonic()
    if (plan := found.release.plan(words)) is None or not found.release.start_command:
        raise store.Refused("the repository's release provider plans and starts no release")
    planned = round(time.monotonic() - began, 1)
    if pulled := [addition for addition in plan["additions"] if addition["component"] not in also]:
        raise store.Refused(f"the plan pulls in {'; '.join(f'{addition['component']} ({addition['why']})' for addition in pulled)} beside what you named: a release applies only the stacks it names, so name each with --with <component>, or narrow the release so it pulls in none")
    left = {entry.partition(":")[0].strip(): entry.partition(":")[2].strip() for entry in leaves_out}
    lane = store.load(drive, lane_id)
    if unnamed := [ask["ts"] for ask in lane.get("asks", []) if not ask.get("done") and ask.get("ts") and ask["ts"] not in asks and ask["ts"] not in left]:
        raise store.Refused(f"open asks {', '.join(unnamed)} are neither shipped nor left out: name each this release ships with --ask <ts>, and each it leaves out with --leaves-out \"<ts>: <why>\", so it says beforehand what it leaves out")
    said = found.release.run(f"{found.release.start_command} {shlex.join(words)}")
    if not (links := found.build.finditer(said)):
        raise store.Refused(f"the release started but its output names no build to follow: {' '.join(said.split())[:300]}")
    ref = links[-1]["number"]
    body = {"ref": ref, "lane": lane_id, "words": words, "asks": asks, "leaves_out": left, "reporters": reporters, "plan": plan, "planned_seconds": planned, "started_at": store.stamp()}
    folder = store.drive_dir(drive) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ref}.json").write_text(json.dumps(body, indent=1) + "\n")
    store.update(drive, lane_id, lambda kept: store.note(kept, f"release {ref} started ({' '.join(words)}), planned in {planned} s, shipping asks {', '.join(asks) or 'none'}, leaving out {', '.join(left) or 'none'}"))
    holds = "; ".join(f"{hold['subject']} when {hold['when']}: {', '.join(hold['subjects'])}" for hold in plan["holds"]) or "none"
    return f"release {ref} started at {plan['commit'][:12]}, planned in {planned} s; it applies {', '.join([*plan['picked'], *also])}; its approve step holds: {holds}. Wait on release:{ref}."


def finished(drive: str, body: dict) -> None:
    """Tell the lane that started a release, once its result is written, what it shipped and to whom it owes word: the result, what it carried, the asks it shipped and left out, and each reporter's thread to tell once, in that thread, that the fix is live, so no reporter waits on a promise the release kept."""
    from lane import verbs

    reporters = ", ".join(body.get("reporters") or []) or "none named"
    left = "; ".join(f"{ts}: {why}" for ts, why in (body.get("leaves_out") or {}).items()) or "none"
    verbs.handover(drive, body["lane"], f"- release {body['ref']} is {body['result']}, carrying {', '.join(body.get('carries') or []) or 'nothing it names'}; it shipped asks {', '.join(body.get('asks') or []) or 'none'} and left out {left}. Tell each reporter once, in their thread, when it is live: {reporters}.", "lane-land")


def lived(drive: str, body: dict) -> list[str]:
    """Each lane PR a live production release carried, by its merge commit being in the release's commit, marked live on its row with the release, and given an e2e work item that proves the change on the live build, by his words that merged is not live: a release to a rehearsal env proves nothing live. The work items added."""
    import subprocess

    from drive import providers

    from lane import landing, rows, work

    checkout = Path(store.config(drive)["checkout"])
    env = next((word for before, word in zip(body["words"], body["words"][1:]) if before == "--env"), None)
    if body.get("result") != "live" or not body.get("commit") or env in providers.at(checkout).release.rehearsal_envs:
        return []
    read = rows.view(drive)
    git = ["git", "-C", str(checkout)]
    subprocess.run([*git, "fetch", "-q", "--no-tags", "origin", body["commit"]], capture_output=True, check=False)
    added = []
    for lane_id, lane in store.load_all(drive).items():
        for row in lane["prs"]:
            facts_of = read.get(landing.key(row)) or {}
            if row.get("live") or not facts_of.get("merge_commit") or subprocess.run([*git, "merge-base", "--is-ancestor", facts_of["merge_commit"], body["commit"]], capture_output=True, check=False).returncode:
                continue
            key = landing.key(row)
            store.update(drive, lane_id, lambda kept, key=key: marked(kept, key, {"release": body["ref"], "commit": body["commit"], "at": store.stamp()}))
            added.append(work.add(drive, "e2e", lane_id, key, f"Prove {key} on the live build: release {body['ref']} carried it to {env or 'production'} at {body['commit'][:12]}. Exercise the change there as the person who asked for it would, read its logs and traces for the path it changed, and finish this item with what you saw, with links; a failure goes back to its lane with what broke.", head=facts_of["merge_commit"], touches=[key.rpartition("#")[0]], by="lane-land")["id"])
    return added


def marked(lane: dict, key: str, live: dict) -> None:
    for row in lane["prs"]:
        if f"{row['repo']}#{row['number']}" == key:
            row["live"] = live
