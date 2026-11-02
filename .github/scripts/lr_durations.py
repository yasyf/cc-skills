# /// script
# requires-python = ">=3.13"
# ///
"""Rewrite the seconds long-running CI balances its shards on, from one run's artifacts.

Run it on a push to main or a nightly run, which run every test and control:

  gh run download <run-id> -D <dir>
  uv run .github/scripts/lr_durations.py <dir>

.github/lr-test-durations.json gets, from the `pytest-*` junit reports, the
seconds of every test that took 5 s or more under `slow`, which the conftest
plans one by one, and under `files` two numbers per test file for the rest: the
seconds of its tests the conftest leaves unmarked, then of those it marks
`scale`. A pull request skips the scale tests, so it plans without them.
.github/lr-controls/seconds.json gets each control script's seconds, from the
`lr-controls-*` artifacts.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

GITHUB = Path(__file__).resolve().parents[1]
SLOW = 5.0


def rows(entries: dict[str, str], depth: int = 0) -> str:
    indent = "  " * depth
    return "{\n" + ",\n".join(f"{indent}  {json.dumps(name)}: {value}" for name, value in sorted(entries.items())) + f"\n{indent}}}" + ("" if depth else "\n")


def main() -> None:
    artifacts = Path(sys.argv[1])
    files: defaultdict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    slow = {}
    for report in artifacts.glob("pytest-*/pytest.xml"):
        for case in ET.parse(report).iter("testcase"):
            _, module, *classes = case.get("classname").split(".")
            seconds = float(case.get("time"))
            if seconds >= SLOW:
                slow[f"tests/{module}.py::{'::'.join([*classes, case.get('name')])}"] = f"{seconds:.1f}"
            else:
                files[f"{module}.py"][any(prop.get("name") == "scale" for prop in case.iter("property"))] += seconds
    controls = {name: seconds for record in artifacts.glob("lr-controls-*/seconds.json") for name, seconds in json.loads(record.read_text()).items()}
    tests = {"files": rows({name: f"[{plain:.1f}, {scale:.1f}]" for name, (plain, scale) in files.items()}, 1), "slow": rows(slow, 1)}
    (GITHUB / "lr-test-durations.json").write_text(rows(tests))
    (GITHUB / "lr-controls" / "seconds.json").write_text(rows({name: f"{seconds:.1f}" for name, seconds in controls.items()}))


if __name__ == "__main__":
    main()
