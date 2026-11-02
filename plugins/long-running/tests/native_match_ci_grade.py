from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

root = Path(sys.argv[1])


def cases(name):
    return list(ET.parse(root / name).iter("testcase"))


def clean(name, count=None):
    rows = cases(name)
    assert rows and (count is None or len(rows) == count), (name, len(rows))
    assert not any(row.find(tag) is not None for row in rows for tag in ("failure", "error", "skipped")), name
    return rows


clean("immutable.xml", 19)
current = clean("current.xml", 19)
assert sum(row.attrib["name"].startswith("test_actual_match_warm_body_work") for row in current) == 2
assert sum(row.attrib["name"].startswith("test_actual_match_source_policy") for row in current) == 17
transport = clean("transport-and-policy.xml")
assert sum(row.attrib["name"].startswith("test_session_") or "test_match_session" in row.attrib.get("classname", "") for row in transport) == 17
for count in (32, 10000):
    rows = cases(f"old-{count}.xml")
    assert len(rows) == 1 and rows[0].find("failure") is not None
    assert rows[0].find("error") is rows[0].find("skipped") is None
    failure = rows[0].find("failure")
    assert "unchanged match still materializes answer or mirror bodies" in failure.attrib["message"]
    records = [json.loads(line.lstrip(".").removeprefix("NATIVE_PYTHON_MATCH_WORK ")) for line in (root / f"old-{count}.log").read_text().splitlines() if line.lstrip(".").startswith("NATIVE_PYTHON_MATCH_WORK ")]
    assert len(records) == 1 and records[0]["count"] == count and records[0]["driver"] == "legacy"
    assert records[0]["cold"]["words"] and records[0]["warm"]["words"]
    assert records[0]["warm"]["calls"]["answer_rows"] >= count
    assert records[0]["warm"]["mirror"]["reads"] >= records[0]["setup"]["short_id_members"]
records = [json.loads(line.lstrip(".").removeprefix("NATIVE_PYTHON_MATCH_WORK ")) for line in (root / "current.log").read_text().splitlines() if line.lstrip(".").startswith("NATIVE_PYTHON_MATCH_WORK ")]
assert {row["count"] for row in records} == {32, 10000} and len(records) == 2
assert all(row["driver"] == "current" and row["warm"]["metrics"]["text_calls"] == row["warm"]["calls"]["answer_rows"] == row["warm"]["mirror"]["reads"] == 0 for row in records)
print(json.dumps({"status": "ACTUAL_NATIVE_PYTHON_MATCH_CI_PASS", "native_cases": len(current), "immutable_cases": 19, "transport_cases": 17, "counts": [32, 10000], "limits": "Fake embeddings with real session/cut/rank/Pick. Actual-call chunker and canonical-refresh counters UNKNOWN; independent component gates supply their own proof. No installed official binary/model/physical-IO or zero-all-corpus-work claim."}, sort_keys=True))
