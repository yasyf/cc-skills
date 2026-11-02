# /// script
# requires-python = ">=3.13"
# ///
"""Rewrite the seconds long-running CI balances its shards on, from one run's artifacts.

Run it on a push to main or a nightly run, which run every test and control:

  gh run download <run-id> -D <dir>
  uv run .github/scripts/lr_durations.py <dir>

.github/lr-test-durations.json gets two numbers per test file, from the
`pytest-*` junit reports: the seconds of its tests the conftest leaves
unmarked, then the seconds of those it marks `scale`. A pull request skips the
scale tests, so it balances its shards on the first number alone.
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


def rows(entries: dict[str, str]) -> str:
    return "{\n" + ",\n".join(f"  {json.dumps(name)}: {value}" for name, value in sorted(entries.items())) + "\n}\n"


def main() -> None:
    artifacts = Path(sys.argv[1])
    tests: defaultdict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for report in artifacts.glob("pytest-*/pytest.xml"):
        for case in ET.parse(report).iter("testcase"):
            scale = any(prop.get("name") == "scale" for prop in case.iter("property"))
            tests[f"{case.get('classname').split('.')[1]}.py"][scale] += float(case.get("time"))
    controls = {name: seconds for record in artifacts.glob("lr-controls-*/seconds.json") for name, seconds in json.loads(record.read_text()).items()}
    (GITHUB / "lr-test-durations.json").write_text(rows({name: f"[{plain:.1f}, {scale:.1f}]" for name, (plain, scale) in tests.items()}))
    (GITHUB / "lr-controls" / "seconds.json").write_text(rows({name: f"{seconds:.1f}" for name, seconds in controls.items()}))


if __name__ == "__main__":
    main()
