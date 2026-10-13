#!/usr/bin/env python3
"""Edit the root's landing holds file, the one desk-runner.py reads before every enqueue.

`add --pr <n> --reason R` writes `#<n> R`, `add --lane <name> --reason R` writes
`lane:<name> R`, `rm` deletes that entry, and `list` prints every line with what it holds.
A line holds the one PR its leading `#<n>` names; every later `#<n>` is prose. A reason
never carries `lane:<name>`, and a PR line whose reason says the PR should land is refused:
remove its hold instead. Name the file with `--file`, or with `--config` for a desk-runner config.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HELD_PR = re.compile(r"^#(\d+)", re.MULTILINE)
HELD_LANE = re.compile(r"\blane:(\S+)")
HOLD_ALL = re.compile(r"^hold:all\s+\S", re.MULTILINE)
RELEASE_LINE = re.compile(r"^release\s+(.*)$", re.MULTILINE)
LANE_NAME = re.compile(r"^[\w.-]+$")
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
        return [] if self.release else HELD_LANE.findall(self.line)

    @property
    def holds_all(self) -> bool:
        return bool(HOLD_ALL.match(self.line))

    def describe(self) -> str:
        if self.release:
            return f"releases {self.line.split(None, 1)[1]}"
        held = [*(f"#{pr}" for pr in self.prs), *(f"lane:{lane}" for lane in self.lanes)]
        if self.holds_all:
            held.insert(0, "every open row")
        return f"holds {', '.join(held)}" if held else "holds nothing"


def holds_path(args: argparse.Namespace) -> Path:
    if args.file:
        return Path(args.file).expanduser()
    return Path(json.loads(Path(args.config).expanduser().read_text())["landing"]["holds"]).expanduser()


def read(path: Path) -> list[Entry]:
    return [Entry(line) for line in path.read_text().splitlines()] if path.is_file() else []


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
    if args.pr is not None:
        if not args.pr.lstrip("#").isdigit():
            raise Refused(f"--pr takes a PR number, not `{args.pr}`")
        return "pr", args.pr.lstrip("#")
    if not LANE_NAME.match(args.lane):
        raise Refused(f"--lane takes one lane name, not `{args.lane}`")
    return "lane", args.lane


def add(path: Path, args: argparse.Namespace) -> str:
    kind, name = target(args)
    reason = check_reason(args.reason, name if kind == "pr" else None)
    entries = read(path)
    if existing := next((entry for entry in entries if name in (entry.prs if kind == "pr" else entry.lanes)), None):
        raise Refused(f"`{existing.line}` already {existing.describe()}; `rm` it before adding another reason")
    line = f"#{name} {reason}" if kind == "pr" else f"lane:{name} {reason}"
    write(path, [*entries, Entry(line)])
    return f"added `{line}` to {path}"


def without(entry: Entry, kind: str, name: str) -> Entry | None:
    if kind == "pr" and not entry.lanes:
        return None
    token = re.compile(rf"^#{name}\b" if kind == "pr" else rf"\blane:{re.escape(name)}(?=\s|$)")
    remaining = Entry(" ".join(token.sub("", entry.line).split()))
    if remaining.release:
        raise Refused(f"removing {token.pattern} from `{entry.line}` would turn it into a release line; split that line by hand")
    return remaining if remaining.prs or remaining.lanes or remaining.holds_all else None


def rm(path: Path, args: argparse.Namespace) -> str:
    kind, name = target(args)
    entries = read(path)
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
    for verb in ("add", "rm"):
        sub = verbs.add_parser(verb)
        which = sub.add_mutually_exclusive_group(required=True)
        which.add_argument("--pr")
        which.add_argument("--lane")
        if verb == "add":
            sub.add_argument("--reason", required=True)
    verbs.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print({"add": add, "rm": rm, "list": listing}[args.verb](holds_path(args), args))
    except Refused as refusal:
        print(f"holds.py: refused: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
