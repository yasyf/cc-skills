from __future__ import annotations

import json
import re
from pathlib import Path

from livedash import Checklist, Context, Gate, component, payloads
from livedash.components import ledger as ledgers
from livedash.components import metrics, stack

REGISTER_STATUS = {"closed": "pass", "resolved": "pass", "validated": "pass", "holds": "pass", "invalidated": "fail", "broken": "fail", "rejected": "fail", "blocked": "blocked", "waived": "waived"}
PR_SOURCE = re.compile(r"^(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<pr>\d+)$")


def entries(registers: dict) -> dict[str, dict]:
    return {entry["id"]: entry for value in registers.values() if isinstance(value, list) for entry in value if isinstance(entry, dict) and "id" in entry}


@component("design-gates", "Design gates", every="5m", timeout="60s")
def design_gates(
    ctx: Context,
    *,
    repo_path: Path,
    doc: str,
    ids: list[str],
    ref: str = "origin/main",
    fetch: bool = True,
    owners: dict[str, str] = {},
    closes: dict[str, str] = {},
    link: str | None = None,
) -> Checklist:
    """Register entries `ids` from `<doc>/registers.json` at `ref` in a design-doc checkout, as gates: closed, resolved or
    validated pass; invalidated fails; anything else is open, blocked on the decisions it blocks. `owners` and `closes`
    name who owns each gate and the PR that closes it."""
    if fetch and "/" in ref:
        remote, branch = ref.split("/", 1)
        ctx.run(["git", "-C", str(repo_path), "fetch", "--quiet", remote, branch])
    registers = entries(json.loads(ctx.run(["git", "-C", str(repo_path), "show", f"{ref}:{doc}/registers.json"])))
    gates = []
    for ident in ids:
        entry = registers.get(ident)
        if entry is None:
            gates.append(Gate(ident, f"{ident} is not in {doc}/registers.json at {ref}", "not-created", owner=owners.get(ident), closes=closes.get(ident)))
            continue
        status = REGISTER_STATUS.get(entry.get("s") or "", "open")
        blocker = f"closed by {entry['by']}" if entry.get("by") else f"blocks {', '.join(entry['blocks'])}" if entry.get("blocks") and status == "open" else None
        gates.append(Gate(ident, entry.get("h") or entry.get("t", ident), status, blocker, owners.get(ident), closes.get(ident), f"{link}#{ident}" if link else None))
    return Checklist(gates)


def pr_gate(ctx: Context, source: str, repo: str) -> tuple[str, str | None]:
    if not (match := PR_SOURCE.match(source)):
        raise ValueError(f"pr:{source} must name a PR as N or owner/name#N")
    number = int(match["pr"])
    status = stack.queue_status(ctx, match["repo"] or repo, [number]).get(number, {})
    verdict = status.get("verdict", "")
    if verdict == "landed":
        return "pass", None
    if status.get("state") == "CLOSED":
        return "fail", "closed without landing"
    if verdict.startswith("blocked"):
        return "blocked", verdict.removeprefix("blocked:")
    return "open", verdict or None


def ledger_gate(ctx: Context, key: str, ledger: str | None) -> tuple[str, str | None]:
    if not ledger:
        raise ValueError(f"ledger:{key} needs the dashboard's ledger fact or a `ledger` parameter")
    fields = ledgers.rows(ctx, ledger).get(key)
    if fields is None:
        return "not-created", f"no ledger row {key}"
    if fields.get("state") == "landed" or fields.get("answered_at"):
        return "pass", None
    if fields.get("dropped_at"):
        return "waived", "dropped"
    return "open", fields.get("hold_reason")


def monitor_gate(ctx: Context, query: str) -> tuple[str, str | None]:
    found = metrics.pup(ctx, ["monitors", "search", "--query", query]).get("monitors") or []
    if not found:
        return "not-created", f"no monitor matches {query}"
    statuses = [monitor.get("status") for monitor in found]
    if "Alert" in statuses:
        return "fail", f"{statuses.count('Alert')} alerting"
    return ("pass", None) if all(status == "OK" for status in statuses) else ("open", ", ".join(sorted(set(map(str, statuses)))))


@component("gates", "Gates", every="2m", timeout="60s")
def gates(
    ctx: Context,
    *,
    gates: dict[str, str],
    titles: dict[str, str] = {},
    owners: dict[str, str] = {},
    closes: dict[str, str] = {},
    repo: str | None = None,
    ledger: str | None = None,
) -> Checklist:
    """Named gates, each read from one source: `pr:N` (landed passes; blocked carries the queue's reason), `file:<glob>`
    (a match passes, none has not run), `ledger:<key>` (landed or answered passes), `monitor:<query>` (all OK passes,
    any Alert fails, none is not created), or `status:<status>` held by hand until the gate's owner updates it."""
    out = []
    for ident, source in gates.items():
        kind, _, rest = source.partition(":")
        if kind == "pr":
            status, blocker = pr_gate(ctx, rest, repo or "")
        elif kind == "file":
            status, blocker = ("pass", None) if ctx.glob(rest) else ("not-run", f"nothing matches {rest}")
        elif kind == "ledger":
            status, blocker = ledger_gate(ctx, rest, ledger)
        elif kind == "monitor":
            status, blocker = monitor_gate(ctx, rest)
        elif kind == "status" and rest in payloads.GATE_STATUSES:
            status, blocker = rest, None
        else:
            raise ValueError(f"gate {ident} reads {source!r}; use pr:, file:, ledger:, monitor: or status:<{'|'.join(payloads.GATE_STATUSES)}>")
        out.append(Gate(ident, titles.get(ident, ident), status, blocker, owners.get(ident), closes.get(ident)))
    return Checklist(out)
