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
    def __init__(self, records: list[dict] | None = None):
        self.calls: list[list[str]] = []
        self.records = records or []

    def run(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["cci", "post"]:
            return json.dumps({"seq": 41, "num": "#7@remote"}) + "\n"
        since, budget = int(argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1]), int(argv[argv.index("--budget") + 1])
        lines = [json.dumps(record) + "\n" for record in self.records if record["seq"] > since]
        fitting = next((count for count in range(len(lines), 0, -1) if sum(map(len, lines[:count])) <= budget), 0)
        return "".join(lines[:fitting])


def record(seq: int, **fields) -> dict:
    return {"seq": seq, "drive": "d1", "lane": "incident-x", "kind": "ask", "at": "2026-10-05T09:00:00Z", "text": "t", "to": ["comms"], "refs": {}, **fields}


def test_post_is_one_cci_post_and_prints_its_seq(capsys):
    shell = FakeShell()
    argv = ["post", "--bus", "d1", "--from", "incident-x", "--kind", "ask", "--topic", "x2", "--text", '{"event": "opened"}', "--to", "comms", "--repo", "/m"]
    assert bus.main(argv, shell) == 0
    assert shell.calls == [["cci", "post", "--drive", "d1", "--lane", "incident-x", "--kind", "ask", "--text", '{"event": "opened"}', "--topic", "x2", "--to", "comms", "--json"]]
    assert json.loads(capsys.readouterr().out) == {"seq": 41, "num": "#7@remote"}


def test_a_reply_carries_its_re(capsys):
    shell = FakeShell()
    assert bus.main(["post", "--bus", "d1", "--from", "comms", "--kind", "answer", "--re", "7", "--text", "posted ts=1.2"], shell) == 0
    assert shell.calls == [["cci", "post", "--drive", "d1", "--lane", "comms", "--kind", "answer", "--text", "posted ts=1.2", "--re-seq", "7", "--json"]]


def test_read_pages_every_delivery_into_one_json_list(capsys):
    shell = FakeShell([record(3), record(9, kind="answer", re=3, lane="comms", to=[]), record(12, topic="x2")])
    argv = ["read", "--bus", "d1", "--lane", "incident-x", "--kind", "answer", "--kind", "ask", "--all", "--peek", "--json", "--repo", "/m"]
    assert bus.main(argv, shell) == 0
    assert [call[call.index("--since-seq") + 1] for call in shell.calls] == ["0"]
    assert shell.calls[0][2:] == ["--drive", "d1", "--since-seq", "0", "--json", "--budget", "4000000", "--reader", "incident-x", "--kind=answer", "--kind=ask"]
    entries = json.loads(capsys.readouterr().out)
    assert [entry["seq"] for entry in entries] == [3, 9, 12]
    assert entries[1] == {"seq": 9, "at": "2026-10-05T09:00:00Z", "kind": "answer", "topic": "", "from": "comms", "to": [], "text": "t", "re": 3}
    assert entries[2]["topic"] == "x2" and entries[2]["re"] is None


def test_read_is_json_only():
    with pytest.raises(SystemExit):
        bus.main(["read", "--bus", "d1", "--lane", "x"], FakeShell())


def test_read_takes_a_whole_cci_page_of_records_in_one_call(capsys):
    shell = FakeShell([record(seq, text="t" * 300) for seq in range(1, 301)])
    assert bus.main(["read", "--bus", "d1", "--lane", "incident-x", "--all", "--peek", "--json", "--repo", "/m"], shell) == 0
    assert len(json.loads(capsys.readouterr().out)) == 300
    assert len(shell.calls) == 1


def test_posted_receipts_preserve_all_internal_ids_in_multi_record_json():
    assert bus.cci.posted('{"seq":41,"num":"#7"}\n{"seq":52,"num":"#100@remote"}\n') == (bus.cci.Posted(41, "#7"), bus.cci.Posted(52, "#100@remote"))


@pytest.mark.parametrize("value,expected", [("", None), ("#41", 41), ("#41 (duplicate)", 41)])
def test_book_receipt_decoder_preserves_existing_persisted_schema(value, expected):
    assert bus.cci.receipt_seq(value) == expected
    if expected is not None:
        assert bus.cci.receipt(expected) == "#41"


@pytest.mark.parametrize("value", ["#41@remote", "posted #41", "#41 extra", "41"])
def test_book_receipt_decoder_refuses_human_numbers_and_decorated_text(value):
    with pytest.raises(ValueError, match="invalid internal sequence receipt"):
        bus.cci.receipt_seq(value)


@pytest.mark.parametrize("since,expected", [(0, ["--since-seq", "0"]), (41, ["--since-seq", "41"]), ("41", ["--since-seq", "41"]), ("#41", ["--since-seq", "41"]), ("+41", ["--since-seq", "41"]), ("2h", ["--since", "2h"]), ("2026-10-30T22:00:00Z", ["--since", "2026-10-30T22:00:00Z"])])
def test_record_bounds_keep_internal_sequences_separate_from_times(since, expected):
    calls = []

    def run(argv):
        calls.append(argv)
        return ""

    assert bus.cci.records(run, "d1", since=since) == []
    assert calls[0][4:6] == expected
