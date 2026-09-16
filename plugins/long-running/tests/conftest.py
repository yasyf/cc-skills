"""A fake :class:`ledger.Shell` routing every gh/bk/ccn call to a recorded fixture.

Zero network and zero ``ccn``: the ledger store is a dict the fake mutates the way
``ccn ledger sync`` and ``ccn ledger row set`` would, so merge and prune semantics are
exercised rather than mocked away. Every argv is recorded on ``shell.calls``.
"""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

import ledger

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MOVED_HEAD = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
LISTED_HEAD = "e8ad88696bf85732fc0bec48b8486977b94b6f1b"
DIRTY_HEAD = "ab0de038c1100e5c66e9ad3b21b1b8bb1b1ad0bb"
GREEN_HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
LEDGER = "1a2b3c4d"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeShell(ledger.Shell):
    def __init__(self, rows=None, routes=None, comments=None, pages=None):
        self.store = {"id": LEDGER, "title": "open PRs", "columns": [], "rows": deepcopy(list(rows or []))}
        self.routes = dict(routes or {})
        self.comments = {key: list(value) for key, value in (comments or {}).items()}
        self.pages = pages or {1: "pulls-page-1.json", 2: "pulls-page-2.json"}
        self.fail_gh: str | None = None
        self.calls: list[list[str]] = []
        self.posted: list[tuple[str, str]] = []

    def run(self, argv, stdin=None):
        self.calls.append(list(argv))
        if argv[0] == "gh":
            return self._gh(argv[2], stdin)
        if argv[0] == "bk":
            return self._bk(argv)
        if argv[0] == "ccn":
            return self._ccn(argv, stdin)
        raise AssertionError(f"unexpected command: {argv}")

    def _gh(self, endpoint, stdin):
        if self.fail_gh and self.fail_gh in endpoint:
            raise subprocess.CalledProcessError(1, ["gh", "api", endpoint], stderr="gh: connection refused")
        path, _, query = endpoint.partition("?")
        params = dict(pair.split("=", 1) for pair in query.split("&") if pair)
        parts = path.split("/")[3:]
        if parts == ["pulls"]:
            return fixture(self.pages.get(int(params["page"]), "empty-list.json"))
        if parts[:1] == ["pulls"] and len(parts) == 2:
            detail = FIXTURES / f"pull-{parts[1]}.json"
            return detail.read_text() if detail.exists() else json.dumps(self._listed(parts[1]))
        if parts[:1] == ["issues"] and parts[2:] == ["labels"]:
            return fixture("labels.json")
        if parts[:1] == ["issues"] and parts[2:] == ["comments"]:
            if stdin is not None:
                self.posted.append((parts[1], json.loads(stdin)["body"]))
                return "{}"
            page = int(params.get("page", 1))
            return json.dumps(
                [{"body": body} for body in self.comments.get(parts[1], [])] if page == 1 else []
            )
        if parts[:1] == ["commits"] and parts[2:] == ["status"]:
            return fixture(self.routes.get(f"status:{parts[1]}", "status-success.json"))
        if parts[:1] == ["commits"] and parts[2:] == ["check-runs"]:
            return fixture(self.routes.get(f"checks:{parts[1]}", "check-runs.json"))
        raise AssertionError(f"unexpected gh endpoint: {endpoint}")

    def _listed(self, number: str) -> dict:
        for name in self.pages.values():
            for entry in json.loads(fixture(name)):
                if str(entry["number"]) == number:
                    return entry
        raise AssertionError(f"no listed PR {number}")

    def _bk(self, argv):
        if argv[1:3] == ["build", "view"]:
            return fixture("build-view.json")
        if argv[1:3] == ["job", "log"]:
            return fixture(self.routes.get(f"log:{argv[-1]}", "job-log.txt"))
        raise AssertionError(f"unexpected bk call: {argv}")

    def _ccn(self, argv, stdin):
        if argv[1:3] == ["ledger", "show"]:
            return json.dumps(self.store)
        if argv[1:3] == ["ledger", "sync"]:
            self._sync(json.loads(stdin), prune="--prune" in argv)
            return ""
        if argv[1:4] == ["ledger", "row", "set"]:
            key = argv[argv.index("--key") + 1]
            updates = dict(
                argv[index + 1].split("=", 1) for index, value in enumerate(argv) if value == "--field"
            )
            self.row(key)["fields"].update(updates)
            return ""
        raise AssertionError(f"unexpected ccn call: {argv}")

    def _sync(self, incoming, prune):
        keys = {entry["key"] for entry in incoming}
        for entry in incoming:
            existing = next((row for row in self.store["rows"] if row["key"] == entry["key"]), None)
            if existing is None:
                self.store["rows"].append({"key": entry["key"], "fields": dict(entry["fields"])})
            else:
                existing["fields"].update(entry["fields"])
        if prune:
            self.store["rows"] = [row for row in self.store["rows"] if row["key"] in keys]

    def row(self, key: str) -> dict:
        return next(row for row in self.store["rows"] if row["key"] == key)

    def fields(self, key: str) -> dict:
        return self.row(key)["fields"]

    def endpoints(self) -> list[str]:
        return [argv[2] for argv in self.calls if argv[0] == "gh"]


@pytest.fixture
def lock(tmp_path) -> Path:
    return tmp_path / "ledger.lock"


@pytest.fixture
def red_routes() -> dict[str, str]:
    return {f"status:{MOVED_HEAD}": "status-failure.json"}
