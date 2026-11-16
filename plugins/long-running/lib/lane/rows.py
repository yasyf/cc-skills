"""lane-land's writes onto lane prs rows: each open PR's head, checks, merge and the PR it sits on, the dangerous reader's verdict at each head, and each stack's staging proof."""

from pathlib import Path

from lane import facts, landing, store

CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail"}


def refresh(drive: str) -> int:
    """Read every unmerged lane PR in one GraphQL request and write its head, checks, merged and under: the tracked open PR whose branch is its base, None when its base is the default branch, and the key left out when its base is a branch no tracked PR heads, which landing reads as unread; the count of rows changed."""
    keys = {(row["repo"], row["number"]) for lane in store.load_all(drive).values() for row in lane["prs"] if not row.get("merged")}
    read = facts.read_prs(keys)
    heads = {(repo, pull["branch"]): f"{repo}#{number}" for (repo, number), pull in read.items() if pull["state"] == "OPEN"}

    def written(lanes: dict[str, dict]) -> int:
        count = 0
        for lane in lanes.values():
            for row in lane["prs"]:
                if not (pull := read.get((row["repo"], row["number"]))):
                    continue
                was = dict(row)
                row["head"] = pull["head"]
                row["checks"] = CHECKS.get(pull["rollup"] or "", "pending")
                if pull["merged"]:
                    row["merged"] = row.get("merged") or store.stamp()
                if pull["base"] == pull["default"]:
                    row["under"] = None
                elif below := heads.get((row["repo"], pull["base"])):
                    row["under"] = below
                else:
                    row.pop("under", None)
                count += row != was
        return count

    return store.transact(drive, written)


def unjudged(lanes: dict[str, dict]) -> list[str]:
    """Each open lane PR with a head and no verdict at it."""
    return list(dict.fromkeys(landing.key(row) for lane in lanes.values() for row in lane["prs"] if row["head"] and not row.get("merged") and not landing.judged(row)))


def judge(drive: str) -> int:
    """Read each unjudged head's changed files in one batched GitHub read and write its verdict, {head, classes}, by state.dangerous_in as the providers of the drive's checkout, its config's `checkout`, read the repository's [[dangerous]] tables and dangerous_paths; with no checkout configured nothing is judged, so nothing lands. The count written."""
    from drive import github, providers, state

    checkout = store.config(drive)["checkout"]
    if not checkout or not (waiting := unjudged(store.load_all(drive))):
        return 0
    found = providers.at(Path(checkout))
    verdicts = {key: {"head": head, "classes": state.dangerous_in(found.config, found.repo, paths)} for key, (head, paths) in github.paths(waiting).items()}

    def written(lanes: dict[str, dict]) -> int:
        count = 0
        for lane in lanes.values():
            for row in lane["prs"]:
                if (verdict := verdicts.get(landing.key(row))) and verdict["head"] == row["head"] and not landing.judged(row):
                    row["dangerous"] = verdict
                    count += 1
        return count

    return store.transact(drive, written)


def prove(drive: str, cut: str, stacks: dict[str, dict]) -> None:
    """Put each stack's cut result on the lane row of its top PR as its staging proof, {cut, head, result, target}, which landing.proven reads at that head only."""

    def proven(lanes: dict[str, dict]) -> None:
        for lane in lanes.values():
            for row in lane["prs"]:
                for result in stacks.values():
                    if result["stack"][-1] == landing.key(row):
                        row["staging"] = {"cut": cut, "head": result["head"], "result": result["result"], "target": result["target"]}

    store.transact(drive, proven)
