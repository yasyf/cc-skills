from __future__ import annotations

from livedash import Context, Graph, Kv, Node, component, view
from livedash.components import ledger as ledgers

LANDABLE = "landable"
OPEN = "OPEN"


def queue_status(ctx: Context, repo: str, numbers: list[int]) -> dict[int, dict]:
    if not numbers:
        return {}
    return {row["number"]: row for row in ctx.json(["ccx", "vcs", "pr", "status", *map(str, numbers), "--json", "-R", repo])}


def still_open(rows: dict[int, dict], statuses: dict[int, dict]) -> dict[int, dict]:
    return {number: fields | {"base": fields.get("base") or statuses[number].get("base")} for number, fields in rows.items() if statuses[number]["state"] == OPEN}


def parents_of(prs: dict[int, dict], overrides: dict[str, str]) -> dict[int, int | str]:
    by_branch = {fields.get("branch"): number for number, fields in prs.items() if fields.get("branch")}
    out: dict[int, int | str] = {}
    for number, fields in prs.items():
        if (override := overrides.get(str(number))) is not None:
            out[number] = int(override) if override.isdigit() and int(override) in prs else override
        else:
            out[number] = by_branch.get(fields.get("base"), fields.get("base") or "?")
    return out


def children_of(parents: dict[int, int | str]) -> dict[int | str, list[int]]:
    children: dict[int | str, list[int]] = {}
    for number, parent in sorted(parents.items()):
        children.setdefault(parent, []).append(number)
    return children


def ordered(parents: dict[int, int | str]) -> list[int]:
    children = children_of(parents)
    out: list[int] = []

    def walk(node: int | str) -> None:
        for child in children.get(node, []):
            out.append(child)
            walk(child)

    for trunk in sorted({parent for parent in parents.values() if isinstance(parent, str)}):
        walk(trunk)
    return out + sorted(set(parents) - set(out))


def bottom_prefix(parents: dict[int, int | str], verdicts: dict[int, str]) -> list[int]:
    prefix: list[int] = []
    for number in ordered(parents):
        parent = parents[number]
        if verdicts.get(number) == LANDABLE and (isinstance(parent, str) or parent in prefix):
            prefix.append(number)
    return prefix


def node_tone(status: dict, fields: dict) -> str:
    verdict = status.get("verdict", "")
    if (status.get("ci") or {}).get("state") == "red":
        return "bad"
    if verdict == LANDABLE and not ledgers.held(fields):
        return "ok"
    return "warn"


@component("stack-graph", "Stack", question="How do the open PRs stack, and which bottom prefix can land?", reads=["ccn ledger row list", "ccx vcs pr status"], every="2m", timeout="60s")
def stack_graph(ctx: Context, *, ledger: str, repo: str, parents: dict[str, str] | None = None) -> Graph:
    """The ledger's open PRs that `ccx vcs pr status` still reports open, as stacks rooted at their trunk branches, drawn
    from base and branch fields, or the status's base where the ledger has none; a Graphite `graphite-base/<n>` base names no PR, so `parents` maps a PR number to its parent PR or trunk. Nodes are toned by the
    `ccx vcs pr status` verdict and ledger holds; the largest landable bottom prefix of each stack is highlighted."""
    listed = {int(key): fields for key, fields in ledgers.rows(ctx, ledger).items() if key.isdigit() and fields.get("state", "open") == "open"}
    statuses = queue_status(ctx, repo, sorted(listed))
    rows = still_open(listed, statuses)
    tree = parents_of(rows, parents or {})
    nodes = [Node(trunk, trunk, "warn" if trunk.startswith("graphite-base/") else "muted") for trunk in sorted({parent for parent in tree.values() if isinstance(parent, str)})]
    for number in ordered(tree):
        status = statuses.get(number, {})
        lane = view.lane_name(rows[number].get("lane"), ctx.facts) or ""
        nodes.append(Node(str(number), f"#{number} {lane}".strip(), node_tone(status, rows[number]), f"https://github.com/{repo}/pull/{number}"))
    prefix = bottom_prefix(tree, {number: status.get("verdict", "") for number, status in statuses.items()})
    return Graph(nodes, [[str(parent), str(number)] for number, parent in tree.items()], [str(number) for number in prefix])


@component("landing-preview", "What an approval lands", question="If the owner approves each stack's bottom now, what lands?", reads=["the stack-graph card", "the pr-review-queue card"], every="30s")
def landing_preview(ctx: Context, *, graph: str = "stack", queue: str = "review") -> Kv:
    """Reads the `graph` card's stacks and the `queue` card's verdicts: per stack, which PRs approving its bottom lands now,
    the first PR the prefix stops at and why, and how many PRs wait behind it."""
    drawn, table = ctx.latest(graph), ctx.latest(queue)
    if drawn is None or table is None:
        return Kv({"waiting on": f"cards {graph} and {queue} to run once"})
    verdicts = {int(row["key"]): row["verdict"] for row in table.rows}
    tree: dict[int, int | str] = {}
    for parent, child in drawn.edges:
        tree[int(child)] = int(parent) if parent.isdigit() else parent
    prefix = set(bottom_prefix(tree, verdicts))
    children = children_of(tree)
    pairs: dict[str, str | int | float | None] = {}
    for root in [number for number, parent in sorted(tree.items()) if isinstance(parent, str)]:
        chain, node = [root], root
        while len(children.get(node, [])) == 1:
            node = children[node][0]
            chain.append(node)
        landing = [number for number in chain if number in prefix]
        stopped = next((number for number in chain if number not in prefix), None)
        behind = len(chain) - len(landing) - (1 if stopped else 0)
        if landing:
            head = f"lands #{landing[0]}" + (f"…#{landing[-1]} ({len(landing)} PRs)" if len(landing) > 1 else "")
        else:
            head = "lands nothing"
        tail = f"; stops at #{stopped}: {verdicts.get(stopped) or 'no verdict'}" if stopped else ""
        pairs[f"Approve #{root} ({tree[root]})"] = head + tail + (f"; {behind} more behind it" if behind > 0 else "")
    return Kv(pairs or {"stacks": "none open"})
