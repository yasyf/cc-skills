"""One batched read per source per pass, assembled into :class:`lr.machine.Facts` rows.

GitHub, the merge queue and CI are read together through `ccx vcs pr status --json`, one
call for every open PR; the commits every open PR carries come from one GraphQL query; the
holds are one `cci tail`; the bus is read once from the store's cursor and lands as fact
rows keyed by PR, head and kind. Call counts are the same at one open PR and at a thousand.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from lr.bus import Bus, Record
from lr.machine import Facts
from lr.store import Store

Run = Callable[[list[str]], str]
BUS_CURSOR = "bus"
GATEWAY = ["ccx", "vcs", "gh", "--max-age", "0s", "--"]
CI = {"green": "success", "red": "failure", "pending": "pending", "none": "pending"}
QUEUE = {"not queued": "none", "queued": "queued", "evicted": "evicted", "landed": "none", "unknown": "none"}


def open_prs(run: Run, repo: str, author: str, label: str) -> list[int]:
    rows = json.loads(run([*GATEWAY, "pr", "list", "--repo", repo, "--state", "open", "--limit", "500", "--json", "number,author,labels"]))
    return sorted(row["number"] for row in rows if row["author"]["login"] == author or any(found["name"] == label for found in row["labels"]))


def statuses(run: Run, numbers: list[int]) -> list[dict]:
    return json.loads(run(["ccx", "vcs", "pr", "status", "--json", *map(str, numbers)])) if numbers else []


def carried(run: Run, repo: str, numbers: list[int]) -> dict[int, tuple[int, ...]]:
    """For every open PR, the other open PRs whose heads sit among its commits: a branch GitHub says is based on trunk but still carries a sibling's work."""
    if not numbers:
        return {}
    owner, name = repo.split("/")
    fields = " ".join(f"pr{n}: pullRequest(number: {n}) {{ headRefOid commits(first: 100) {{ nodes {{ commit {{ oid }} }} }} }}" for n in numbers)
    query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'
    data = json.loads(run([*GATEWAY, "api", "graphql", "-f", f"query={query}"]))["data"]["repository"]
    heads = {n: data[f"pr{n}"]["headRefOid"] for n in numbers}
    commits = {n: {node["commit"]["oid"] for node in data[f"pr{n}"]["commits"]["nodes"]} for n in numbers}
    return {n: tuple(m for m in numbers if m != n and heads[m] in commits[n]) for n in numbers}


def holds(run: Run, drive: str) -> dict[int, str]:
    """Each PR an open cci `hold` names, mapped to who holds it: the owner, or root for every other lane."""
    out = {}
    for line in run(["cci", "tail", "--drive", drive, "--since", "0", "--json", "--budget", "4000000", "--kind=hold"]).splitlines():
        record = json.loads(line)
        if record["kind"] == "hold" and record["status"] == "open":
            for ref in record["refs"].get("prs", []):
                out[int(str(ref).rsplit("#", 1)[-1])] = "owner" if record["lane"] == "owner" else "root"
    return out


def ingest(store: Store, repo: str, records: list[Record]) -> None:
    for record in records:
        if record.kind == "grade":
            store.put_fact(repo, record.pr, record.head, record.grader, record.verdict, record.at)
        elif record.kind == "tap":
            store.put_fact(repo, record.pr, record.head, "tap", record.value, record.at)
            if record.by == "rule":
                store.put_fact(repo, record.pr, record.head, "exempt", record.rule, record.at)
        elif record.kind == "verdict":
            store.put_fact(repo, record.pr, record.head, "verdict", record.verdict, record.at)
        elif record.kind == "head":
            store.put_fact(repo, record.pr, record.head, "lane", record.lane, record.at)
        store.advance(BUS_CURSOR, record.seq)


def assemble(store: Store, repo: str, rows: list[dict], carries: dict[int, tuple[int, ...]], held: dict[int, str]) -> list[Facts]:
    out = []
    for row in rows:
        facts = store.facts(repo, row["number"], row["head"])
        out.append(
            Facts(
                repo=repo,
                number=row["number"],
                head=row["head"],
                base=row["base"],
                lane=facts.get("lane", ""),
                open=row["state"] == "OPEN",
                merged=row["state"] == "MERGED" or row["queue"] == "landed",
                ci=CI[row["ci"]["state"]],
                mergeable=mergeable(row),
                queue=QUEUE[row["queue"]],
                queued_head=row["head"] if row["queue"] == "queued" else None,
                resubmitted=int(facts.get("resubmitted", "0")),
                finder=facts.get("finder"),
                fidelity=facts.get("fidelity"),
                verdict="verdict" in facts,
                tap=facts.get("tap"),
                exempt="exempt" in facts,
                hold=held.get(row["number"]),
                carries=carries.get(row["number"], ()),
            )
        )
    return out


def mergeable(row: dict) -> str:
    causes = set(row["verdict"].removeprefix("blocked:").split(",")) if row["verdict"].startswith("blocked:") else set()
    if "conflict" in causes:
        return "conflict"
    if "mergeable-unknown" in causes:
        return "unknown"
    if any(cause.startswith("needs-restack") for cause in causes):
        return "behind"
    return "clean"


def snapshot(store: Store, bus: Bus, run: Run, repo: str, drive: str, author: str, label: str) -> list[Facts]:
    records, _rejected = bus.read(store.cursor(BUS_CURSOR), kinds=("head", "grade", "tap", "verdict"))
    ingest(store, repo, records)
    numbers = open_prs(run, repo, author, label)
    return assemble(store, repo, statuses(run, numbers), carried(run, repo, numbers), holds(run, drive))
