from __future__ import annotations

from pathlib import Path

LR = Path(__file__).parents[3] / "bin" / "lr"
OWN = "OWN-RED #5 lr-go https://github.com/acme/app/actions/runs/1/job/2\nINHERITED #4 lr-go\n"
INHERITED = "INHERITED #5 lr-go\n"
BASE_UNREAD = "BASE-UNREAD #5 lr-go\n"
PENDING = ""
UNREAD = "UNREAD #360037 meta-desk-2 relay: hold #2188, do not merge\nMAIL #360040 root note: dev is green\n"
BROADCAST = "MAIL #360040 root note: dev is green\n"


def own_red(*args: str, said: str) -> dict[str, str]:
    return {" ".join((str(LR), "own-red", *args)): said}
