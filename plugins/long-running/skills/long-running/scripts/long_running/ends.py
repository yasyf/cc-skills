"""The condition that ends something a person waits on: a card on the owner's board, a retro row's due time.

Owner, 12:03 AM Nov 2: a board card about a release that happened 12 hours ago is "way out of date"; 10:26 PM Nov 1: "make sure it wont happen again". Every waiting item names, when it is made, the event after which it is moot, and one function reads that event for every writer.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

FORMS = "at:TIME (an ISO 8601 time or a span such as 90m, 4h or 1d), pr:N or pr:OWNER/REPO#N, release:STACK@ENV:COMMIT, or record:SEQ"
SPAN = re.compile(r"(?P<count>\d+)(?P<unit>[mhd])")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
PR = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<number>\d+)")
RELEASE = re.compile(r"(?P<stack>[\w.-]+)@(?P<env>[\w.-]+):(?P<commit>[0-9a-f]{7,40})")
RECORD = re.compile(r"#?(?P<seq>\d+)")
LIVE = re.compile(
    r"^(?P<stack>\S+) \([^)]*\) on (?P<env>\S+): .* at (?P<commit>[0-9a-f]{7,40}),"
)
RESOLVED = re.compile(r"\[(?:RESOLVED|ANSWERED) #\d+")
DEFAULT_REPO = "Forge-AI/monorepo"


class Shell(Protocol):
    def run(
        self, argv: list[str], stdin: str | None = None, cwd: Path | None = None
    ) -> str: ...


@dataclass(frozen=True)
class End:
    kind: str
    value: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


def at_time(text: str, moment: datetime) -> str:
    if span := SPAN.fullmatch(text):
        return stamp(moment + timedelta(**{UNITS[span["unit"]]: int(span["count"])}))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{text!r} is neither an ISO 8601 time nor a span such as 90m, 4h or 1d"
        ) from None
    if parsed.tzinfo is None:
        raise ValueError(f"{text!r} has no time zone")
    return stamp(parsed.astimezone(UTC))


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str, moment: datetime) -> End:
    kind, colon, value = text.strip().partition(":")
    if not colon or not value:
        raise ValueError(f"{text!r} names no end condition; give {FORMS}")
    match kind:
        case "at":
            return End("at", at_time(value, moment))
        case "pr" if found := PR.fullmatch(value):
            return End("pr", f"{found['repo'] or DEFAULT_REPO}#{found['number']}")
        case "release" if RELEASE.fullmatch(value):
            return End("release", value)
        case "record" if found := RECORD.fullmatch(value):
            return End("record", found["seq"])
    raise ValueError(f"{text!r} is not an end condition; give {FORMS}")


def refusal(
    text: str | None,
    moment: datetime,
    kinds: tuple[str, ...] = ("at", "pr", "release", "record"),
) -> str | None:
    if not text:
        return f"no end condition; give {FORMS}"
    try:
        end = parse(text, moment)
    except ValueError as error:
        return str(error)
    if end.kind not in kinds:
        return f"{text!r} is a {end.kind} condition; this needs {' or '.join(kinds)}"
    if end.kind == "at" and end.value <= stamp(moment):
        return f"{text!r} has already passed"
    return None


@dataclass(frozen=True)
class Facts:
    shell: Shell
    checkout: Path
    prs: dict[str, str]
    live: list[str]

    @classmethod
    def read(cls, waiting: list[End], shell: Shell, checkout: Path) -> Facts:
        repos: dict[str, list[str]] = {}
        for end in waiting:
            if end.kind == "pr":
                repo, number = end.value.split("#")
                repos.setdefault(repo, []).append(number)
        prs = {
            f"{repo}#{row['number']}": row["state"]
            for repo, numbers in repos.items()
            for row in json.loads(shell.run(["ccx", "vcs", "pr", "status", "--json", "-R", repo, *numbers], cwd=checkout))
        }
        live = shell.run(["tools/ci", "release", "live"], cwd=checkout).splitlines() if any(end.kind == "release" for end in waiting) else []
        return cls(shell, checkout, prs, live)

    def reached(self, end: End, moment: datetime) -> str | None:
        match end.kind:
            case "at":
                return f"its time {end.value} passed" if end.value <= stamp(moment) else None
            case "pr":
                state = self.prs[end.value]
                return None if state == "OPEN" else f"{end.value} is {state.lower()}"
            case "release":
                return self.released(RELEASE.fullmatch(end.value))
            case "record":
                shown = self.shell.run(["cci", "show", end.value])
                line = next((line for line in shown.splitlines() if not line.startswith("@")), "")
                return f"record #{end.value} is closed" if RESOLVED.search(line) else None
        raise AssertionError(end)

    def released(self, want: re.Match[str]) -> str | None:
        for line in self.live:
            if (live := LIVE.match(line)) and (live["stack"], live["env"]) == (want["stack"], want["env"]) and carries(self.shell, self.checkout, want["commit"], live["commit"]):
                return f"{want['stack']} on {want['env']} runs {live['commit']}, which carries {want['commit']}"
        return None


def carries(shell: Shell, checkout: Path, commit: str, live: str) -> bool:
    try:
        shell.run(
            ["git", "-C", str(checkout), "merge-base", "--is-ancestor", commit, live]
        )
    except subprocess.CalledProcessError:
        return False
    return True
