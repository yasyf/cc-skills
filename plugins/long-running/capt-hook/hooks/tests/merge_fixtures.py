from __future__ import annotations

from pathlib import Path

LR = Path(__file__).parents[3] / "bin" / "lr"
OWN = "OWN-RED #5 lr-go lr/internal/lanes/sources.go:336 Consider preallocating messages with capacity 1 (prealloc)\nINHERITED #4 lr-go\n"
INHERITED = "INHERITED #5 lr-go\n"
PENDING = ""


def own_red(*args: str, said: str) -> dict[str, str]:
    return {" ".join((str(LR), "own-red", *args)): said}
