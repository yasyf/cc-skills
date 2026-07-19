from __future__ import annotations

import dataclasses
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from gd_build import grouped_reference as gr


# --- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Core Functions", "core-functions"),
        ("Style & Rules!", "style-rules"),
        ("  Data  Types  ", "data-types"),
        ("", "group"),
        ("---", "group"),
    ],
)
def test_slug(title: str, expected: str) -> None:
    assert gr.slug(title) == expected


@pytest.mark.parametrize(
    ("package", "name", "expected"),
    [
        ("mypkg", "greet", "mypkg.greet"),
        ("mypkg", "sub.Thing", "mypkg.sub.Thing"),
        ("mypkg", "sub:Thing", "mypkg.sub.Thing"),
        (None, "greet", "greet"),
        ("", "greet", "greet"),
    ],
)
def test_anchor(package: str | None, name: str, expected: str) -> None:
    assert gr.anchor(package, name) == expected


@pytest.mark.parametrize(
    ("titles", "expected"),
    [
        (["A", "B"], True),
        (["A", "B", None], True),
        (["A"], False),
        (["A", None], False),
        ([None, None], False),
        ([], False),
    ],
)
def test_should_group(titles: list[str | None], expected: bool) -> None:
    assert gr.should_group(titles) is expected


# --- fake content tree ------------------------------------------------------


@pytest.fixture
def content(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A fake `great_docs._apiref.content` shaped like the real Page/Doc/Section"""

    @dataclass
    class Doc:
        name: str = ""
        anchor: str = ""
        members: list[object] = field(default_factory=list)

    @dataclass
    class Page:
        kind: str = "page"
        path: str = ""
        summary: object = None
        flatten: bool = False
        contents: list[object] = field(default_factory=list)

    @dataclass
    class Text:
        kind: str = "text"
        contents: str = ""

    @dataclass
    class Section:
        kind: str = "section"
        title: str | None = None
        contents: list[object] = field(default_factory=list)

        def replace(self, **changes: object):
            return dataclasses.replace(self, **changes)

    module = types.ModuleType("great_docs._apiref.content")
    module.Doc = Doc
    module.Page = Page
    module.Text = Text
    module.Section = Section
    apiref = types.ModuleType("great_docs._apiref")
    apiref.content = module
    great_docs = types.ModuleType("great_docs")
    great_docs._apiref = apiref
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs._apiref", apiref)
    monkeypatch.setitem(sys.modules, "great_docs._apiref.content", module)
    return module


def _single_page(content: types.ModuleType, name: str, anchor: str):
    doc = content.Doc(name=name, anchor=anchor)
    return content.Page(path=name, contents=[doc]), doc


def test_single_symbol_doc_identifies_and_skips(content: types.ModuleType) -> None:
    page, doc = _single_page(content, "greet", "pkg.greet")
    assert gr.single_symbol_doc(page) is doc
    # A text block and a multi-object page are not single-symbol pages.
    assert gr.single_symbol_doc(content.Text(contents="hi")) is None
    assert gr.single_symbol_doc(content.Page(path="x", contents=[doc, doc])) is None


def test_group_sections_collapses_titled_sections(content: types.ModuleType) -> None:
    p_a, a = _single_page(content, "greet", "pkg.greet")
    p_b, b = _single_page(content, "sub.Thing", "pkg.sub.Thing")
    p_c, c = _single_page(content, "Widget", "pkg.Widget")
    text = content.Text(contents="prose")
    sections = [
        content.Section(title="Core Functions", contents=[p_a, p_b, text]),
        content.Section(title="Data Types", contents=[p_c]),
    ]

    out = gr.group_sections(sections)

    core, data = out
    # One group page per section, plus the preserved non-symbol Text entry.
    assert len(core.contents) == 2
    page, leftover = core.contents
    assert leftover is text
    assert page.path == "core-functions"
    assert page.flatten is True
    assert page.contents == [a, b]
    assert getattr(page, gr.GROUP_TITLE_ATTR) == "Core Functions"

    (data_page,) = data.contents
    assert data_page.path == "data-types"
    assert data_page.contents == [c]
    assert getattr(data_page, gr.GROUP_TITLE_ATTR) == "Data Types"


def test_group_sections_left_stock_below_two_titled(content: types.ModuleType) -> None:
    p_a, _ = _single_page(content, "greet", "pkg.greet")
    sections = [content.Section(title="Only Group", contents=[p_a])]
    assert gr.group_sections(sections) is sections


def test_group_sections_skips_untitled_section(content: types.ModuleType) -> None:
    p_a, _ = _single_page(content, "greet", "pkg.greet")
    p_b, _ = _single_page(content, "add", "pkg.add")
    p_c, _ = _single_page(content, "Widget", "pkg.Widget")
    untitled = content.Section(title=None, contents=[p_c])
    sections = [
        content.Section(title="A", contents=[p_a]),
        content.Section(title="B", contents=[p_b]),
        untitled,
    ]
    out = gr.group_sections(sections)
    # Titled sections collapse to a single group page; the untitled one is left alone.
    assert out[0].contents[0].path == "a"
    assert out[1].contents[0].path == "b"
    assert out[2] is untitled


def test_member_anchors_recursive(content: types.ModuleType) -> None:
    leaf = content.Doc(name="leaf", anchor="pkg.C.n.leaf")
    method = content.Doc(name="n", anchor="pkg.C.n", members=[leaf])
    cls = content.Doc(name="C", anchor="pkg.C", members=[method])
    assert gr.member_anchors([cls]) == {"pkg.C.n", "pkg.C.n.leaf"}


def test_group_sections_dedups_curated_member(content: types.ModuleType) -> None:
    as_input = content.Doc(name="as_input", anchor="pkg.Event.as_input")
    event = content.Doc(name="Event", anchor="pkg.Event", members=[as_input])
    cls_page = content.Page(path="Event", contents=[event])
    member_page = content.Page(path="Event.as_input", contents=[as_input])
    other, _ = _single_page(content, "Widget", "pkg.Widget")
    sections = [
        content.Section(title="Events", contents=[cls_page, member_page]),
        content.Section(title="Types", contents=[other]),
    ]

    out = gr.group_sections(sections)

    (page, *_) = out[0].contents
    # The class stays; the redundant top-level copy of its embedded member is dropped.
    assert [d.anchor for d in page.contents] == ["pkg.Event"]


def test_group_sections_raises_on_slug_collision(content: types.ModuleType) -> None:
    p_a, _ = _single_page(content, "greet", "pkg.greet")
    p_b, _ = _single_page(content, "add", "pkg.add")
    sections = [
        content.Section(title="Core Functions", contents=[p_a]),
        content.Section(title="core functions", contents=[p_b]),
    ]
    with pytest.raises(ValueError, match="both slug to 'core-functions'"):
        gr.group_sections(sections)


def test_group_sections_preserves_entry_order(content: types.ModuleType) -> None:
    lead = content.Text(contents="lead")
    p_a, a = _single_page(content, "greet", "pkg.greet")
    mid = content.Text(contents="mid")
    p_b, b = _single_page(content, "add", "pkg.add")
    other, _ = _single_page(content, "Widget", "pkg.Widget")
    sections = [
        content.Section(title="Core", contents=[lead, p_a, mid, p_b]),
        content.Section(title="Types", contents=[other]),
    ]

    out = gr.group_sections(sections)

    core = out[0].contents
    # The group page lands at the first collapsed slot; text blocks keep their place.
    assert core[0] is lead
    assert core[1].path == "core" and core[1].contents == [a, b]
    assert core[2] is mid
    assert len(core) == 3


# --- sidebar transform ------------------------------------------------------


class FakeGreatDocs:
    def __init__(self, project_path: Path, config: dict[str, object]) -> None:
        self.project_path = project_path
        self._config = SimpleNamespace(language="en")
        self.written: dict[str, object] | None = None
        (project_path / "_quarto.yml").write_text(json.dumps(config))

    def _write_quarto_yml(self, path: Path, config: dict[str, object]) -> None:
        self.written = config


@pytest.fixture
def sidebar_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake `great_docs.core.read_yaml` (json) and `_translations.get_translation`"""
    core = types.ModuleType("great_docs.core")
    core.read_yaml = lambda f: json.load(f)
    translations = types.ModuleType("great_docs._translations")
    translations.get_translation = lambda key, lang: "API Index"
    great_docs = types.ModuleType("great_docs")
    great_docs.core = core
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs.core", core)
    monkeypatch.setitem(sys.modules, "great_docs._translations", translations)


def _config_with_sections(sections: list[object]) -> dict[str, object]:
    return {"api-reference": {"package": "mypkg", "sections": sections}}


def test_grouped_update_sidebar_emits_group_anchor_entries(
    sidebar_env: None, tmp_path: Path
) -> None:
    config = _config_with_sections(
        [
            {"title": "Core Functions", "contents": ["greet", "sub.compute"]},
            {"title": "Data Types", "contents": [{"name": "Widget"}, "sub.Thing"]},
        ]
    )
    instance = FakeGreatDocs(tmp_path, config)

    def unused_original(_self: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("original should not be called for a grouped config")

    gr.grouped_update_sidebar(instance, unused_original)

    sidebar = instance.written["website"]["sidebar"]
    assert sidebar == [
        {
            "id": "reference",
            "contents": [
                {"text": "API Index", "href": "reference/index.qmd"},
                {
                    "section": "Core Functions",
                    "contents": [
                        {"text": "Core Functions", "href": "reference/core-functions.qmd"},
                        {"text": "greet", "href": "reference/core-functions.qmd#mypkg.greet"},
                        {
                            "text": "sub.compute",
                            "href": "reference/core-functions.qmd#mypkg.sub.compute",
                        },
                    ],
                },
                {
                    "section": "Data Types",
                    "contents": [
                        {"text": "Data Types", "href": "reference/data-types.qmd"},
                        {"text": "Widget", "href": "reference/data-types.qmd#mypkg.Widget"},
                        {"text": "sub.Thing", "href": "reference/data-types.qmd#mypkg.sub.Thing"},
                    ],
                },
            ],
        }
    ]


def test_grouped_update_sidebar_honors_section_package(sidebar_env: None, tmp_path: Path) -> None:
    config = _config_with_sections(
        [
            {"title": "A", "package": "other", "contents": ["thing"]},
            {"title": "B", "contents": ["greet"]},
        ]
    )
    instance = FakeGreatDocs(tmp_path, config)
    gr.grouped_update_sidebar(instance, lambda _self: None)
    section_a = instance.written["website"]["sidebar"][0]["contents"][1]
    # contents[0] is the fragment-free membership leaf; the symbol follows.
    assert section_a["contents"][1]["href"] == "reference/a.qmd#other.thing"


def test_grouped_update_sidebar_honors_item_package(sidebar_env: None, tmp_path: Path) -> None:
    config = _config_with_sections(
        [
            {
                "title": "A",
                "contents": [{"name": "Thing", "package": "vendored"}, "local"],
            },
            {"title": "B", "contents": ["greet"]},
        ]
    )
    instance = FakeGreatDocs(tmp_path, config)
    gr.grouped_update_sidebar(instance, lambda _self: None)
    section_a = instance.written["website"]["sidebar"][0]["contents"][1]["contents"]
    # section_a[0] is the membership leaf; per-item package override wins for its
    # entry; the sibling uses the top package.
    assert section_a[1]["href"] == "reference/a.qmd#vendored.Thing"
    assert section_a[2]["href"] == "reference/a.qmd#mypkg.local"


def test_grouped_update_sidebar_falls_back_below_two_titled(
    sidebar_env: None, tmp_path: Path
) -> None:
    config = _config_with_sections([{"title": "Only", "contents": ["greet"]}])
    instance = FakeGreatDocs(tmp_path, config)
    called: list[bool] = []

    def original(_self: object) -> None:
        called.append(True)

    gr.grouped_update_sidebar(instance, original)
    assert called == [True]
    assert instance.written is None
