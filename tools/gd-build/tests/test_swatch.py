from __future__ import annotations

from pathlib import Path

import pytest

from gd_build import swatch

LOADER = (
    "<script>(function(){var s=document.createElement('script');"
    "s.src='../../color-swatch.js';document.head.appendChild(s);})()</script>"
)


@pytest.mark.parametrize(
    ("relpath", "expected_src"),
    [
        pytest.param("index.html", "color-swatch.js", id="root-depth-0"),
        pytest.param("guide/page.html", "../color-swatch.js", id="one-dir-deep"),
        pytest.param("a/b/c.html", "../../color-swatch.js", id="two-dirs-deep"),
    ],
)
def test_fix_swatches_rewrites_loader_at_depth(tmp_path, relpath: str, expected_src: str) -> None:
    site = tmp_path / "_site"
    page = site / relpath
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"<html><head>{LOADER}</head></html>")
    swatch.fix_swatches(site)
    assert page.read_text() == f'<html><head><script src="{expected_src}"></script></head></html>'


def test_fix_swatches_leaves_non_matching_pages_untouched(tmp_path) -> None:
    site = tmp_path / "_site"
    site.mkdir()
    page = site / "plain.html"
    page.write_text("<html><body>no loader here</body></html>")
    swatch.fix_swatches(site)
    assert page.read_text() == "<html><body>no loader here</body></html>"


def test_fix_swatches_rewrites_every_page(tmp_path) -> None:
    site = tmp_path / "_site"
    (site / "sub").mkdir(parents=True)
    (site / "index.html").write_text(LOADER)
    (site / "sub" / "page.html").write_text(LOADER)
    swatch.fix_swatches(site)
    assert (site / "index.html").read_text() == '<script src="color-swatch.js"></script>'
    assert (site / "sub" / "page.html").read_text() == '<script src="../color-swatch.js"></script>'


def test_site_dir_is_cwd_relative() -> None:
    assert swatch.SITE_DIR == Path("great-docs/_site")
