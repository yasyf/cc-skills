from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from long_running import ends


class Shown:
    def __init__(self, out: str) -> None:
        self.out = out

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        assert argv == ["cci", "show", "41"]
        return self.out


@pytest.mark.parametrize(
    ("shown", "closed"),
    [
        ("@9:05 PM\n41 ask lane-a>root take it? [RESOLVED #42]\n", True),
        ("#41 9:05 PM ASK lane-a -> root take it? [ANSWERED #42]\n", True),
        ("@9:05 PM\n41 ask lane-a>root take it?\nthe body quotes [RESOLVED #7]\n", False),
        ("", False),
    ],
)
def test_a_record_end_is_reached_once_the_records_own_line_carries_a_closing_mark(shown: str, closed: bool) -> None:
    reached = ends.Facts(Shown(shown), Path("."), {}, []).reached(ends.End("record", "41"), datetime.now(UTC))

    assert (reached == "record #41 is closed") is closed
