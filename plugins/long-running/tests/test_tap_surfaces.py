"""Each tap surface's event, through the board recorder, makes the record the land-order reader is tested on; a new owner event with no row here fails CI."""

from __future__ import annotations

import json
from pathlib import Path

import board_watch
import pytest
from test_board_watch import SESSION, RecordedShell

SURFACES = json.loads((Path(__file__).parents[1] / "lr" / "internal" / "bus" / "testdata" / "tap_surfaces.json").read_text())


class SurfaceShell(RecordedShell):
    def __init__(self, surface: dict) -> None:
        super().__init__(doc=surface["doc"])
        self.surface = surface

    def watch(self, session: str):
        yield json.dumps(self.surface["event"]) + "\n"


def test_every_owner_event_the_recorder_posts_has_a_surface() -> None:
    assert {surface["event"]["type"] for surface in SURFACES} == set(board_watch.OWNER_EVENTS)


@pytest.mark.parametrize("surface", SURFACES, ids=[surface["surface"] for surface in SURFACES])
def test_a_surfaces_tap_records_what_the_order_reader_is_tested_on(surface: dict, tmp_path: Path) -> None:
    shell = SurfaceShell(surface)
    assert board_watch.main(["start", "--session", SESSION, "--program", "brook", "--state-dir", str(tmp_path)], shell) == 0

    posts = [post for post in shell.posts if post[post.index("--kind") + 1] == "owner"]

    assert [post[post.index("--text") + 1] for post in posts] == [surface["record"]["text"]]
    attrs = dict(value.split("=", 1) for flag, value in zip(posts[0], posts[0][1:], strict=False) if flag == "--attr")
    assert surface["record"]["attrs"].items() <= attrs.items()
