from __future__ import annotations

import re
from pathlib import Path

from livedash import Col, Context, Table, component

ASK_PREFIX = "ask/"
REVIEW_PREFIX = "review/"
HOLD_SUBJECT = re.compile(r"(?<![\w/])#(?P<pr>\d{2,6})\b|\blane:(?P<lane>[\w.-]+)")
LIFTED = re.compile(r"\bLIFT(?:ED|S)?\b")
DONE_ASKS = frozenset({"dropped", "answered", "delivered"})


def rows(ctx: Context, ledger: str) -> dict[str, dict]:
    return {row["key"]: row["fields"] for row in ctx.json(["ccn", "-R", str(ctx.facts["checkout"]), "ledger", "row", "list", ledger, "--json"])}


def held(fields: dict) -> bool:
    return bool(fields.get("hold_until"))


def current_head(fields: dict) -> str:
    return fields.get("head") or fields.get("reported_head", "")


def rules_verdict(all_rows: dict[str, dict], pr: str, head: str) -> str:
    review = all_rows.get(f"{REVIEW_PREFIX}{pr}@{head}")
    if review is None:
        return "none"
    return f"{review['verdict']} (overridden)" if review.get("override") else review["verdict"]


def linked(fields: dict) -> list[str]:
    return re.findall(r"\d+", fields.get("prs") or "")


def ask_state(fields: dict, prs: dict[str, dict]) -> str:
    if fields.get("dropped_at"):
        return "dropped"
    if fields.get("answered_at"):
        return "answered"
    numbers = linked(fields)
    if numbers and all(prs.get(number, {}).get("state") == "landed" for number in numbers):
        return "delivered"
    if any(prs.get(number, {}).get("state") == "open" for number in numbers):
        return "in-pr"
    return "open"


def hold_lines(path: Path) -> list[dict]:
    out = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or LIFTED.search(line):
            continue
        subjects = [match for match in HOLD_SUBJECT.finditer(line)]
        reason = HOLD_SUBJECT.sub("", line).strip()
        out.append({"line": number, "prs": [match["pr"] for match in subjects if match["pr"]], "lanes": [match["lane"] for match in subjects if match["lane"]], "reason": reason})
    return out


@component("held-prs", "Held PRs", question="Which PRs are held, why, and what lifts each hold?", reads=["ccn ledger row list", "holds file"], every="1m")
def held_prs(ctx: Context, *, ledger: str, holds: Path | None = None) -> Table:
    """Open ledger PRs carrying a hold (reason, since, until), joined with the lines of a holds file that name the PR by
    `#N` or its lane by `lane:<name>`; a line containing LIFT, LIFTED or LIFTS counts as lifted and is skipped."""
    all_rows = rows(ctx, ledger)
    lines = hold_lines(holds) if holds else []
    out = []
    for key, fields in sorted(all_rows.items()):
        if not key.isdigit() or fields.get("state", "open") != "open":
            continue
        named = [line for line in lines if key in line["prs"] or fields.get("lane") in line["lanes"]]
        if not held(fields) and not named:
            continue
        out.append(
            {
                "key": key,
                "cite": f"pr:{key}",
                "pr": f"#{key}",
                "pr_url": f"https://github.com/{ctx.facts['repo']}/pull/{key}",
                "lane": fields.get("lane"),
                "since": fields.get("hold_since"),
                "until": fields.get("hold_until"),
                "reason": fields.get("hold_reason") or "; ".join(line["reason"] for line in named),
                "file": ", ".join(f"line {line['line']}" for line in named),
                "tone": "warn",
            }
        )
    return Table(
        [Col("pr", "PR", "link"), Col("lane", "Lane"), Col("since", "Held since", "age"), Col("reason", "Reason"), Col("file", "Holds file"), Col("until", "Until")],
        out,
        note=None if out else "Nothing is held.",
    )


@component("asks", "Open asks", question="Which owner asks are still open, and what finishes each one?", reads=["ccn ledger row list", "cci digest"], every="2m")
def asks(ctx: Context, *, ledger: str | None = None, keys: list[str] = [], cci_to: list[str] = ["owner", "main", "root"]) -> Table:
    """The ledger's `ask/*` rows that are not dropped, answered or delivered (every linked PR landed), with their accept
    criteria; `keys` narrows to named rows. Open cci asks addressed to any `cci_to` reader join them."""
    all_rows = rows(ctx, ledger) if ledger else {}
    prs = {key: fields for key, fields in all_rows.items() if key.isdigit()}
    out = []
    for key, fields in sorted(all_rows.items()):
        if not key.startswith(ASK_PREFIX) or (keys and key not in keys):
            continue
        if (state := ask_state(fields, prs)) in DONE_ASKS:
            continue
        out.append({"key": key, "cite": f"ask:{key}", "ask": fields.get("text"), "accept": fields.get("accept"), "lane": fields.get("lane"), "state": state, "asked": fields.get("asked_at"), "prs": fields.get("prs"), "tone": "warn" if state == "open" else None})
    if ctx.facts.get("cci_drive"):
        for record in ctx.cci("digest", since_time=ctx.facts.get("started_at")).get("open_asks") or []:
            if set(record.get("to") or []) & set(cci_to):
                out.append({"key": f"cci:{record['seq']}", "cite": f"cci:{record['seq']}", "ask": record["text"], "accept": (record.get("fields") or {}).get("accept"), "lane": record["lane"], "state": "open", "asked": record["at"], "tone": "warn"})
    return Table([Col("ask", "Ask"), Col("accept", "Done when"), Col("lane", "Lane"), Col("state", "State", "badge"), Col("asked", "Asked", "age")], out, note=None if out else "No open asks.")
