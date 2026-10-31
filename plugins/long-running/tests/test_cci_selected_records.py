import json

import pytest

from long_running import cci


def test_selected_records_complete_statuses_missing_and_order():
    calls = []
    rows = [{"seq": seq, "status": status, "kind": kind} for seq, status, kind in [(41, "closed", "blocker"), (43, "imported", "note"), (44, "open", "blocker")]]

    def run(argv):
        calls.append(argv)
        return "\n".join(map(json.dumps, reversed(rows)))

    assert cci.selected_records(run, "d1", [44, 42, 41, 43, 41]) == rows
    assert calls == [["cci", "tail", "--drive", "d1", "--since-seq", "0", "--seqs", "41,42,43,44", "--json", "--budget", "0"]]


def test_selected_records_bounded_batches_and_empty():
    calls = []

    def run(argv):
        seqs = list(map(int, argv[argv.index("--seqs") + 1].split(",")))
        calls.append(seqs)
        return "\n".join(json.dumps({"seq": seq}) for seq in seqs)

    assert cci.selected_records(run, "d1", []) == []
    assert calls == []
    assert len(cci.selected_records(run, "d1", list(range(1, 502)))) == 501
    assert list(map(len, calls)) == [500, 1]


def test_selected_records_refreshes_within_tail_pass():
    status = "open"
    calls = []

    def run(argv):
        calls.append(argv)
        return json.dumps({"seq": 41, "status": status})

    tail = cci.Tail(run)
    tail.remember()
    assert cci.selected_records(tail, "d1", [41])[0]["status"] == "open"
    status = "closed"
    assert cci.selected_records(tail, "d1", [41])[0]["status"] == "closed"
    assert len(calls) == 2
    assert not tail.kept and not tail.read


@pytest.mark.parametrize("output", ['{"seq":42}', '{"seq":41}\n{"seq":41}'])
def test_selected_records_rejects_unrequested_or_duplicate_rows(output):
    with pytest.raises(ValueError, match="unexpected selected cci sequence"):
        cci.selected_records(lambda argv: output, "d1", [41])


def test_selected_records_preserves_native_failure():
    failure = RuntimeError("native unavailable")

    def run(argv):
        raise failure

    with pytest.raises(RuntimeError) as caught:
        cci.selected_records(run, "d1", [41])
    assert caught.value is failure
