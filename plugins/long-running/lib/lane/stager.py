"""The staging tester's facts and writes: the dangerous stacks waiting on a proof, in rank order, each with what the tester needs to choose its batch, and each stack's result. No stager holds staging: cuts run side by side, each on its own staging target.

The tester is an agent (agents/stager.md). Code here gathers facts and records results; it never sizes a cut or packs stacks. Until cutover writes the lanes/<drive>/live marker, store.live, every verb runs in shadow: it reads the old drive's approved dangerous stacks, takes no lease, starts no cut, merges nothing, and keeps the tester's decision under shadow/.
"""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lane import landing, rows, store

SHADOW = "shadow"
STAGER_MINUTES = 60
WANTS = "staging-wants"
AFTER_MERGE = "after_merge: no PR of this lane needs a staging test before merge, so its stacks merge on his Approve and are tested after; wait on nothing for staging"
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
    rule = rows.rule_key(drive)
    asked = wants(drive)
    found = []
    for rank, lane_id in enumerate(order):
        lane = rows.seen(store.load(drive, lane_id), read, rule, store.config(drive)["premerge_lanes"])
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
                        "held": [landing.key(row) for row in open_rows if row["held"]],
                        "dangerous": {landing.key(row): row["dangerous"] for row in open_rows if landing.judged(row) and landing.dangerous(row)},
                    }
                )
    found += [{"rank": rank, "lane": lane_id, "stack": [landing.key(row)], "top_head": row["head"], "after_merge": True} for rank, lane_id in enumerate(order) for row in rows.seen(store.load(drive, lane_id), read, rule, store.config(drive)["premerge_lanes"])["prs"] if row.get("merged") and not row.get("staging") and row.get("approved_head")]
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
    """A lane's ask for a staging test of its pending stack: kept under wants/ until a stager's cut serves it, one per lane, so asking again returns the same cut id; the cut id the lane waits on as staging_result:<id>. The lane never runs the test itself: a stager folds every waiting lane's stack into its cut, by his words "let me check if there's anything else pending I can pull in"."""
    if not (lanes_dir(drive) / WANTS / f"{lane_id}.json").is_file() and not premerge(drive, lane_id):
        return AFTER_MERGE
    return filed(drive, lane_id)


def filed(drive: str, lane_id: str) -> str:
    """The cut id of the lane's open ask, filing one when it has none."""
    path = lanes_dir(drive) / WANTS / f"{lane_id}.json"
    if path.is_file():
        return json.loads(path.read_text())["cut"]
    cut = f"{lane_id}-{store.now().strftime('%Y%m%d%H%M%S')}"
    written(path, {"lane": lane_id, "cut": cut, "at": store.stamp()})
    return cut


def starts(drive: str, cut: str) -> str | None:
    """When a cut a lane waits on starts, as the lane's wait names it: the time a stager took its ask, `next` when its ask waits for a free target, or the result already written; None for a cut no ask or result names, which no stager will ever run."""
    if (lanes_dir(drive) / CUTS / f"{cut}.json").is_file():
        return "written"
    asked = next((ask for ask in wants(drive).values() if ask["cut"] == cut), None)
    if asked is None:
        return None
    return asked.get("claimed") or "next"


def premerge(drive: str, lane_id: str) -> list[str]:
    """The lane's open PRs the danger page in force holds for a staging test before merge, judged at their current heads, SandSQL work alone by his 5:00 PM words; a lane with none asks no cut, since its stacks merge on his Approve and are tested after."""
    seen = rows.seen(store.load(drive, lane_id), rows.view(drive), rows.rule_key(drive), store.config(drive)["premerge_lanes"])
    return [landing.key(row) for row in seen["prs"] if not row.get("merged") and landing.judged(row) and landing.dangerous(row)]


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


def unserved(drive: str) -> dict[str, dict]:
    """Each lane's ask no live stager has taken: never claimed, or claimed by a stager started more than STAGER_MINUTES ago, which has ended without serving it."""
    moment = store.now()
    return {lane: asked for lane, asked in wants(drive).items() if not asked.get("claimed") or moment - store.parse(asked["claimed"]) > timedelta(minutes=STAGER_MINUTES)}


def running(drive: str) -> int:
    """How many stagers run now: the distinct claims on open asks younger than STAGER_MINUTES, one per stager start."""
    moment = store.now()
    return len({asked["claimed"] for asked in wants(drive).values() if asked.get("claimed") and moment - store.parse(asked["claimed"]) <= timedelta(minutes=STAGER_MINUTES)})


def block(drive: str, lane_id: str, why: str) -> Path:
    """End a lane's ask the stager will not run, writing `blocked` with its why in full to the lane's own cut file, the file its staging_result wait reads, which wakes it at once; a stager never sends cci to a lane, since a lane is a file no cci session reads."""
    refuse_in_shadow(drive, "blocking a lane's cut")
    asked = wants(drive)[lane_id]
    path = written(lanes_dir(drive) / CUTS / f"{asked['cut']}.json", {"lane": lane_id, "cut": asked["cut"], "result": "blocked", "stacks": {}, "why": why})
    (lanes_dir(drive) / WANTS / f"{lane_id}.json").unlink()
    return path


def reconcile(drive: str, owed: set[str]) -> None:
    """Keep the asks in line with the lanes that owe a staging test before merge, read each landing pass: a lane that owes one and has no ask gets one, as want() files it, so a stack waits on no ask by hand after a block, a new head or his Approve; an ask of a lane that owes none is closed with an after_merge result, which wakes the lane to land on his Approve, so a ruling that narrows the bar re-judges every live staging wait at once."""
    if not live(drive):
        return
    for lane_id in sorted(owed - blocked(drive)):
        filed(drive, lane_id)
    for lane_id, asked in wants(drive).items():
        if lane_id not in owed:
            written(lanes_dir(drive) / CUTS / f"{asked['cut']}.json", {"lane": lane_id, "cut": asked["cut"], "result": "after_merge", "stacks": {}, "why": AFTER_MERGE})
            (lanes_dir(drive) / WANTS / f"{lane_id}.json").unlink()


def blocked(drive: str) -> set[str]:
    """The lanes a stager blocked within STAGER_MINUTES, whose ask reconcile() files again only after that, so a stack no cut can run starts no stager each pass."""
    since = store.now() - timedelta(minutes=STAGER_MINUTES)
    folder = lanes_dir(drive) / CUTS
    return {read["lane"] for path in folder.glob("*.json") if datetime.fromtimestamp(path.stat().st_mtime, UTC) > since and (read := json.loads(path.read_text())).get("result") == "blocked"} if folder.is_dir() else set()


def defer(drive: str, lane_id: str, why: str) -> Path:
    """Hand a lane's ask back to the line, unclaimed, with why it waits, for a stack this cut cannot carry but a later one can, such as a stack that shares a service with this cut's: the next stager start takes it once a target frees, so the lane never asks again by hand, by his 18:33 words "no hand steps"."""
    refuse_in_shadow(drive, "deferring a lane's cut")
    asked = {field: value for field, value in wants(drive)[lane_id].items() if field != "claimed"}
    return written(lanes_dir(drive) / WANTS / f"{lane_id}.json", asked | {"deferred": why, "deferred_at": store.stamp()})


def start(drive: str) -> str | None:
    """Start a stager session for every lane ask no live stager has taken, with no hold on staging: cuts run in parallel, each on a staging target of the drive config's staging_targets that no other running cut uses, by his 17:00 ruling "we dont need a staging hold to test on the 3rd and 4th clusters on staging"; None when every ask is taken, or a stager already runs on every target, so one target serves one cut at a time and the next stager starts when it frees."""
    if not live(drive) or not (asked := unserved(drive)) or running(drive) >= len(store.config(drive)["staging_targets"]):
        return None
    order = {lane: index for index, lane in enumerate(json.loads((lanes_dir(drive) / RANK).read_text())["order"])} if (lanes_dir(drive) / RANK).is_file() else {}
    first = min(asked, key=lambda lane: (order.get(lane, len(order)), asked[lane]["at"]))
    claimed = store.stamp()
    for lane, ask in asked.items():
        written(lanes_dir(drive) / WANTS / f"{lane}.json", ask | {"claimed": claimed})
    targets = ", ".join(store.config(drive)["staging_targets"])
    alerts = store.config(drive)["staging_alerts"]
    prompt = f"You are a stager for drive {drive}. Read {PAGE} and follow it, running its verbs as {PAGE.parents[1] / 'bin' / 'lane-land'} --drive {drive}. Serve the lanes {', '.join(asked)} waiting in {lanes_dir(drive) / WANTS}, starting with {first}. Test on one of the staging targets {targets} that no other running cut in {lanes_dir(drive) / CUTS} uses; you take no hold. Staging alerts post in {alerts}: read them through your cut into each stack's result."
    from lane import session

    argv = session.argv(store.config(drive), "stager", Path(store.config(drive)["checkout"]), {"permissions": {"deny": ["Bash(cci post:*)", "Bash(cci post *)", "mcp__plugin_cc-inbox_cci__post"]}}, ["--bg"], ["--permission-mode", "bypassPermissions", "-n", f"stager {drive} {first}", prompt])
    done = subprocess.run(argv, cwd=store.config(drive)["checkout"], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=120)
    if done.returncode:
        for lane, ask in asked.items():
            written(lanes_dir(drive) / WANTS / f"{lane}.json", ask)
        raise store.Refused(f"stager start: claude --bg exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()


def missing(drive: str, target: str) -> str | None:
    """Why a staging target, `<env>` or `<env> <SandSQL generation>`, does not exist on the repository's default branch: its env has no infra/envs/<env>.yaml, or that file names no such generation; None when it exists."""
    from drive import providers

    env, _, generation = target.partition(" ")
    checkout = Path(store.config(drive)["checkout"])
    if not (spec := providers.blob(checkout, providers.at(checkout).sha, f"infra/envs/{env}.yaml")):
        return f"{env} has no infra/envs/{env}.yaml on the default branch"
    if generation and generation not in spec:
        return f"infra/envs/{env}.yaml on the default branch names no {generation}"
    return None


def targets(drive: str, named: list[str]) -> list[str]:
    """Write the drive's staging_targets, refusing the whole set when any target does not exist, as sandsql-nodes-4, read from his words about "the 3rd and 4th clusters", exists nowhere."""
    if refused := [f"{target}: {why}" for target in named if (why := missing(drive, target))]:
        raise store.Refused(f"no such staging target: {'; '.join(refused)}")
    path = store.drive_dir(drive) / "config.json"
    kept = json.loads(path.read_text()) if path.is_file() else {}
    written(path, kept | {"staging_targets": named})
    return named
