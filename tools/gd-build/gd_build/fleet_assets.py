"""Fleet design-system CSS materialization (the titles.py pattern for CSS).

`materialize_fleet_css` writes the packaged `assets/fleet-theme.css` into the
consumer repo at `docs/assets/.gd-build/fleet-theme.css`. `quarto_config_entries`
returns the `format.html` entries to merge at the `_write_quarto_yml` seam: the
integrator copies `CSS_DEST` into the Quarto staging root and references it by
basename — the same mechanism great-docs uses for user `css:` files
(core.py:284-287 copy, core.py:11847-11849 config).
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

CSS_DEST = Path("docs/assets/.gd-build/fleet-theme.css")


def materialize_fleet_css() -> Path:
    CSS_DEST.parent.mkdir(parents=True, exist_ok=True)
    CSS_DEST.write_text(
        importlib.resources.files("gd_build").joinpath("assets/fleet-theme.css").read_text()
    )
    return CSS_DEST


def quarto_config_entries() -> dict[str, list[str]]:
    return {"css": [CSS_DEST.name]}
