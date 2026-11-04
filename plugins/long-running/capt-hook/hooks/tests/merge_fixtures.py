from __future__ import annotations

from pathlib import Path

LR = Path(__file__).parents[3] / "bin" / "lr"
OWN = "OWN-RED #5 lr-go lr/internal/lanes/sources.go:336 Consider preallocating messages with capacity 1 (prealloc)\nINHERITED #4 lr-go\n"
INHERITED = "INHERITED #5 lr-go\n"
BASE_UNREAD = "INHERITED #5 lr-go (base branch state unread)\n"
PENDING = ""
UNREAD = "UNREAD #360037 meta-desk-2 relay: hold #2188, do not merge\nMAIL #360040 root note: dev is green\n"
BROADCAST = "MAIL #360040 root note: dev is green\n"


def own_red(*args: str, said: str) -> dict[str, str]:
    return {" ".join((str(LR), "own-red", *args)): said}
