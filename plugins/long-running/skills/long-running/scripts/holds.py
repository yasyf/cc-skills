#!/usr/bin/env python3
"""Edit the root's landing holds file, the one desk-runner.py reads before every enqueue.

`add --pr <n> --reason R` writes `#<n> R`, `add --lane <name> --reason R` writes
`lane:<name> R`, `add --all --reason R` writes `hold:all R`, and `release --pr <n> --reason R`
or `release --lane <name> --reason R` writes `release #<n> R` or `release lane:<name> R`.
`rm` deletes the entry `add` or `release` names, and `list` prints every line with what it holds.
A line holds the one PR its leading `#<n>` names; every later `#<n>` is prose. A reason
never carries `lane:<name>`, and a PR line whose reason says the PR should land is refused:
remove its hold instead. Name the file with `--file`, or with `--config` for a desk-runner config.

This module is the one parse of that file. A line is a PR hold, a lane hold written
`lane:<name> R`, `<lane> HOLD R`, or `HOLD <lane> R`, a `hold:all R` line, a `release #<n>`
or `release lane:<name>` line, or blank. Any other line is unknown: `list` says so, and the
landing runner enqueues nothing while one stands. `held` resolves the entries against ledger rows,
and `rowless` names each lane line whose lane no ledger row carries, which stops the runner too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

HELD_PR = re.compile(r"^#(\d+)", re.MULTILINE)
HELD_LANE = re.compile(r"\blane:(\S+)")
LANE_HOLD = re.compile(r"^(?:HOLD\s+([\w.-]+)|([\w.-]+)\s+HOLD)(?=\s|$)")
HOLD_ALL = re.compile(r"^hold:all\s+\S", re.MULTILINE)
RELEASE_LINE = re.compile(r"^release\s+(.*)$", re.MULTILINE)
LANE_NAME = re.compile(r"^[\w.-]+$")
PR_REF = re.compile(r"#\d")
SHOULD_LAND = re.compile(
    r"\b(?:ours|ready|ok|okay|clear|cleared|free|safe|good|fine) to (?:land|merge|enqueue)\b"
    r"|^(?:land|merge|enqueue|release|unhold|lift)\b|\b(?:released|lifted|unheld|not held)\b",
    re.IGNORECASE,
)


class Refused(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    line: str

    @property
    def release(self) -> bool:
        return bool(RELEASE_LINE.match(self.line))

    @property
    def prs(self) -> list[str]:
        return [] if self.release else HELD_PR.findall(self.line)

    @property
    def lanes(self) -> list[str]:
        if self.release:
            return []
        named = LANE_HOLD.match(self.line)
        return [*HELD_LANE.findall(self.line), *filter(None, named.groups() if named else ())]

    @property
    def holds_all(self) -> bool:
        return bool(HOLD_ALL.match(self.line))

    @property
    def freed(self) -> str:
        named = RELEASE_LINE.match(self.line)
        return named[1] if named else ""

    @property
    def unknown(self) -> bool:
        return bool(self.line.strip()) and not (self.release or self.holds_all or self.prs or self.lanes)

    def describe(self) -> str:
        if self.release:
            return f"releases {self.line.split(None, 1)[1]}"
        held = [*(f"#{pr}" for pr in self.prs), *(f"lane:{lane}" for lane in self.lanes)]
        if self.holds_all:
            held.insert(0, "every open row")
        return f"holds {', '.join(held)}" if held else "matches no form; the landing runner enqueues nothing until it is fixed"


def holds_path(args: argparse.Namespace) -> Path:
    if args.file:
        return Path(args.file).expanduser()
    return Path(json.loads(Path(args.config).expanduser().read_text())["landing"]["holds"]).expanduser()


def read(path: Path) -> list[Entry]:
    return [Entry(line) for line in path.read_text().splitlines()] if path.is_file() else []


def held(entries: Sequence[Entry], rows: dict[str, dict]) -> set[str]:
    """Every PR `entries` hold: the PR a `#<n>` line leads with, each open row of a lane a lane line names, and under `hold:all` each open row no `release` line names by number or by lane. A lane with no open row holds nothing."""
    open_rows = {pr for pr, row in rows.items() if row.get("state", "open") == "open"}
    lanes = {lane for entry in entries for lane in entry.lanes}
    numbers = {pr for entry in entries for pr in entry.prs} | {pr for pr in open_rows if rows[pr].get("lane") in lanes}
    if any(entry.holds_all for entry in entries):
        numbers |= open_rows - released(entries, rows)
    return numbers


def released(entries: Sequence[Entry], rows: dict[str, dict]) -> set[str]:
    """Every open row a `release` line names, by number or by its lane."""
    freed = "\n".join(entry.freed for entry in entries)
    freed_lanes = set(HELD_LANE.findall(freed))
    named = set(HELD_PR.findall(freed))
    return {pr for pr, row in rows.items() if row.get("state", "open") == "open" and (pr in named or row.get("lane") in freed_lanes)}


def rowless(entries: Sequence[Entry], rows: dict[str, dict]) -> dict[str, list[str]]:
    """Each lane line naming a lane no row carries in any state, with those lanes: such a line holds nothing, now or once a PR opens under another lane name."""
    carried = {row.get("lane") for row in rows.values()}
    return {entry.line: missing for entry in entries if (missing := [lane for lane in entry.lanes if lane not in carried])}


def write(path: Path, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_text("".join(f"{entry.line}\n" for entry in entries))
    staged.replace(path)


def check_reason(reason: str, pr: str | None) -> str:
    reason = " ".join(reason.split())
    if not reason:
        raise Refused("a hold needs a reason")
    if HELD_LANE.search(reason):
        raise Refused(f"the reason `{reason}` names `lane:<name>`, which the runner reads as another hold; name the lane without the `lane:`")
    if pr and SHOULD_LAND.search(reason):
        raise Refused(f"the reason `{reason}` says #{pr} should land, but a `#{pr}` line holds it; run `holds.py rm --pr {pr}` to let it land")
    return reason


def target(args: argparse.Namespace) -> tuple[str, str]:
    if getattr(args, "all", False):
        return "all", ""
    if args.pr is not None:
        if not args.pr.lstrip("#").isdigit():
            raise Refused(f"--pr takes a PR number, not `{args.pr}`")
        return "pr", args.pr.lstrip("#")
    if not LANE_NAME.match(args.lane):
        raise Refused(f"--lane takes one lane name, not `{args.lane}`")
    return "lane", args.lane


def subject(kind: str, name: str) -> str:
    return {"pr": f"#{name}", "lane": f"lane:{name}", "all": "hold:all"}[kind]


def add(path: Path, args: argparse.Namespace) -> str:
    kind, name = target(args)
    reason = check_reason(args.reason, name if kind == "pr" else None)
    entries = read(path)
    if existing := next((entry for entry in entries if (entry.holds_all if kind == "all" else name in (entry.prs if kind == "pr" else entry.lanes))), None):
        raise Refused(f"`{existing.line}` already {existing.describe()}; `rm` it before adding another reason")
    line = f"{subject(kind, name)} {reason}"
    write(path, [*entries, Entry(line)])
    return f"added `{line}` to {path}"


def releases(entry: Entry, kind: str, name: str) -> bool:
    return entry.release and entry.freed.split()[:1] == [subject(kind, name)]


def release(path: Path, args: argparse.Namespace) -> str:
    kind, name = target(args)
    reason = check_reason(args.reason, None)
    if PR_REF.search(reason):
        raise Refused(f"the reason `{reason}` names a PR, which a `release` line reads as another release; name it without the `#`")
    entries = read(path)
    if existing := next((entry for entry in entries if releases(entry, kind, name)), None):
        raise Refused(f"`{existing.line}` already {existing.describe()}; `rm --release` it before adding another reason")
    line = f"release {subject(kind, name)} {reason}"
    write(path, [*entries, Entry(line)])
    return f"added `{line}` to {path}"


def without(entry: Entry, kind: str, name: str) -> Entry | None:
    token = re.compile(rf"^#{name}\b" if kind == "pr" else rf"\blane:{re.escape(name)}(?=\s|$)")
    remaining = Entry(" ".join(token.sub("", entry.line).split()))
    kept = [*(entry.prs if kind == "lane" else []), *(lane for lane in entry.lanes if kind == "pr" or lane != name)]
    if not kept and not entry.holds_all:
        return None
    if remaining.release or remaining.unknown or remaining.prs + remaining.lanes != kept:
        raise Refused(f"removing {token.pattern} from `{entry.line}` would leave `{remaining.line}`, which {remaining.describe()}; split that line by hand")
    return remaining


def rm(path: Path, args: argparse.Namespace) -> str:
    kind, name = target(args)
    entries = read(path)
    if kind == "all" or args.release:
        dropped = [entry for entry in entries if (releases(entry, kind, name) if args.release else entry.holds_all)]
        if not dropped:
            raise Refused(f"no line in {path} {'releases ' if args.release else 'is '}{subject(kind, name)}")
        write(path, [entry for entry in entries if entry not in dropped])
        return f"removed {len(dropped)} line(s) of {path}"
    holding = [entry for entry in entries if name in (entry.prs if kind == "pr" else entry.lanes)]
    if not holding:
        raise Refused(f"no line in {path} holds {'#' + name if kind == 'pr' else 'lane:' + name}")
    kept = [
        remaining
        for entry in entries
        if (remaining := without(entry, kind, name) if entry in holding else entry) is not None
    ]
    write(path, kept)
    return f"removed {'#' + name if kind == 'pr' else 'lane:' + name} from {len(holding)} line(s) of {path}"


def listing(path: Path, _: argparse.Namespace) -> str:
    entries = [entry for entry in read(path) if entry.line.strip()]
    return "\n".join(f"{entry.line}\t{entry.describe()}" for entry in entries) or f"{path} holds nothing"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="holds.py", description=__doc__.split("\n", 1)[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--file")
    where.add_argument("--config")
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("add", "rm", "release"):
        sub = verbs.add_parser(verb)
        which = sub.add_mutually_exclusive_group(required=True)
        which.add_argument("--pr")
        which.add_argument("--lane")
        if verb != "release":
            which.add_argument("--all", action="store_true")
        if verb == "rm":
            sub.add_argument("--release", action="store_true")
        else:
            sub.add_argument("--reason", required=True)
    verbs.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print({"add": add, "rm": rm, "release": release, "list": listing}[args.verb](holds_path(args), args))
    except Refused as refusal:
        print(f"holds.py: refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
