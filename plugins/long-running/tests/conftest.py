"""A fake :class:`ledger.Shell` routing every gh/bk/git/ccn call to a recorded fixture.

Zero network and zero ``ccn``: each ledger store is a dict the fake mutates the way
``ccn ledger sync`` and ``row set`` would, so merge and insert semantics are exercised
rather than mocked away; ``git`` answers from the fetch, log, and conflict tables a test
sets; the repository's PR list is not served at all, so a listing call fails the test
that makes it. Every argv is recorded on ``shell.calls``.
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
    def __init__(self, rows=None, routes=None, pages=None):
        self.stores = {LEDGER: {"id": LEDGER, "title": "open PRs", "columns": [], "rows": deepcopy(list(rows or []))}}
        self.pulls: dict[str, dict] = {}
        self.commit_dates: dict[str, str] = {}
        self.pull_heads: dict[str, str] = {}
        self.pr_files: dict[str, list[str]] = {}
        self.reviews: dict[str, list[dict]] = {}
        self.delivered: dict[str, tuple[str, str]] = {}
        self.diffed_head = ""
        self.closed_by: dict[str, str] = {}
        self.shallow = False
        self.fetch_fails = ""
        self.base_squash = ""
        self.children: list[dict] = []
        self.ejected: dict[str, tuple[str, str]] = {}
        self.pr_labels: dict[str, list[str]] = {}
        self.conflicts: dict[str, list[str]] = {}
        self.labelled: list[str] = []
        self.unlabelled: list[str] = []
        self.fetched = ""
        self.routes = dict(routes or {})
        self.pages = pages or {1: "pulls-page-1.json", 2: "pulls-page-2.json"}
        self.fail_gh: str | None = None
        self.calls: list[list[str]] = []

    def run(self, argv, stdin=None):
        self.calls.append(list(argv))
        if argv[0] == "gh":
            return self._gh(argv, stdin)
        if argv[0] == "bk":
            return self._bk(argv)
        if argv[0] == "ccn":
            return self._ccn(argv, stdin)
        if argv[0] == "git":
            return self._git(argv)
        raise AssertionError(f"unexpected command: {argv}")

    @property
    def store(self) -> dict:
        return self.stores[LEDGER]

    def _gh(self, argv, stdin):
        endpoint = argv[2]
        if self.fail_gh and self.fail_gh in endpoint:
            raise subprocess.CalledProcessError(1, ["gh", "api", endpoint], stderr="gh: connection refused")
        path, _, query = endpoint.partition("?")
        params = dict(pair.split("=", 1) for pair in query.split("&") if pair)
        parts = path.split("/")[3:]
        if parts[:1] == ["pulls"] and len(parts) == 2:
            if parts[1] in self.pulls:
                return json.dumps(self.pulls[parts[1]])
            detail = FIXTURES / f"pull-{parts[1]}.json"
            return detail.read_text() if detail.exists() else json.dumps(self._listed(parts[1]))
        if parts[:1] == ["issues"] and parts[2:] == ["labels"] and "POST" in argv:
            self.labelled.extend(f"{parts[1]}:{name}" for name in json.loads(stdin)["labels"])
            return "[]"
        if parts[:1] == ["issues"] and parts[2:3] == ["labels"] and "DELETE" in argv:
            self.unlabelled.append(f"{parts[1]}:{parts[3]}")
            return "[]"
        if parts[:1] == ["issues"] and parts[2:] == ["labels"]:
            if parts[1] in self.pr_labels:
                return json.dumps([{"name": name} for name in self.pr_labels[parts[1]]])
            return fixture("labels.json")
        if parts[:1] == ["issues"] and parts[2:] == ["events"]:
            events = []
            if parts[1] in self.ejected:
                at, ej = self.ejected[parts[1]]
                events.append({"event": "labeled", "created_at": at, "label": {"name": "merge"}, "actor": {"login": "yasyf"}})
                events.append({"event": "unlabeled", "created_at": ej, "label": {"name": "merge"}, "actor": {"login": "graphite-app[bot]"}})
            login = self.closed_by.get(parts[1])
            if login:
                events.append({"event": "closed", "created_at": "2026-09-17T00:00:00Z", "actor": {"login": login}})
            return json.dumps(events)
        if parts[:1] == ["pulls"] and len(parts) == 1 and "base=" in endpoint:
            return json.dumps(self.children)
        if parts[:1] == ["pulls"] and parts[2:] == ["reviews"]:
            size, page = int(params["per_page"]), int(params["page"])
            return json.dumps(self.reviews.get(parts[1], [])[(page - 1) * size : page * size])
        if parts[:1] == ["pulls"] and parts[2:] == ["files"]:
            return json.dumps([{"filename": name} for name in self.pr_files.get(parts[1], [])])
        if parts[:1] == ["commits"] and len(parts) == 2:
            return json.dumps({"sha": parts[1], "commit": {"committer": {"date": self.commit_dates[parts[1]]}}})
        if parts[:1] == ["commits"] and parts[2:] == ["status"]:
            return fixture(self.routes.get(f"status:{parts[1]}", "status-success.json"))
        if parts[:1] == ["commits"] and parts[2:] == ["check-runs"]:
            checks = json.loads(fixture(self.routes.get(f"checks:{parts[1]}", "check-runs.json")))
            if "check_name" in params:
                checks["check_runs"] = [run for run in checks["check_runs"] if run["name"] == params["check_name"]]
            return json.dumps(checks)
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
        if argv[1:3] == ["ledger", "add"]:
            ledger = f"{len(self.stores) + 1:040x}"
            self.stores[ledger] = {"id": ledger, "title": argv[3], "columns": [], "rows": []}
            return json.dumps(self.stores[ledger])
        if argv[1:3] == ["ledger", "show"]:
            return json.dumps(self.stores[argv[3]])
        if argv[1:3] == ["ledger", "sync"]:
            self._sync(self.stores[argv[3]], json.loads(stdin), prune="--prune" in argv)
            return ""
        if argv[1:4] == ["ledger", "row", "set"]:
            key = argv[argv.index("--key") + 1]
            updates = dict(
                argv[index + 1].split("=", 1) for index, value in enumerate(argv) if value == "--field"
            )
            self._upsert(self.stores[argv[4]], key, updates)
            return ""
        raise AssertionError(f"unexpected ccn call: {argv}")

    def _git(self, argv):
        verb = argv[3]
        if verb == "fetch":
            if self.fetch_fails:
                raise subprocess.CalledProcessError(1, argv, stderr=self.fetch_fails)
            ref = argv[-1].lstrip("+").split(":")[0]
            self.fetched = self.pull_heads[ref.split("/")[2]] if ref.startswith("refs/pull/") else "base-tip"
            return ""
        if verb == "rev-parse" and "--is-shallow-repository" in argv:
            return ("true" if self.shallow else "false") + "\n"
        if verb == "rev-parse":
            return self.fetched + "\n"
        if verb == "merge-tree":
            paths = self.conflicts.get(argv[-1])
            if paths:
                raise subprocess.CalledProcessError(1, argv, output="tree\n" + "".join(f"100644 blob x\t{p}\n" for p in paths))
            return "tree\n"
        if verb == "diff" and "--numstat" in argv:
            left, right = self._resolve(argv[5]), self._resolve(argv[6])
            assert left != right, f"diffed {argv[5]} against {argv[6]}: both resolve to {left}"
            self.diffed_head = right
            if right in self.delivered:
                return ""
            return "".join(f"1\t0\t{path}\n" for path in argv[argv.index("--") + 1 :])
        if verb == "log" and "--grep" in " ".join(argv):
            return self.base_squash + ("\n" if self.base_squash else "")
        if verb == "log" and argv[5] == "-1":
            assert self._resolve(argv[4]) == "base-tip", f"named the landing commit from {argv[4]}"
            sha, when = self.delivered[self.diffed_head]
            return f"{sha} {when}\n"
        raise AssertionError(f"unexpected git call: {argv}")

    def _resolve(self, ref: str) -> str:
        if ref == "FETCH_HEAD":
            return self.fetched
        return "base-tip" if ref.startswith("refs/desk/base/") else ref

    @staticmethod
    def _upsert(store, key, updates):
        existing = next((row for row in store["rows"] if row["key"] == key), None)
        if existing is None:
            store["rows"].append({"key": key, "fields": dict(updates)})
        else:
            existing["fields"].update(updates)

    def _sync(self, store, incoming, prune):
        keys = {entry["key"] for entry in incoming}
        for entry in incoming:
            self._upsert(store, entry["key"], entry["fields"])
        if prune:
            store["rows"] = [row for row in store["rows"] if row["key"] in keys]

    def row(self, key: str, ledger: str = LEDGER) -> dict:
        return next(row for row in self.stores[ledger]["rows"] if row["key"] == key)

    def fields(self, key: str, ledger: str = LEDGER) -> dict:
        return self.row(key, ledger)["fields"]

    def keys(self, ledger: str = LEDGER) -> list[str]:
        return [row["key"] for row in self.stores[ledger]["rows"]]

    def endpoints(self) -> list[str]:
        return [argv[2] for argv in self.calls if argv[0] == "gh"]


@pytest.fixture
def lock(tmp_path) -> Path:
    return tmp_path / "ledger.lock"


@pytest.fixture
def red_routes() -> dict[str, str]:
    return {f"status:{MOVED_HEAD}": "status-failure.json"}
