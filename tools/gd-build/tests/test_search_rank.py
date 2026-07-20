from __future__ import annotations

import json
from pathlib import Path

import pytest

from gd_build.search_rank import apply_search_ranking, classify_entry

REAL_FUSE_KEYS = """  keys: [
    { name: "title", weight: 20 },
    { name: "section", weight: 20 },
    { name: "text", weight: 10 },
  ],"""


def make_site(tmp_path: Path, *, fuse_keys: str = REAL_FUSE_KEYS) -> tuple[Path, Path, Path]:
    site_dir = tmp_path / "_site"
    script_path = site_dir / "site_libs/quarto-search/quarto-search.js"
    script_path.parent.mkdir(parents=True)
    search_path = site_dir / "search.json"
    search_path.write_text(
        json.dumps(
            [
                {
                    "objectID": "reference/widget.html",
                    "href": "reference/widget.html",
                    "title": "Widget",
                    "section": "API reference",
                    "text": "Create a widget.",
                },
                {
                    "objectID": "guide/widgets.html",
                    "href": "guide/widgets.html",
                    "title": "Widget guide",
                    "section": "Getting productive",
                    "text": "Learn how to create a widget.",
                },
            ]
        )
    )
    script_path.write_text(
        """/* Search Index Handling */
// create the index
var fuseIndex = undefined;
var shownWarning = false;

// fuse index options
const kFuseIndexOptions = {
"""
        + fuse_keys
        + """

  ignoreLocation: true,
  threshold: 0.1,
};
"""
    )
    return site_dir, search_path, script_path


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("getting-started/install.html", "narrative", id="getting-started"),
        pytest.param("tutorial/first-block.html", "narrative", id="tutorial"),
        pytest.param("guide/widgets.html", "narrative", id="guide"),
        pytest.param("cheatsheet/index.html", "narrative", id="cheatsheet"),
        pytest.param("examples/basic.html", "narrative", id="examples"),
        pytest.param("index.html", "narrative", id="site-index"),
        pytest.param("reference/widget.html", "reference", id="reference"),
        pytest.param("about.html", "other", id="other"),
    ],
)
def test_classify_entry(href: str, expected: str) -> None:
    assert classify_entry(href) == expected


def test_apply_search_ranking_patches_index_and_fuse_keys_idempotently(tmp_path: Path) -> None:
    site_dir, search_path, script_path = make_site(tmp_path)

    apply_search_ranking(site_dir)

    entries = json.loads(search_path.read_text())
    reference, narrative = entries
    assert "gd_rank" not in reference
    assert narrative["gd_rank"] == "Widget guide Getting productive"
    script = script_path.read_text()
    assert script.count('{ name: "gd_rank", weight: 30 },') == 1
    assert REAL_FUSE_KEYS not in script

    first_search = search_path.read_bytes()
    first_script = script_path.read_bytes()
    apply_search_ranking(site_dir)
    assert search_path.read_bytes() == first_search
    assert script_path.read_bytes() == first_script


def test_custom_narrative_prefixes_replace_defaults(tmp_path: Path) -> None:
    site_dir, search_path, _ = make_site(tmp_path)
    entries = json.loads(search_path.read_text())
    entries.append(
        {
            "objectID": "tutorial/widgets.html",
            "href": "tutorial/widgets.html",
            "title": "Widget tutorial",
            "section": "Tutorial",
            "text": "Build a widget.",
        }
    )
    search_path.write_text(json.dumps(entries))

    apply_search_ranking(site_dir, ["tutorial/"])

    ranked = json.loads(search_path.read_text())
    assert "gd_rank" not in ranked[1]
    assert ranked[2]["gd_rank"] == "Widget tutorial Tutorial"


def test_missing_search_json_warns_without_modifying_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site_dir, search_path, script_path = make_site(tmp_path)
    search_path.unlink()
    original_script = script_path.read_bytes()

    apply_search_ranking(site_dir)

    assert "::warning::gd-build search ranking skipped:" in capsys.readouterr().out
    assert script_path.read_bytes() == original_script


def test_keys_probe_failure_warns_without_modifying_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_keys = REAL_FUSE_KEYS.replace('weight: 10', 'weight: 11')
    site_dir, search_path, script_path = make_site(tmp_path, fuse_keys=bad_keys)
    original_search = search_path.read_bytes()
    original_script = script_path.read_bytes()

    apply_search_ranking(site_dir)

    warning = capsys.readouterr().out
    assert "::warning::gd-build search ranking skipped:" in warning
    assert "Fuse keys block not found exactly once" in warning
    assert search_path.read_bytes() == original_search
    assert script_path.read_bytes() == original_script
