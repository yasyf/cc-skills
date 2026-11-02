"""The lane supervisor's worker registry as every desk reads it.

One `drive_supervisor.py lanes --json` answer holds a row per worker. A sender takes from it
where a lane's mail goes, `route`, and a reader takes which names are one worker's line of
successors, `lineage`, from the rows' own successor links. A numbered name such as
`fidelity-desk-13` still joins `fidelity-desk-12`'s line until every launch names the
worker it succeeds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

ROOT = "root"
NUMBER = re.compile(r"(?:-\d{1,2})+$")


@dataclass(frozen=True)
class Registry:
    rows: dict[str, dict]

    @classmethod
    def parse(cls, out: str) -> Registry:
        return cls({row["lane"]: row for line in out.splitlines() if line.strip() and (row := json.loads(line))})

    def live(self, lane: str) -> bool:
        return bool((self.rows.get(lane) or {}).get("live"))

    def retired(self, lane: str) -> bool:
        return (self.rows.get(lane) or {}).get("state") == "retired"

    def route(self, lane: str) -> str:
        """Where mail for `lane` goes: the end of its successor chain, root when that end retired with no successor, and `lane` itself when no row names it."""
        return (self.rows.get(lane) or {}).get("route") or lane

    def lineage(self, lane: str) -> set[str]:
        """`lane` with every worker it succeeded or that succeeded it."""
        links: dict[str, set[str]] = {}
        for name, row in self.rows.items():
            for other in (row.get("predecessor"), row.get("retired_by")):
                if other and other != ROOT and other in self.rows:
                    links.setdefault(name, set()).add(other)
                    links.setdefault(other, set()).add(name)
        family = re.compile(rf"{re.escape(NUMBER.sub('', lane))}(?:-\d{{1,2}})*")
        seen, todo = {lane}, [lane, *(name for name in self.rows if family.fullmatch(name))]
        while todo:
            name = todo.pop()
            seen.add(name)
            todo += [other for other in links.get(name, ()) if other not in seen]
        return seen
