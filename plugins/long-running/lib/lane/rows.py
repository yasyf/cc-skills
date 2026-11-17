"""lane-land's reads and writes of lane PRs: their state from GitHub, kept in memory and never on a row, and on each row the dangerous reader's verdict at a head and its stack's staging proof."""

import hashlib
from fnmatch import fnmatchcase
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from spawnllm import JEV, Binary, BinaryAnswer, DecideError, DecideKeyMissing, decide_sync

from lane import facts, landing, store

CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail"}
PR_STATE = ("head", "checks", "merged", "merged_at", "merge_commit", "branch", "base", "default", "under", "commits", "whole", "premerge", "title", "mergeable")
DANGER = Path(__file__).with_name("danger.md")
BEFORE_MERGE = "plainly needs a staging pass before merge, by his 17:00 ruling"
DANGER_AT = 0.6
MARKS = "marks: classes, tooling"
JUDGED_AT = 20
JUDGES = 6
FILES_SHOWN = 150
JUDGE_SECONDS = 60.0
CLEARLY_NOT_PREMERGE = 0.1


def view(drive: str, lane_ids: list[str] | None = None) -> dict[str, dict]:
    """Each lane PR's state, read in lane-core's one GraphQL request and kept in memory, never on its row: head, checks, merged and under, the tracked open PR whose branch is its base, None when its base is the default branch, and left out when its base is a branch no tracked PR heads, which landing reads as unread. With lane_ids, only those lanes' PRs, for an event handler that lands one stack."""
    keys = {(row["repo"], row["number"]) for lane_id, lane in store.load_all(drive).items() if lane_ids is None or lane_id in lane_ids for row in lane["prs"]}
    read = facts.read_prs(keys)
    heads = {(repo, pull["branch"]): f"{repo}#{number}" for (repo, number), pull in read.items() if pull["state"] == "OPEN"}
    found = {}
    for (repo, number), pull in read.items():
        facts_of = {"head": pull["head"] if pull["state"] == "OPEN" or pull["merged"] else None, "checks": CHECKS.get(pull["rollup"] or "", "pending"), "merged": pull["merged"], "merged_at": pull["merged_at"], "merge_commit": pull["merge_commit"], "branch": pull["branch"], "base": pull["base"], "default": pull["default"], "title": pull["title"], "mergeable": pull["mergeable"], "commits": pull["commits"], "whole": pull["commit_count"] <= len(pull["commits"])}
        if pull["base"] == pull["default"]:
            facts_of["under"] = None
        elif below := heads.get((repo, pull["base"])):
            facts_of["under"] = below
        found[f"{repo}#{number}"] = facts_of
    return found


def seen(lane: dict, read: dict[str, dict], rule: str, premerge_lanes: list[str]) -> dict:
    """The lane with each prs row joined to its PR's state from view(), in memory, a PR GitHub did not answer keeping no head, so it never lands, a verdict judged under a bar other than rule dropped, so a head stays unjudged and holds until judge() reads it against the bar in force, with `premerge` when the drive config's premerge_lanes names the lane, so its every PR waits on a staging proof."""
    joined = []
    for row in lane["prs"]:
        kept = {field: value for field, value in row.items() if field not in PR_STATE} | read.get(landing.key(row), {"head": None})
        kept |= {gate: kept[gate] if kept.get(gate) and kept[gate].get("rule") == rule else None for gate in ("dangerous", "guarded")}
        joined.append(kept | {"premerge": lane["id"] in premerge_lanes and not cleared_of_premerge(kept, rule)})
    return lane | {"prs": joined}


def unjudged(lanes: dict[str, dict], key: str) -> list[str]:
    """Each open lane PR with a head and no verdict at it under the rule whose key is given, so a change to danger.md judges every open head again."""
    return list(dict.fromkeys(landing.key(row) for lane in lanes.values() for row in lane["prs"] if row["head"] and not row.get("merged") and not (landing.judged(row) and row["dangerous"].get("rule") == key and row["guarded"].get("rule") == key)))


def judge(drive: str, read: dict[str, dict]) -> int:
    """Write each unjudged head's verdict, {head, classes}: a model reads danger.md, the bar rule() reads, his list of what plainly needs a staging pass before merge, against each PR's title and changed files, JUDGED_AT a request, and a yes at DANGER_AT or more names the class `before merge` with its why; a no leaves classes empty, so his Approve merges it and the tester runs its test after the merge. A head whose files or answer could not be read stays unjudged and holds. The count written."""
    key = rule_key(drive)
    if cleared(store.load_all(drive), key):
        store.transact(drive, lambda lanes: cleared(lanes, key))
    premerge = {"premerge_work": premerge_work(drive, read, found)} if (found := premerge_unjudged(drive, read, key)) else {}
    if not (waiting := unjudged({lane_id: seen(lane, read, key, store.config(drive)["premerge_lanes"]) for lane_id, lane in store.load_all(drive).items()}, key)):
        return store.transact(drive, lambda lanes: written(lanes, read, premerge, key)) if premerge else 0
    files = readable(waiting)
    verdicts: dict[str, dict] = {}
    chunks = [dict(list(files.items())[start : start + JUDGED_AT]) for start in range(0, len(files), JUDGED_AT)]
    with ThreadPoolExecutor(max_workers=JUDGES) as pool:
        for answered in pool.map(lambda chunk: asked(drive, chunk, read), chunks):
            verdicts |= {pr: verdict | {"rule": key} for pr, verdict in answered.items()}
    marks = presence(drive, files, key)
    return store.transact(drive, lambda lanes: written(lanes, read, {"guarded": marks, "dangerous": verdicts} | premerge, key))


def cleared(lanes: dict[str, dict], key: str) -> int:
    """Drop from every row, merged and headless ones included, each verdict judged under a bar other than the rule whose key is given, so no row carries a class from a ruling he replaced, by his 18:22 words that a ruling re-judges every live wait. The count dropped."""
    count = 0
    for lane in lanes.values():
        for row in lane["prs"]:
            for gate in ("dangerous", "guarded"):
                if row.get(gate) and row[gate].get("rule") != key:
                    row[gate] = None
                    count += 1
    return count


def written(lanes: dict[str, dict], read: dict[str, dict], judged: dict[str, dict[str, dict]], key: str) -> int:
    """Write each gate's verdict at a PR's current head on its row, unless the row already holds one at that head under this rule, so a head that moved since its last verdict is judged again; a row added after the read, such as a PR opened while the judge ran, waits for the next judge. The count written."""
    count = 0
    for lane in lanes.values():
        for row in lane["prs"]:
            if landing.key(row) not in read:
                continue
            head = read[landing.key(row)]["head"]
            for gate, verdicts in judged.items():
                kept = row.get(gate) or {}
                if (verdict := verdicts.get(landing.key(row))) and verdict["head"] == head and (kept.get("head"), kept.get("rule")) != (head, key):
                    row[gate] = verdict
                    count += 1
    return count


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


def cleared_of_premerge(row: dict, rule: str) -> bool:
    """Whether a premerge lane's PR is clearly not the work its pre-merge test is for, at its head: lane premerge-exempt marked it, or the model answered a clear no to the drive config's premerge_question under this bar, so it merges on his Approve and tests after the merge. A borderline or missing verdict keeps it held."""
    if (row.get("premerge_exempt") or {}).get("head") == row.get("head"):
        return True
    verdict = row.get("premerge_work") or {}
    return verdict.get("head") == row.get("head") and verdict.get("rule") == rule and verdict["p_yes"] <= CLEARLY_NOT_PREMERGE


def premerge_unjudged(drive: str, read: dict[str, dict], key: str) -> list[str]:
    """Each open PR of a premerge lane with a head and no premerge_question verdict at it under this bar; none when the drive config asks no premerge_question."""
    if not (premerge := store.config(drive)["premerge_lanes"]) or not store.config(drive)["premerge_question"]:
        return []
    return list(dict.fromkeys(landing.key(row) for lane_id, lane in store.load_all(drive).items() if lane_id in premerge for row in seen(lane, read, key, premerge)["prs"] if row["head"] and not row.get("merged") and ((row.get("premerge_work") or {}).get("head"), (row.get("premerge_work") or {}).get("rule")) != (row["head"], key)))


def premerge_work(drive: str, read: dict[str, dict], keys: list[str]) -> dict[str, dict]:
    """A model's answer for each PR of keys, {head, p_yes, rule}, to the drive config's premerge_question against the danger page, with its title and changed files as the evidence, by team-lead's 20:40 ruling that a premerge lane only makes a PR a candidate and only a clear no skips the test before merge."""
    key = rule_key(drive)
    files = readable(keys)
    chunks = [dict(list(files.items())[start : start + JUDGED_AT]) for start in range(0, len(files), JUDGED_AT)]
    found: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=JUDGES) as pool:
        for answered in pool.map(lambda chunk: premerge_asked(drive, chunk, read), chunks):
            found |= {pr: verdict | {"rule": key} for pr, verdict in answered.items()}
    return found


def premerge_asked(drive: str, chunk: dict[str, tuple[str, list[str]]], read: dict[str, dict], evidence: str | None = None) -> dict[str, dict]:
    """One model request asking the drive config's premerge_question of each PR of chunk, with a worker's evidence beside them when it reruns the judge; none when the request fails, so those heads stay held."""
    told = [{"id": f"p{index}", "pr": key, "title": read[key].get("title") or "", "files": paths[:FILES_SHOWN], "more_files": max(0, len(paths) - FILES_SHOWN)} | ({"evidence": evidence} if evidence else {}) for index, (key, (_, paths)) in enumerate(chunk.items())]
    questions = {item["id"]: Binary(f"By the rule, of {item['pr']}: {store.config(drive)['premerge_question']}", yes="any of its files does, or it is unclear", no="its files plainly do none of it, such as CI, tooling, docs or tests alone") for item in told}
    try:
        decision = decide_sync({"rule": rule(drive), "prs": told}, questions, provider=JEV, timeout=JUDGE_SECONDS)
    except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError):
        return {}
    return {item["pr"]: {"head": chunk[item["pr"]][0], "p_yes": round(answer.p_yes, 3)} | ({"evidence": evidence} if evidence else {}) for item in told if isinstance(answer := decision.answers.get(item["id"]), BinaryAnswer)}


def rejudge(drive: str, key: str, evidence: str) -> dict:
    """Ask the premerge_question of one PR again at its head with a worker's evidence beside its title and files, and write the answer as its verdict, by team-lead's 9:34 PM ruling that a worker who thinks the verdict wrong reruns the judge and the new verdict decides. The verdict written, or a refusal when the model did not answer."""
    read = view(drive)
    answered = premerge_asked(drive, readable([key]), read, evidence)
    if key not in answered:
        raise store.Refused(f"the judge did not answer for {key}; its verdict stands")
    verdict = answered[key] | {"rule": rule_key(drive)}
    store.transact(drive, lambda lanes: written(lanes, read, {"premerge_work": {key: verdict}}, None))
    return verdict

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
    return hashlib.sha256((rule(drive) + MARKS).encode()).hexdigest()[:16]


def presence(drive: str, files: dict[str, tuple[str, list[str]]], key: str) -> dict[str, dict]:
    """Each head's repository mark, {head, classes, tooling, rule}: the repository's broad [[dangerous]] tables and its provider's dangerous_paths, by state.dangerous_in as the providers of the drive's checkout read them, which gate nothing since his 6:13 PM words "if I approve something its approved" and are kept for the record; and tooling, every path CI configuration, checkout tooling or a [[tooling]] glob with no dangerous class, which lands on green with no Approve of his by his rulings 02a1e36 and d2a8213. None with no checkout configured, so those heads stay unjudged and hold."""
    from drive import providers, state

    if not (checkout := store.config(drive)["checkout"]):
        return {}
    found = providers.at(Path(checkout))
    globs = [glob for table in found.config.get("tooling", []) for glob in table["paths"]]

    def mark(head: str, paths: list[str]) -> dict:
        classes = state.dangerous_in(found.config, found.repo, paths)
        tool = bool(paths) and not classes and all(found.repo.ci_config(path) or found.repo.checkout_tools(path) or any(fnmatchcase(path, glob) for glob in globs) for path in paths)
        return {"head": head, "classes": classes, "tooling": tool, "rule": key}

    return {pr: mark(head, paths) for pr, (head, paths) in files.items()}
