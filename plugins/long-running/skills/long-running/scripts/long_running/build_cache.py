import hashlib
from datetime import datetime, timezone
from pathlib import Path

from . import actions
from .book import Book


_JOB_FIELDS = ("type", "name", "step_key", "state", "soft_failed")


def _ordered_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class BuildCache:
    """Passed build summaries indexed by their exact URL within one runner configuration."""

    def __init__(self, book: Book, config: Path, owner: str):
        self.book = book
        self.container = "build-cache-" + hashlib.sha256(str(config.absolute()).encode()).hexdigest()
        self.book.ensure(self.container, owner)

    def get(self, url: str) -> dict | None:
        return self.book.fact_path(self.container, ("builds", url, "build"), None)

    def put(self, builds: dict[str, dict], floor: datetime) -> None:
        passed = {url: {key: build[key] for key in ("state", "commit", "finished_at") if key in build} | {"jobs": [{key: job[key] for key in _JOB_FIELDS if key in job} for job in build.get("jobs", [])]} for url, build in builds.items() if build.get("state") == "passed" and build.get("finished_at") and actions.parse_stamp(build["finished_at"]) >= floor}
        if not passed:
            return

        def change(incident):
            entries = incident.facts.setdefault("builds", {})
            for url in incident.fact_keys_before("builds", "finished", _ordered_time(floor)):
                del entries[url]
            for url, build in passed.items():
                entries[url] = {"finished": _ordered_time(actions.parse_stamp(build["finished_at"])), "build": build}

        self.book.edit(self.container, change)
