# /// script
# requires-python = ">=3.13"
# ///
"""Rewrite .github/lr-test-durations.json from one long-running CI run's junit reports.

Run it on a push to main or a nightly run, which run the scale tests too:

  gh run download <run-id> -p 'pytest-*' -D <dir>
  uv run .github/scripts/lr_durations.py <dir>/*/pytest.xml

Each test file gets two numbers: the seconds of its tests the conftest leaves
unmarked, then the seconds of those it marks `scale`. A pull request skips the
scale tests, so it balances its shards on the first number alone.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

DURATIONS = Path(__file__).resolve().parents[1] / "lr-test-durations.json"


def main() -> None:
    seconds: defaultdict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for report in sys.argv[1:]:
        for case in ET.parse(report).iter("testcase"):
            scale = any(prop.get("name") == "scale" for prop in case.iter("property"))
            seconds[f"{case.get('classname').split('.')[1]}.py"][scale] += float(case.get("time"))
    rows = (f"  {json.dumps(name)}: [{plain:.1f}, {scale:.1f}]" for name, (plain, scale) in sorted(seconds.items()))
    DURATIONS.write_text("{\n" + ",\n".join(rows) + "\n}\n")


if __name__ == "__main__":
    main()
