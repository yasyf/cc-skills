"""lane-land's reads and writes of lane PRs: their state from GitHub, kept in memory and never on a row, and on each row the dangerous reader's verdict at a head and its stack's staging proof."""

import subprocess
from functools import cache
from pathlib import Path

from lane import facts, landing, store

CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail"}
PR_STATE = ("head", "checks", "merged", "under", "commits", "foreign")


def view(drive: str) -> dict[str, dict]:
    """Each lane PR's state, read in lane-core's one GraphQL request and kept in memory, never on its row: head, checks, merged and under, the tracked open PR whose branch is its base, None when its base is the default branch, and left out when its base is a branch no tracked PR heads, which landing reads as unread."""
    keys = {(row["repo"], row["number"]) for lane in store.load_all(drive).values() for row in lane["prs"]}
    read = facts.read_prs(keys)
    heads = {(repo, pull["branch"]): f"{repo}#{number}" for (repo, number), pull in read.items() if pull["state"] == "OPEN"}
    found = {}
    for (repo, number), pull in read.items():
        facts_of = {"head": pull["head"] if pull["state"] == "OPEN" or pull["merged"] else None, "checks": CHECKS.get(pull["rollup"] or "", "pending"), "merged": pull["merged"], "commits": pull["commits"]}
        if pull["base"] == pull["default"]:
            facts_of["under"] = None
        elif below := heads.get((repo, pull["base"])):
            facts_of["under"] = below
        found[f"{repo}#{number}"] = facts_of
    return found


def seen(lane: dict, read: dict[str, dict]) -> dict:
    """The lane with each prs row joined to its PR's state from view(), in memory, a PR GitHub did not answer keeping no head, so it never lands, and with `foreign`, the commits since his approved head that our lanes' account did not author, by foreign()."""
    joined = []
    for row in lane["prs"]:
        kept = {field: value for field, value in row.items() if field not in PR_STATE} | read.get(landing.key(row), {"head": None})
        joined.append(kept | {"foreign": foreign(kept.get("commits") or [], kept["approved_head"], ours())})
    return lane | {"prs": joined}


def foreign(commits: list[tuple[str, str | None]], approved: str | None, account: str) -> list[str] | None:
    """Each commit after his approved head on the PR authored by any account but ours, as `<sha> by <login>`, so a teammate's push needs his tap again while our lanes' own pushes keep his Approve, by his 14:13 words and team-lead's rule; None when his approved head is not among the PR's last COMMITS_READ commits, which landing reads as needing his tap; empty for none."""
    oids = [oid for oid, _ in commits]
    if not approved or approved not in oids:
        return None
    return [f"{oid[:12]} by {login or 'an unknown author'}" for oid, login in commits[oids.index(approved) + 1 :] if login != account]


@cache
def ours() -> str:
    """The GitHub account our lanes push as, the one gh is signed in to."""
    return subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, check=True, timeout=60).stdout.strip()


def unjudged(lanes: dict[str, dict]) -> list[str]:
    """Each open lane PR with a head and no verdict at it."""
    return list(dict.fromkeys(landing.key(row) for lane in lanes.values() for row in lane["prs"] if row["head"] and not row.get("merged") and not landing.judged(row)))


def judge(drive: str, read: dict[str, dict]) -> int:
    """Read each unjudged head, as view() read it,'s changed files in one batched GitHub read and write its verdict, {head, classes}, by state.dangerous_in as the providers of the drive's checkout, its config's `checkout`, read the repository's [[dangerous]] tables and dangerous_paths; with no checkout configured nothing is judged, so nothing lands. The count written."""
    from drive import providers, state

    checkout = store.config(drive)["checkout"]
    if not checkout or not (waiting := unjudged({lane_id: seen(lane, read) for lane_id, lane in store.load_all(drive).items()})):
        return 0
    found = providers.at(Path(checkout))
    verdicts = {key: {"head": head, "classes": state.dangerous_in(found.config, found.repo, paths)} for key, (head, paths) in readable(waiting).items()}

    def written(lanes: dict[str, dict]) -> int:
        count = 0
        for lane in lanes.values():
            for row in lane["prs"]:
                if (verdict := verdicts.get(landing.key(row))) and verdict["head"] == read[landing.key(row)]["head"] and (row.get("dangerous") or {}).get("head") != verdict["head"]:
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


def readable(keys: list[str]) -> dict[str, tuple[str, list[str]]]:
    """Each PR's head and changed files by github.paths, a batch GitHub refuses read again one PR at a time, so one PR whose files cannot be read, such as #49164's incomplete snapshot, stays unjudged and holds while the rest are judged."""
    from drive import github

    found: dict[str, tuple[str, list[str]]] = {}
    for start in range(0, len(keys), github.PRS_PER_QUERY):
        chunk = keys[start : start + github.PRS_PER_QUERY]
        try:
            found |= github.paths(chunk)
        except github.GithubError:
            for key in chunk:
                try:
                    found |= github.paths([key])
                except github.GithubError:
                    continue
    return found
