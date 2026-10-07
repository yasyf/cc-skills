from __future__ import annotations

import json
from datetime import datetime

from livedash import Col, Context, Table, Tile, Tiles, component
from livedash.components import ledger as ledgers
from livedash.components import stack

PR_FIELDS = """number title url isDraft additions deletions createdAt baseRefName headRefName headRefOid
author { login }
reviewThreads(first: 100) { nodes { isResolved } }
reviews(last: 50) { nodes { author { login } state body submittedAt commit { oid } } }
commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes { __typename ... on CheckRun { name conclusion status } ... on StatusContext { context state } } } } } } }"""
PASSING = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
AGENT_REVIEW_MARKS = ("<!-- rules-review", "@forge-pr-reviewer accept")
REVIEW_COLUMNS = [
    Col("pr", "PR", "link"),
    Col("title", "Title"),
    Col("lane", "Lane"),
    Col("size", "Size", "num"),
    Col("ci", "CI", "badge"),
    Col("bot", "Bot", "badge"),
    Col("rules", "Rules review", "badge"),
    Col("threads", "Open threads", "num"),
    Col("verdict", "Queue", "badge"),
    Col("held", "Held since", "age"),
    Col("owner", "Owner", "badge"),
    Col("opened", "Opened", "age"),
]


def pull_requests(ctx: Context, repo: str, numbers: list[int]) -> dict[int, dict]:
    if not numbers:
        return {}
    owner, name = repo.split("/", 1)
    aliases = " ".join(f"pr{number}: pullRequest(number: {number}) {{ {PR_FIELDS} }}" for number in numbers)
    data = ctx.gh_graphql(f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {aliases} }} }}")
    return {node["number"]: node for node in data["repository"].values() if node}


def contexts_of(pr: dict) -> list[dict]:
    commits = pr["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    return rollup["contexts"]["nodes"] if rollup else []


def check_state(node: dict) -> str:
    if node["__typename"] == "CheckRun":
        if node["status"] != "COMPLETED":
            return "pending"
        return "green" if node["conclusion"] in PASSING else "red"
    return {"SUCCESS": "green", "PENDING": "pending", "EXPECTED": "pending"}.get(node["state"], "red")


def ci_state(contexts: list[dict], ignore: list[str], bots: list[str]) -> str:
    states = [check_state(node) for node in contexts if (node.get("name") or node.get("context")) not in {*ignore, *bots}]
    return "red" if "red" in states else "pending" if "pending" in states else "green" if states else "none"


def bot_state(contexts: list[dict], bots: list[str]) -> str:
    states = [check_state(node) for node in contexts if node.get("name") in bots]
    return "red" if "red" in states else "pending" if "pending" in states else "green" if states else "none"


def human_reviews(pr: dict) -> list[dict]:
    return [review for review in pr["reviews"]["nodes"] if review["author"] and not any(mark in (review["body"] or "") for mark in AGENT_REVIEW_MARKS)]


def owner_state(fields: dict, pr: dict, owner_login: str) -> str:
    if fields.get("owner_reviewed_at"):
        return "reviewed"
    if owner_login and any(review["author"]["login"] == owner_login for review in human_reviews(pr)):
        return "reviewed"
    return "waiting"


def tone(ci: str, verdict: str, held: bool) -> str:
    if ci == "red":
        return "bad"
    if held or verdict.startswith("blocked") or ci == "pending":
        return "warn"
    return "ok"


def record_review(ctx: Context, row: dict, text: str) -> str:
    note = f": {text}" if text else ""
    record = ctx.json(["cci", "post", "--drive", ctx.facts["cci_drive"], "--lane", "owner", "--kind", "owner", "--to", "main", "--topic", row["key"], "--text", f"Reviewed #{row['key']} at {row['head'][:12]}{note}"[:400], "--json"])
    return f"cci #{record['seq']}: main records owner_reviewed_at on #{row['key']}"


@component("pr-review-queue", "PRs waiting for review", every="2m", timeout="60s", actions={"reviewed": record_review})
def review_queue(
    ctx: Context,
    *,
    repo: str,
    ledger: str | None = None,
    prs: list[int] | None = None,
    order: str = "stack",
    ignore_checks: list[str] = ["Graphite / mergeability_check"],
    bot_checks: list[str] = ["ai-review"],
    owner_login: str = "",
    parents: dict[str, str] | None = None,
) -> Table:
    """Open PRs from `prs`, else the ledger's open rows: size, CI without the ignored checks, the review bot's check, the
    ledger's rules-review verdict at the current head, unresolved threads, the queue verdict from `ccx vcs pr status`, hold
    age, and whether the owner reviewed (`owner_reviewed_at`, or a review by `owner_login`). Agent-posted reviews never count.
    `order: stack` walks each stack from its trunk; `age` puts the oldest first. Action `reviewed` posts an owner record to main.
    """
    rows = ledgers.rows(ctx, ledger) if ledger and not prs else {}
    opened = {int(key): fields for key, fields in rows.items() if key.isdigit() and fields.get("state", "open") == "open"}
    numbers = sorted(prs or opened)
    details = pull_requests(ctx, repo, numbers)
    statuses = stack.queue_status(ctx, repo, numbers)
    if order == "stack":
        numbers = stack.ordered(stack.parents_of({number: opened.get(number) or {"base": details[number]["baseRefName"], "branch": details[number]["headRefName"]} for number in numbers}, parents or {}))
    elif order == "age":
        numbers.sort(key=lambda number: details[number]["createdAt"])
    else:
        raise ValueError(f"order must be stack or age, not {order!r}")
    out = []
    for number in numbers:
        pr, fields, status = details[number], opened.get(number, {}), statuses.get(number, {})
        contexts = contexts_of(pr)
        ci = ci_state(contexts, ignore_checks, bot_checks)
        held = ledgers.held(fields)
        verdict = status.get("verdict") or status.get("queue") or ""
        out.append(
            {
                "key": str(number),
                "cite": f"pr:{number}",
                "pr": f"#{number}",
                "pr_url": pr["url"],
                "title": pr["title"],
                "lane": fields.get("lane") or pr["author"]["login"],
                "size": f"+{pr['additions']:,}/-{pr['deletions']:,}",
                "additions": pr["additions"],
                "deletions": pr["deletions"],
                "ci": ci,
                "bot": bot_state(contexts, bot_checks),
                "rules": ledgers.rules_verdict(rows, str(number), fields) if fields else "",
                "threads": sum(not thread["isResolved"] for thread in pr["reviewThreads"]["nodes"]),
                "verdict": verdict,
                "held": fields.get("hold_since") if held else None,
                "hold_reason": fields.get("hold_reason") if held else None,
                "owner": owner_state(fields, pr, owner_login),
                "opened": pr["createdAt"],
                "head": pr["headRefOid"],
                "draft": pr["isDraft"],
                "tone": tone(ci, verdict, held),
            }
        )
    footer = {"pr": f"{len(out)} PRs", "size": f"+{sum(row['additions'] for row in out):,}/-{sum(row['deletions'] for row in out):,}", "threads": sum(row["threads"] for row in out)}
    return Table(REVIEW_COLUMNS, out, footer=footer, note=None if out else "No open PRs.")


@component("gh-quota", "GitHub API quota", every="2m")
def quota(ctx: Context, *, floor: int = 500) -> Tiles:
    """REST core and GraphQL remaining quota from `gh api rate_limit`, which spends none; under `floor` reads bad."""
    resources = ctx.json(["gh", "api", "rate_limit"])["resources"]
    tiles = []
    for name in ("core", "graphql"):
        limit = resources[name]
        reset = datetime.fromtimestamp(limit["reset"]).astimezone().strftime("%H:%M")
        share = limit["remaining"] / limit["limit"] if limit["limit"] else 0
        tiles.append(Tile(f"GitHub {name}", limit["remaining"], f"of {limit['limit']}", "bad" if limit["remaining"] < floor else "warn" if share < 0.2 else "ok", f"resets {reset}"))
    return Tiles(tiles)
