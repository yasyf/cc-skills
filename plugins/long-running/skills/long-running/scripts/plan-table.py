#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Render the owner report table for the drive dashboard's Plan table panel.

    plan-table.py --doc ID --out PATH [--retro-log ID] [--repo PATH]

Reads the first markdown table under a ``Step`` header in the cc-notes doc ``--doc``,
the owner table the root keeps current. ``--repo`` is the checkout both ``ccn`` and
``ccx`` run in. Only PRs are written ``#N`` in that table; each is read in
one ``ccx vcs pr status --json`` call. A ``live`` or ``done`` row keeps its state, a
row whose PRs have all landed reads ``landed``, and every other row with PRs gets
its PR verdicts counted after its state. With ``--retro-log``, the newest entry of
that cc-notes log tagged ``[step <n>]`` is appended to step ``<n>``'s last column,
where ``<n>`` is the Step cell's text before its first dot. The table is written
to ``--out`` atomically under a refresh line. A desk runs it every 30 minutes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from long_running import ledger

COLUMNS = ["Step", "What it delivers", "State", "What blocks it", "What we are doing to unblock it", "What went wrong or slowed it so far"]
STATE, RETRO = 2, 5
SETTLED = {"live", "done"}
PR = re.compile(r"#(\d{4,})\b")
STEP_KEY = re.compile(r"^\s*([0-9]+[a-z]?)\.")
RETRO_TAG = re.compile(r"^\s*\[step ([0-9]+[a-z]?)\]\s*", re.IGNORECASE)
CELL = re.compile(r"^\|(.*)\|\s*$")
RULE = re.compile(r"^\|[\s:|-]+\|\s*$")
PACIFIC = ZoneInfo("America/Los_Angeles")


def owner_rows(body: str) -> list[list[str]]:
    rows: list[list[str]] = []
    inside = False
    for line in body.splitlines():
        match = CELL.match(line)
        if not match:
            if inside:
                break
            continue
        cells = [cell.strip() for cell in match[1].split("|")]
        if not inside:
            inside = cells[0].lower() == "step"
            continue
        if RULE.match(line):
            continue
        if len(cells) != len(COLUMNS):
            raise ValueError(f"owner table row has {len(cells)} cells, not {len(COLUMNS)}: {line}")
        rows.append(cells)
    if not rows:
        raise ValueError("doc has no markdown table whose header starts with Step")
    return rows


def step_key(step: str) -> str | None:
    match = STEP_KEY.match(step)
    return match[1].lower() if match else None


def latest_retro(entries: list[dict]) -> dict[str, tuple[str, str]]:
    latest: dict[str, tuple[str, str]] = {}
    for entry in sorted(entries, key=lambda entry: entry["ts"]):
        if match := RETRO_TAG.match(entry["text"]):
            latest[match[1].lower()] = (entry["ts"], entry["text"][match.end() :].strip())
    return latest


def refreshed_state(state: str, verdicts: list[str]) -> str:
    if not verdicts or state.lower() in SETTLED:
        return state
    counts = Counter(verdict.split(":")[0] for verdict in verdicts)
    if counts["landed"] == len(verdicts):
        return "landed"
    return f"{state}, PRs: " + ", ".join(f"{count} {verdict}" for verdict, count in sorted(counts.items()))


def pacific(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(PACIFIC).strftime("%-I:%M %p")


def one_line(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def render(rows: list[list[str]], verdicts: dict[int, str], retro: dict[str, tuple[str, str]], now: datetime, doc: str) -> str:
    out = [
        "# Plan table",
        "",
        f"Refreshed {now.astimezone(PACIFIC).strftime('%-I:%M %p %b %-d')} Pacific from doc {doc[:7]}, with live PR states and the watchdog's latest retro line per step.",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    for cells in rows:
        cells = list(cells)
        named = [verdicts[number] for number in dict.fromkeys(int(n) for n in PR.findall(" ".join(cells))) if number in verdicts]
        cells[STATE] = refreshed_state(cells[STATE], named)
        if (key := step_key(cells[0])) and key in retro:
            ts, text = retro[key]
            cells[RETRO] = f"{cells[RETRO]} Watchdog {pacific(ts)}: {text}"
        out.append("| " + " | ".join(one_line(cell) for cell in cells) + " |")
    return "\n".join(out) + "\n"


def pr_verdicts(shell: ledger.Shell, numbers: list[int]) -> dict[int, str]:
    if not numbers:
        return {}
    statuses = json.loads(shell.run(["ccx", "vcs", "pr", "status", *map(str, numbers), "--json"]))
    return {status["number"]: status["verdict"] for status in statuses}


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plan-table.py", description=__doc__.splitlines()[0])
    parser.add_argument("--doc", required=True, metavar="ID")
    parser.add_argument("--out", required=True, type=Path, metavar="PATH")
    parser.add_argument("--retro-log", metavar="ID")
    parser.add_argument("--repo", default=".", metavar="PATH")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    os.chdir(args.repo)
    shell = shell or ledger.Shell()
    doc = json.loads(shell.run(["ccn", "doc", "show", args.doc, "--json"]))
    rows = owner_rows(doc["body"])
    numbers = list(dict.fromkeys(int(n) for cells in rows for n in PR.findall(" ".join(cells))))
    verdicts = pr_verdicts(shell, numbers)
    retro = latest_retro(json.loads(shell.run(["ccn", "log", "show", args.retro_log, "--json"]))["entries"]) if args.retro_log else {}
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = out.with_suffix(".tmp")
    staged.write_text(render(rows, verdicts, retro, now or datetime.now(PACIFIC), doc["id"]))
    staged.replace(out)
    return 0


if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    raise SystemExit(main())
