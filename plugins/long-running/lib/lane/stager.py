"""The staging tester's facts and writes: the dangerous stacks waiting on a proof, in rank order, each with what the tester needs to choose its batch, the env's lease, and each stack's result.

The tester is an agent (agents/stager.md). Code here gathers facts and records results; it never sizes a cut or packs stacks. Until cutover writes the lanes/<drive>/live marker, store.live, every verb runs in shadow: it reads the old drive's approved dangerous stacks, takes no lease, starts no cut, merges nothing, and keeps the tester's decision under shadow/.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from lane import landing, rows, store

SHADOW = "shadow"
ENV_LEASE = "staging-env.json"
CUTS = "cuts"
DECISIONS = "decisions"
RANK = "rank.json"
REACH_READS = 12


class ShadowRefusal(SystemExit):
    pass


def lanes_dir(drive: str) -> Path:
    return store.drive_dir(drive)


def live(drive: str) -> bool:
    return store.live(drive)


def refuse_in_shadow(drive: str, what: str) -> None:
    if not live(drive):
        raise ShadowRefusal(f"shadow: {what} waits for cutover, which writes {lanes_dir(drive) / store.LIVE}")


def written(path: Path, body: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    temporary.rename(path)
    return path


def lane_candidates(drive: str) -> list[dict]:
    """Each stack of a lane holding a dangerous PR with no proof at the stack's top head, its PRs green, in rank.json's order of lanes."""
    folder = lanes_dir(drive)
    order = json.loads((folder / RANK).read_text())["order"]
    read = rows.view(drive)
    found = []
    for rank, lane_id in enumerate(order):
        lane = rows.seen(store.load(drive, lane_id), read)
        for stack in landing.stacks(lane):
            open_rows = [row for row in stack if not row.get("merged")]
            if any(landing.judged(row) and landing.dangerous(row) for row in open_rows) and not landing.proven(open_rows):
                found.append(
                    {
                        "rank": rank,
                        "lane": lane_id,
                        "stack": [landing.key(row) for row in open_rows],
                        "top_head": open_rows[-1]["head"],
                        "green": all(row.get("checks") == "pass" for row in open_rows),
                        "approved": all(row["approved_head"] == row["head"] for row in open_rows),
                        "held": [landing.key(row) for row in open_rows if row["held"]],
                        "dangerous": {landing.key(row): row["dangerous"] for row in open_rows if landing.judged(row) and landing.dangerous(row)},
                    }
                )
    return found


def shadow_candidates(drive: str) -> list[dict]:
    """The old drive's dangerous stacks, read only: each stack of open PRs one of which owes a staging test, approved stacks first, then by the best class of their orders, then by his oldest Approve, the shadow's stand-in for rank.json."""
    from drive import land, state

    data = state.load(drive)
    stacks = land.landing_stacks(data)
    seen: set[tuple[str, ...]] = set()
    found = []
    for key, pr in data["prs"].items():
        if pr["state"] != "open" or not (pr["owes_staging"] or state.staging_runs(data, pr)):
            continue
        members = tuple(member for member in stacks.get(key, (key,)) if data["prs"][member]["state"] == "open")
        if members in seen:
            continue
        seen.add(members)
        rows = [data["prs"][member] for member in members]
        taps = [row["tap"] for row in rows if land.approved(row) and row["tap"]["head"] == row["head"]]
        found.append(
            {
                "stack": list(members),
                "top_head": rows[-1]["head"],
                "green": all(row["checks"] == "pass" for row in rows),
                "approved": len(taps) == len(rows),
                "approved_at": min((tap["at"] for tap in taps), default=None),
                "held": [member for member in members if land.held(data, [member])],
                "dangerous": [member for member, row in zip(members, rows, strict=True) if row["owes_staging"]],
                "staging_parts": [run for row in rows for run in land.staged(data, row)],
                "class": min(state.class_rank(data, data["jobs"][row["job"]]) for row in rows if row["job"] in data["jobs"]) if any(row["job"] in data["jobs"] for row in rows) else len(state.CLASSES),
                "orders": sorted({row["order"] for row in rows}),
                "titles": {member: row["title"] for member, row in zip(members, rows, strict=True)},
            }
        )
    found.sort(key=lambda stack: (not stack["approved"], stack["class"], stack["approved_at"] or "~"))
    for rank, stack in enumerate(found):
        stack["rank"] = rank
    return found


def reach(drive: str, candidates: list[dict]) -> None:
    """Add to the first REACH_READS candidates each PR's changed files and what its release select reaches, its components, releases and staged targets, from which the tester judges where the stack is tested and which stacks overlap."""
    from drive import cli, github, providers, state

    found = providers.of(state.load(drive))
    shown = candidates[:REACH_READS]
    keys = [key for stack in shown for key in stack["stack"]]
    paths = github.paths(keys)
    for stack in shown:
        stack["paths"] = {key: paths[key][1] for key in stack["stack"] if key in paths}
        stack["reach"] = {key: {field: read[field] for field in ("releases", "staged", "touched")} for key in stack["stack"] if (read := cli.pr_reach(key, found))}


def candidates(drive: str, reached: bool) -> list[dict]:
    found = lane_candidates(drive) if live(drive) else shadow_candidates(drive)
    if reached:
        reach(drive, found)
    return found


def lease(drive: str) -> dict | None:
    path = lanes_dir(drive) / ENV_LEASE
    return json.loads(path.read_text()) if path.exists() else None


def take(drive: str, holder: str, until: datetime, moment: datetime) -> dict:
    """Take the staging env's lease for holder until the time given, refused while another holder's lease runs or in shadow, where the old stage-test worker holds the env."""
    refuse_in_shadow(drive, "taking the staging env")
    held = lease(drive)
    if held and held["holder"] != holder and datetime.fromisoformat(held["until"]) > moment:
        raise SystemExit(f"the staging env is held by {held['holder']} until {held['until']}")
    body = {"holder": holder, "until": until.isoformat(), "since": (held or {}).get("since") if held and held["holder"] == holder else moment.isoformat()}
    written(lanes_dir(drive) / ENV_LEASE, body)
    return body


def release(drive: str, holder: str) -> None:
    held = lease(drive)
    if held and held["holder"] == holder:
        (lanes_dir(drive) / ENV_LEASE).unlink()


def decide(drive: str, cut: str, body: dict) -> Path:
    """Keep the tester's batch: the stacks it takes, each with the target it tests on, and those it left out with why. In shadow it lands under shadow/decisions and starts nothing."""
    folder = lanes_dir(drive) / (CUTS if live(drive) else f"{SHADOW}/{DECISIONS}")
    return written(folder / f"{cut}.json", body | {"cut": cut, "shadow": not live(drive)})


def result(drive: str, cut: str, stacks: dict[str, dict]) -> Path:
    """Write a cut's result, the file lane-core's supervisor reads to wake each waiting lane, after putting each stack's proof on its top PR's row by rows.prove: stacks keyed by bottom PR, each {stack: [PRs bottom first], head: its top head, target, result: pass or fail}."""
    refuse_in_shadow(drive, "recording a cut's result")
    path = lanes_dir(drive) / CUTS / f"{cut}.json"
    kept = json.loads(path.read_text()) if path.exists() else {}
    rows.prove(drive, cut, stacks)
    verdicts = {stack["result"] for stack in stacks.values()}
    return written(path, kept | {"stacks": stacks, "result": "pass" if verdicts == {"pass"} else "fail"})
