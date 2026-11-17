"""The staging tester's facts and writes: the dangerous stacks waiting on a proof, in rank order, each with what the tester needs to choose its batch, the env's lease, and each stack's result.

The tester is an agent (agents/stager.md). Code here gathers facts and records results; it never sizes a cut or packs stacks. Until cutover writes the lanes/<drive>/live marker, store.live, every verb runs in shadow: it reads the old drive's approved dangerous stacks, takes no lease, starts no cut, merges nothing, and keeps the tester's decision under shadow/.
"""

import json
import os
import subprocess
from pathlib import Path

from lane import landing, queue, rows, store

SHADOW = "shadow"
STAGING_ENV = "staging-env"
STAGER = "stager"
STAGER_MINUTES = 60
WANTS = "staging-wants"
PAGE = Path(__file__).resolve().parents[2] / "agents" / "stager.md"
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
    """Each stack of a lane holding a PR danger.md says needs a staging pass before merge with no proof at the stack's top head, its PRs green, in rank.json's order of lanes; then each PR his Approve merged with no staging proof yet, marked after_merge, whose test runs on its merged head, by his 14:33 ruling."""
    folder = lanes_dir(drive)
    order = json.loads((folder / RANK).read_text())["order"]
    read = rows.view(drive)
    asked = wants(drive)
    found = []
    for rank, lane_id in enumerate(order):
        lane = rows.seen(store.load(drive, lane_id), read)
        for stack in landing.stacks(lane):
            open_rows = [row for row in stack if not row.get("merged")]
            if any(landing.judged(row) and landing.dangerous(row) for row in open_rows) and not landing.proven(open_rows):
                found.append(
                    {
                        "rank": rank,
                        "wanted": (asked.get(lane_id) or {}).get("cut"),
                        "lane": lane_id,
                        "stack": [landing.key(row) for row in open_rows],
                        "top_head": open_rows[-1]["head"],
                        "green": all(row.get("checks") == "pass" for row in open_rows),
                        "approved": all(row["approved_head"] for row in open_rows),
                        "held": [landing.key(row) for row in open_rows if row["held"]],
                        "dangerous": {landing.key(row): row["dangerous"] for row in open_rows if landing.judged(row) and landing.dangerous(row)},
                    }
                )
    found += [{"rank": rank, "lane": lane_id, "stack": [landing.key(row)], "top_head": row["head"], "after_merge": True} for rank, lane_id in enumerate(order) for row in rows.seen(store.load(drive, lane_id), read)["prs"] if row.get("merged") and not row.get("staging") and row.get("approved_head")]
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

    found = providers.at(Path(checkout)) if (checkout := store.config(drive)["checkout"]) else providers.of(state.load(drive))
    shown = candidates[:REACH_READS]
    keys = [key for stack in shown for key in stack["stack"]]
    paths = github.paths(keys)
    for stack in shown:
        stack["paths"] = {key: paths[key][1] for key in stack["stack"] if key in paths}
        stack["reach"] = {key: {field: value for field, value in read.items() if field in ("releases", "staged", "touched")} for key in stack["stack"] if key in paths and (read := cli.pr_reach(key, found, paths[key]))}


def candidates(drive: str, reached: bool) -> list[dict]:
    found = lane_candidates(drive) if (lanes_dir(drive) / RANK).exists() else shadow_candidates(drive)
    if reached:
        reach(drive, found)
    return found


def take(drive: str, holder: str, lane_id: str, minutes: int) -> dict:
    """Ask lane-queue for the staging env for holder on behalf of the lane whose stack is the cut's first, granted in rank order, or taken from a lane that is no incident when that lane's incident hurts users; refused in shadow, where the old stage-test worker holds the env."""
    refuse_in_shadow(drive, "taking the staging env")
    return queue.take(drive, STAGING_ENV, holder, lane_id, minutes)


def renew(drive: str, holder: str, minutes: int) -> str:
    refuse_in_shadow(drive, "renewing the staging env")
    return queue.renew(drive, STAGING_ENV, holder, minutes)


def release(drive: str, holder: str) -> None:
    queue.release(drive, STAGING_ENV, holder)


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
    served(drive, stacks)
    verdicts = {stack["result"] for stack in stacks.values()}
    return written(path, kept | {"stacks": stacks, "result": "pass" if verdicts == {"pass"} else "fail"})


def want(drive: str, lane_id: str) -> str:
    """A lane's ask for a staging test of its pending stack: kept under wants/ until a stager's cut serves it, one per lane, so asking again returns the same cut id; the cut id the lane waits on as staging_result:<id>. The lane never takes the env itself: the stager holding it folds every waiting lane's stack into its cut, by his words "let me check if there's anything else pending I can pull in"."""
    path = lanes_dir(drive) / WANTS / f"{lane_id}.json"
    if path.is_file():
        return json.loads(path.read_text())["cut"]
    cut = f"{lane_id}-{store.now().strftime('%Y%m%d%H%M%S')}"
    written(path, {"lane": lane_id, "cut": cut, "at": store.stamp()})
    return cut


def wants(drive: str) -> dict[str, dict]:
    """Each lane's open ask for a staging test, by lane."""
    folder = lanes_dir(drive) / WANTS
    return {path.stem: json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))} if folder.is_dir() else {}


def served(drive: str, stacks: dict[str, dict]) -> None:
    """Write each served lane's own cut result, the file its staging_result:<cut id> wait reads, from the stacks of its lane in a cut, and close its ask."""
    held = wants(drive)
    for lane_id, asked in held.items():
        if mine := {bottom: result for bottom, result in stacks.items() if result.get("lane") == lane_id}:
            verdicts = {result["result"] for result in mine.values()}
            written(lanes_dir(drive) / CUTS / f"{asked['cut']}.json", {"stacks": mine, "result": "pass" if verdicts == {"pass"} else "fail", "lane": lane_id})
            (lanes_dir(drive) / WANTS / f"{lane_id}.json").unlink()


def start(drive: str) -> str | None:
    """Start the drive's one stager session when a lane waits on a staging test and the env is free for it: the queue grants staging-env to the stager on behalf of the best-ranked waiting lane, and the stager, holding it, folds every waiting lane's stack into its cut; None when none waits, the stager already holds the env, or the queue said wait."""
    if not live(drive) or not (asked := wants(drive)):
        return None
    order = {lane: index for index, lane in enumerate(json.loads((lanes_dir(drive) / RANK).read_text())["order"])} if (lanes_dir(drive) / RANK).is_file() else {}
    first = min(asked, key=lambda lane: (order.get(lane, len(order)), asked[lane]["at"]))
    if any(lease["holder"] == STAGER and store.parse(lease["until"]) > store.now() for lease in queue.kept(drive)["leases"].get(STAGING_ENV, [])):
        return None
    grant = queue.take(drive, STAGING_ENV, STAGER, first, STAGER_MINUTES)
    if not grant["granted"]:
        return None
    prompt = f"You are the drive's stager for drive {drive}. Read {PAGE} and follow it, running its verbs as {PAGE.parents[1] / 'bin' / 'lane-land'} --drive {drive}: you already hold staging-env as holder {STAGER}. Serve every lane waiting in {lanes_dir(drive) / WANTS}, starting with {first}."
    done = subprocess.run(["claude", "--bg", "--model", "opus", "--effort", "high", "--permission-mode", "bypassPermissions", "-n", f"stager {drive}", prompt], cwd=store.config(drive)["checkout"], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=120)
    if done.returncode:
        queue.release(drive, STAGING_ENV, STAGER)
        raise store.Refused(f"stager start: claude --bg exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()
