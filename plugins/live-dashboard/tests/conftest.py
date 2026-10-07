from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
from livedash import registry
from livedash.context import Context

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc)
FACTS = {"id": "test", "title": "Test drive", "repo": "o/r", "checkout": "/checkout", "ledger": "L1", "cci_drive": "test-drive", "started_at": "2026-10-06T00:00:00Z", "packs": {}}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@dataclass
class Fake(Context):
    replies: dict = field(default_factory=dict)
    cci_replies: dict = field(default_factory=dict)
    graphql: dict | None = None
    calls: list = field(default_factory=list)

    def run(self, argv, timeout=None, cwd=None, input=None) -> str:
        self.calls.append(list(argv))
        for prefix, reply in self.replies.items():
            if tuple(argv[: len(prefix)]) == prefix:
                reply = reply(argv) if callable(reply) else reply
                return reply if isinstance(reply, str) else json.dumps(reply)
        raise AssertionError(f"unexpected command {argv}")

    def cci(self, path, **params):
        self.calls.append([path, params])
        reply = self.cci_replies[path]
        return reply(params) if callable(reply) else reply

    def gh_graphql(self, query, **variables) -> dict:
        self.calls.append(["graphql", query])
        return self.graphql


def fake(directory: Path, facts: dict | None = None, **kwargs) -> Fake:
    return Fake(directory, FACTS | (facts or {}), NOW, 30.0, **kwargs)


LEDGER = ("ccn", "-R", "/checkout", "ledger", "row", "list")
PR_STATUS = ("ccx", "vcs", "pr", "status")


@pytest.fixture(scope="session", autouse=True)
def builtins():
    registry.builtins()
