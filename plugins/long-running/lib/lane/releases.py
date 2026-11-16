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
        body = {"ref": ref, "name": record.get("name"), "status": record["status"], "commit": record.get("commit"), "carries": record.get("carries"), "failures": record.get("failures"), "post": release.post(ref), "at": store.stamp()}
        body |= {"state": "started"} if record["status"] in STARTED else {"state": RESULTS[record["status"]], "result": RESULTS[record["status"]]}
        path = folder / f"{ref}.json"
        if path.is_file() and {field: value for field, value in json.loads(path.read_text()).items() if field != "at"} == {field: value for field, value in body.items() if field != "at"}:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        fresh = path.with_name(f".{ref}.{os.getpid()}.tmp")
        fresh.write_text(json.dumps(body, indent=1) + "\n")
        fresh.replace(path)
        count += 1
    return count
