"""The staging tester's facts and writes: the dangerous stacks waiting on a proof, in rank order, each with what the tester needs to choose its batch, and each stack's result. No stager holds staging: cuts run side by side, each on its own staging target.

The tester is an agent (agents/stager.md). Code here gathers facts and records results; it never sizes a cut or packs stacks. Until cutover writes the lanes/<drive>/live marker, store.live, every verb runs in shadow: it reads the old drive's approved dangerous stacks, takes no lease, starts no cut, merges nothing, and keeps the tester's decision under shadow/.
"""

import json
import os
from pathlib import Path

from lane import landing, rows, store

SHADOW = "shadow"
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
    """Write a cut's result after putting each stack's proof on its top PR's row by rows.prove: stacks keyed by bottom PR, each {lane, stack: [PRs bottom first], head: its top head, target, result: pass or fail}. Each stack's staging item is finished with its result by finished(), which wakes its lane, and each passed stack is landed in this same call by lane-land's land_stack, so a stack his Approve already covers is in the merge queue the moment its proof is written; what each landing did is kept under `landed`."""
    from lane import cli as lane_land

    refuse_in_shadow(drive, "recording a cut's result")
    path = lanes_dir(drive) / CUTS / f"{cut}.json"
    kept = json.loads(path.read_text()) if path.exists() else {}
    rows.prove(drive, cut, stacks)
    verdicts = {stack["result"] for stack in stacks.values()}
    landed = {bottom: lane_land.land_stack(drive, stack["lane"], bottom) for bottom, stack in stacks.items() if stack["result"] == "pass"}
    written(path, kept | {"stacks": stacks, "result": "pass" if verdicts == {"pass"} else "fail", "landed": landed})
    finished(drive, stacks)
    return path


def want(drive: str, lane_id: str) -> str:
    """A lane's staging test of its pending stack, as the work item it waits on, `work:<id>`: the staging-before item queue() keeps for its stack, which a staging worker takes with every other staging item for the same targets, by his words "let me check if tehres naytthing else pending I can pull in"; AFTER_MERGE when no PR of the lane needs a test before merge. The lane never runs the test itself."""
    lane = rows.seen(store.load(drive, lane_id), rows.view(drive, [lane_id]), rows.rule_key(drive), store.config(drive)["premerge_lanes"])
    from lane import work

    before = [found for found in queue(drive, [lane]) if work.load(drive, found)["kind"] == "staging-before"]
    return f"work:{before[0]}" if before else AFTER_MERGE


def starts(drive: str, cut: str) -> str | None:
    """When a cut a lane waits on starts, for a staging_result wait from before the work queue: `written` once its result is, the time its ask was claimed, `next` for an ask not yet claimed; None for a cut no ask or result names, which no worker will ever run."""
    if (lanes_dir(drive) / CUTS / f"{cut}.json").is_file():
        return "written"
    asked = next((ask for ask in wants(drive).values() if ask["cut"] == cut), None)
    if asked is None:
        return None
    return asked.get("claimed") or "next"




def wants(drive: str) -> dict[str, dict]:
    """Each lane's open ask for a staging test, by lane."""
    folder = lanes_dir(drive) / WANTS
    return {path.stem: json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))} if folder.is_dir() else {}


def queue(drive: str, lanes: list[dict]) -> list[str]:
    """Put every staging test the lanes owe on the work queue, read each landing pass from the lanes as seen: a staging-before item for each stack holding a PR that waits on a staging proof, keyed by its bottom PR at its top head, and a staging-after item for each PR his Approve merged with no proof yet, so every merged untested change joins one batch, by his words "tested on staging AFTER merging, and as a big batch". Each touches every staging target, so the worker that pulls one takes the rest beside it. Any ask still under wants/ is closed with a result naming its item, which wakes its lane to wait on work:<id> instead. The ids added or kept."""
    from lane import work

    if not live(drive):
        return []
    targets = store.config(drive)["staging_targets"]
    since = store.config(drive)["after_merge_since"]
    added = []
    for lane in lanes:
        for stack in landing.stacks(lane):
            open_rows = [row for row in stack if not row.get("merged")]
            if open_rows and not landing.proven(open_rows) and any(landing.judged(row) and landing.dangerous(row) for row in open_rows):
                keys = [landing.key(row) for row in open_rows]
                added.append(work.add(drive, "staging-before", lane["id"], keys[0], before_words(drive, keys, open_rows[-1]["head"]), head=open_rows[-1]["head"], touches=targets, by="lane-land")["id"])
        for row in lane["prs"]:
            if row.get("merged") and not row.get("staging") and row.get("approved_head") and (not since or row["merged_at"] > since["at"]):
                added.append(work.add(drive, "staging-after", lane["id"], landing.key(row), after_words(drive, landing.key(row), since), head=row["head"], touches=targets, by="lane-land")["id"])
    for lane_id, asked in wants(drive).items():
        moved = work.item_id("staging-before", next((landing.key(stack[0]) for lane in lanes if lane["id"] == lane_id for stack in landing.stacks(lane) if any(not row.get("merged") for row in stack)), lane_id))
        written(lanes_dir(drive) / CUTS / f"{asked['cut']}.json", {"lane": lane_id, "cut": asked["cut"], "result": "moved", "stacks": {}, "why": f"staging asks are work items now: wait on work:{moved}"})
        (lanes_dir(drive) / WANTS / f"{lane_id}.json").unlink()
    return added


def before_words(drive: str, keys: list[str], head: str) -> str:
    return f"Test {', '.join(keys)} on staging before merge, at its top head {head[:12]}, by {PAGE}: read that page first and follow its steps, running its verbs as {PAGE.parents[1] / 'bin' / 'lane-land'} --drive {drive}. Take every other staging item pull shows beside this one into the same cut. Read the staging alerts channel {store.config(drive)["staging_alerts"]} through the cut into each stack's result. `lane-land result` finishes each item it writes a stack for."


def after_words(drive: str, key: str, since: dict | None) -> str:
    cutoff = f" It merged after {since['at']}, the last green staging promotion, {since['build']}, so that promotion did not test it." if since else ""
    return f"Test {key}, merged on his Approve, on staging after merge.{cutoff} Test it in one batch with every other staging-after item pull shows, as one cut of the default branch's head, by {PAGE}: read that page first and follow its steps, running its verbs as {PAGE.parents[1] / 'bin' / 'lane-land'} --drive {drive}. Read the staging alerts channel {store.config(drive)["staging_alerts"]} through the cut into each stack's result. `lane-land result` finishes each item it writes a stack for."


def finished(drive: str, stacks: dict[str, dict]) -> None:
    """Finish the staging item of each stack a cut's result names, the bottom PR's staging-before item or each PR's staging-after item, with its result, which wakes its lane; an item a worker already finished is left as it is."""
    from lane import work

    for bottom, stack in stacks.items():
        for kind, subject in [("staging-before", bottom), *[("staging-after", key) for key in stack["stack"]]]:
            item = work.load(drive, work.item_id(kind, subject))
            if item and item["state"] in (work.QUEUED, work.TAKEN):
                line = f"staging {stack['result']} for {', '.join(stack['stack'])} at {stack['head'][:12]} on {stack['target']}"
                work.done(drive, item["id"], line, failed=stack["result"] != "pass")


def missing(drive: str, target: str) -> str | None:
    """Why a staging target, `<env>` or `<env> <generation>`, does not exist on the repository's default branch: the file the drive config's staging_env_file names for its env, a path with `{env}` in it, is missing there or names no such generation; None when it exists."""
    from drive import providers

    env, _, generation = target.partition(" ")
    checkout = Path(store.config(drive)["checkout"])
    named = store.config(drive)["staging_env_file"].format(env=env)
    if not (spec := providers.blob(checkout, providers.at(checkout).sha, named)):
        return f"{env} has no {named} on the default branch"
    if generation and generation not in spec:
        return f"{named} on the default branch names no {generation}"
    return None


def targets(drive: str, named: list[str]) -> list[str]:
    """Write the drive's staging_targets, refusing the whole set when any target does not exist on the default branch, as a generation his words named but the env file does not."""
    if refused := [f"{target}: {why}" for target in named if (why := missing(drive, target))]:
        raise store.Refused(f"no such staging target: {'; '.join(refused)}")
    path = store.drive_dir(drive) / "config.json"
    kept = json.loads(path.read_text()) if path.is_file() else {}
    written(path, kept | {"staging_targets": named})
    return named
