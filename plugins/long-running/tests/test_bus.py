from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/bus.py"
spec = importlib.util.spec_from_file_location("bus", SCRIPT)
bus = importlib.util.module_from_spec(spec)
sys.modules["bus"] = bus
spec.loader.exec_module(bus)


class FakeShell(bus.Shell):
    def __init__(self, records: list[dict] | None = None, page: int = 2):
        self.calls: list[list[str]] = []
        self.records = records or []
        self.page = page

    def run(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["cci", "post"]:
            return "#41\n"
        since = int(argv[argv.index("--since") + 1])
        fresh = [record for record in self.records if record["seq"] > since][: self.page]
        return "".join(json.dumps(record) + "\n" for record in fresh)


def record(seq: int, **fields) -> dict:
    return {"seq": seq, "drive": "d1", "lane": "incident-x", "kind": "ask", "at": "2026-10-05T09:00:00Z", "text": "t", "to": ["comms"], "refs": {}, **fields}


def test_post_is_one_cci_post_and_prints_its_seq(capsys):
    shell = FakeShell()
    argv = ["post", "--bus", "d1", "--from", "incident-x", "--kind", "ask", "--topic", "x2", "--text", '{"event": "opened"}', "--to", "comms", "--repo", "/m"]
    assert bus.main(argv, shell) == 0
    assert shell.calls == [["cci", "post", "--drive", "d1", "--lane", "incident-x", "--kind", "ask", "--text", '{"event": "opened"}', "--topic", "x2", "--to", "comms"]]
    assert capsys.readouterr().out == "#41\n"


def test_a_reply_carries_its_re(capsys):
    shell = FakeShell()
    assert bus.main(["post", "--bus", "d1", "--from", "comms", "--kind", "answer", "--re", "7", "--text", "posted ts=1.2"], shell) == 0
    assert shell.calls == [["cci", "post", "--drive", "d1", "--lane", "comms", "--kind", "answer", "--text", "posted ts=1.2", "--re", "7"]]


def test_read_pages_every_delivery_into_one_json_list(capsys):
    shell = FakeShell([record(3), record(9, kind="answer", re=3, lane="comms", to=[]), record(12, topic="x2")])
    argv = ["read", "--bus", "d1", "--lane", "incident-x", "--kind", "answer", "--kind", "ask", "--all", "--peek", "--json", "--repo", "/m"]
    assert bus.main(argv, shell) == 0
    assert [call[call.index("--since") + 1] for call in shell.calls] == ["0", "9", "12"]
    assert shell.calls[0][2:] == ["--drive", "d1", "--reader", "incident-x", "--since", "0", "--json", "--budget", "16000", "--kind=answer", "--kind=ask"]
    entries = json.loads(capsys.readouterr().out)
    assert [entry["seq"] for entry in entries] == [3, 9, 12]
    assert entries[1] == {"seq": 9, "at": "2026-10-05T09:00:00Z", "kind": "answer", "topic": "", "from": "comms", "to": [], "text": "t", "re": 3}
    assert entries[2]["topic"] == "x2" and entries[2]["re"] is None


def test_read_is_json_only():
    with pytest.raises(SystemExit):
        bus.main(["read", "--bus", "d1", "--lane", "x"], FakeShell())
