"""lane-land's reads and writes of lane PRs: their state from GitHub, kept in memory and never on a row, and on each row the dangerous reader's verdict at a head and its stack's staging proof."""

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

import httpx
from spawnllm import JEV, Binary, BinaryAnswer, DecideError, DecideKeyMissing, decide_sync

from lane import facts, landing, store

CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail"}
PR_STATE = ("head", "checks", "merged", "under", "commits", "whole", "foreign", "title")
DANGER = Path(__file__).with_name("danger.md")
BEFORE_MERGE = "plainly needs a staging pass before merge, by his 17:00 ruling"
DANGER_AT = 0.6
JUDGED_AT = 20
JUDGES = 6
FILES_SHOWN = 150
JUDGE_SECONDS = 60.0


def view(drive: str) -> dict[str, dict]:
    """Each lane PR's state, read in lane-core's one GraphQL request and kept in memory, never on its row: head, checks, merged and under, the tracked open PR whose branch is its base, None when its base is the default branch, and left out when its base is a branch no tracked PR heads, which landing reads as unread."""
    keys = {(row["repo"], row["number"]) for lane in store.load_all(drive).values() for row in lane["prs"]}
    read = facts.read_prs(keys)
    heads = {(repo, pull["branch"]): f"{repo}#{number}" for (repo, number), pull in read.items() if pull["state"] == "OPEN"}
    found = {}
    for (repo, number), pull in read.items():
        facts_of = {"head": pull["head"] if pull["state"] == "OPEN" or pull["merged"] else None, "checks": CHECKS.get(pull["rollup"] or "", "pending"), "merged": pull["merged"], "title": pull["title"], "commits": pull["commits"], "whole": pull["commit_count"] <= len(pull["commits"])}
        if pull["base"] == pull["default"]:
            facts_of["under"] = None
        elif below := heads.get((repo, pull["base"])):
            facts_of["under"] = below
        found[f"{repo}#{number}"] = facts_of
    return found


def seen(lane: dict, read: dict[str, dict], rule: str) -> dict:
    """The lane with each prs row joined to its PR's state from view(), in memory, a PR GitHub did not answer keeping no head, so it never lands, a verdict judged under a bar other than rule dropped, so a head stays unjudged and holds until judge() reads it against the bar in force, and with `foreign`, the commits since his approved head that our lanes' account did not author, by foreign()."""
    joined = []
    for row in lane["prs"]:
        kept = {field: value for field, value in row.items() if field not in PR_STATE} | read.get(landing.key(row), {"head": None})
        kept |= {gate: kept[gate] if kept.get(gate) and kept[gate].get("rule") == rule else None for gate in ("dangerous", "guarded")}
        joined.append(kept | {"foreign": foreign(kept.get("commits") or [], kept["approved_head"], ours(), kept.get("whole", False))})
    return lane | {"prs": joined}


def foreign(commits: list[tuple[str, str | None]], approved: str | None, account: str, whole: bool) -> list[str] | None:
    """Each commit after his approved head on the PR authored by any account but ours, as `<sha> by <login>`, so a teammate's push needs his tap again while our lanes' own pushes keep his Approve, by his 14:13 words and team-lead's rule; when a rebase dropped his approved head from a PR whose every commit was read, each of its commits another account authored, since a rebase keeps authors; None when his approved head is not among the last COMMITS_READ commits of a longer PR, which landing reads as needing his tap; empty for none."""
    oids = [oid for oid, _ in commits]
    if approved and approved not in oids and whole:
        return [f"{oid[:12]} by {login or 'an unknown author'}" for oid, login in commits if login != account]
    if not approved or approved not in oids:
        return None
    return [f"{oid[:12]} by {login or 'an unknown author'}" for oid, login in commits[oids.index(approved) + 1 :] if login != account]


@cache
def ours() -> str:
    """The GitHub account our lanes push as, the one gh is signed in to."""
    return subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, check=True, timeout=60).stdout.strip()


def unjudged(lanes: dict[str, dict], key: str) -> list[str]:
    """Each open lane PR with a head and no verdict at it under the rule whose key is given, so a change to danger.md judges every open head again."""
    return list(dict.fromkeys(landing.key(row) for lane in lanes.values() for row in lane["prs"] if row["head"] and not row.get("merged") and not (landing.judged(row) and row["dangerous"].get("rule") == key and row["guarded"].get("rule") == key)))


def judge(drive: str, read: dict[str, dict]) -> int:
    """Write each unjudged head's verdict, {head, classes}: a model reads danger.md, the bar rule() reads, his list of what plainly needs a staging pass before merge, against each PR's title and changed files, JUDGED_AT a request, and a yes at DANGER_AT or more names the class `before merge` with its why; a no leaves classes empty, so his Approve merges it and the tester runs its test after the merge. A head whose files or answer could not be read stays unjudged and holds. The count written."""
    key = rule_key(drive)
    if not (waiting := unjudged({lane_id: seen(lane, read, key) for lane_id, lane in store.load_all(drive).items()}, key)):
        return 0
    files = readable(waiting)
    verdicts: dict[str, dict] = {}
    chunks = [dict(list(files.items())[start : start + JUDGED_AT]) for start in range(0, len(files), JUDGED_AT)]
    with ThreadPoolExecutor(max_workers=JUDGES) as pool:
        for answered in pool.map(lambda chunk: asked(drive, chunk, read), chunks):
            verdicts |= {pr: verdict | {"rule": key} for pr, verdict in answered.items()}
    marks = presence(drive, files, key)

    def written(lanes: dict[str, dict]) -> int:
        count = 0
        for lane in lanes.values():
            for row in lane["prs"]:
                if (mark := marks.get(landing.key(row))) and mark["head"] == read[landing.key(row)]["head"] and (row.get("guarded") or {}).get("rule") != key:
                    row["guarded"] = mark
                    count += 1
                if (verdict := verdicts.get(landing.key(row))) and verdict["head"] == read[landing.key(row)]["head"] and (row.get("dangerous") or {}).get("rule") != key:
                    row["dangerous"] = verdict
                    count += 1
        return count

    return store.transact(drive, written)


def rule(drive: str) -> str:
    """The bar a PR is judged against: the page the repository's .claude/long-running.toml names under [lanes] danger, read at its default branch's head in the drive's checkout so no branch loosens it, else lib/lane/danger.md for a repository naming none."""
    from drive import providers

    if checkout := store.config(drive)["checkout"]:
        found = providers.at(Path(checkout))
        if (page := found.config.get("lanes", {}).get("danger")) and (text := providers.blob(Path(checkout), found.sha, page)):
            return text
    return DANGER.read_text()


def asked(drive: str, chunk: dict[str, tuple[str, list[str]]], read: dict[str, dict]) -> dict[str, dict]:
    """One model request judging each PR of chunk against the rule; none when the request fails, so those heads stay unjudged."""
    told = [{"id": f"p{index}", "pr": key, "title": read[key].get("title") or "", "files": paths[:FILES_SHOWN], "more_files": max(0, len(paths) - FILES_SHOWN)} for index, (key, (_, paths)) in enumerate(chunk.items())]
    questions = {item["id"]: Binary(f"By the rule, does {item['pr']} plainly do one of the listed kinds?", yes="its title or files plainly show one of them", no="it does none, or it is unclear, so it merges on his Approve and is tested after the merge") for item in told}
    try:
        decision = decide_sync({"rule": rule(drive), "prs": told}, questions, provider=JEV, timeout=JUDGE_SECONDS)
    except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError):
        return {}
    found = {}
    for item in told:
        if isinstance(answer := decision.answers.get(item["id"]), BinaryAnswer):
            head = chunk[item["pr"]][0]
            found[item["pr"]] = {"head": head, "classes": {BEFORE_MERGE: [f"p(yes) {answer.p_yes:.2f} by danger.md"]} if answer.p_yes >= DANGER_AT else {}, "p_yes": round(answer.p_yes, 3)}
    return found


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


def rule_key(drive: str) -> str:
    return hashlib.sha256(rule(drive).encode()).hexdigest()[:16]


def presence(drive: str, files: dict[str, tuple[str, list[str]]], key: str) -> dict[str, dict]:
    """Each head's presence mark, {head, classes, rule}: the repository's broad [[dangerous]] tables and its provider's dangerous_paths, by state.dangerous_in as the providers of the drive's checkout read them, which mark the head and never ask a staging pass; none with no checkout configured, so those heads stay unjudged and hold."""
    from drive import providers, state

    if not (checkout := store.config(drive)["checkout"]):
        return {}
    found = providers.at(Path(checkout))
    return {pr: {"head": head, "classes": state.dangerous_in(found.config, found.repo, paths), "rule": key} for pr, (head, paths) in files.items()}
