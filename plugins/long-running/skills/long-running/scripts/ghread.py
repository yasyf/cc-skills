"""Build the argv of a GitHub read through ccx's gateway, which serves an identical read from one machine-wide cache, refuses a read under the quota floor, and charges each read to the script that made it."""

from __future__ import annotations

CACHED = "5m"
FRESH = "0s"
GATEWAY = ["ccx", "vcs", "gh"]


def read(*args: str, max_age: str = CACHED) -> list[str]:
    return [*GATEWAY, "--max-age", max_age, "--", *args]


def gh_args(argv: list[str]) -> list[str]:
    """The plain `gh` command a gateway read runs, so a message names what was read rather than the gateway."""
    return ["gh", *argv[argv.index("--") + 1 :]] if argv[:3] == GATEWAY else argv
