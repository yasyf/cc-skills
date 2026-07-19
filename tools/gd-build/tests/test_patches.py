from __future__ import annotations

import dataclasses
import sys
import types
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from gd_build import patches as patches_mod


def make_patch(probe: Callable[[], str | None], apply: Callable[[], None]) -> patches_mod.Patch:
    return patches_mod.Patch(
        name="fake",
        verified_window="",
        probe=probe,
        apply=apply,
        expected_savings="",
        upstream_ref="",
    )


def install_introspect(
    monkeypatch: pytest.MonkeyPatch, *, make_loader: bool = True, loader_param: bool = True
) -> types.ModuleType:
    introspect = types.ModuleType("great_docs._apiref.introspect")
    if make_loader:
        introspect.make_loader = lambda parser: f"loader:{parser}"
    if loader_param:

        def get_object(path: object, parser: object, loader: object = None) -> dict[str, object]:
            return {"path": path, "parser": parser, "loader": loader}
    else:

        def get_object(path: object, parser: object) -> dict[str, object]:  # type: ignore[misc]
            return {"path": path, "parser": parser}

    introspect.get_object = get_object
    apiref = types.ModuleType("great_docs._apiref")
    apiref.introspect = introspect
    great_docs = types.ModuleType("great_docs")
    great_docs._apiref = apiref
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs._apiref", apiref)
    monkeypatch.setitem(sys.modules, "great_docs._apiref.introspect", introspect)
    return introspect


def install_git(
    monkeypatch: pytest.MonkeyPatch, *, has_gitinfo: bool = True, classmethod_fp: bool = True
) -> types.ModuleType:
    git = types.ModuleType("griffe._internal.git")
    if has_gitinfo:

        class GitInfo:
            calls: list[object] = []

            @classmethod
            def from_package(cls, package: object) -> str:
                cls.calls.append(package)
                return f"info:{package}"

        if not classmethod_fp:
            GitInfo.from_package = staticmethod(lambda package: f"info:{package}")
        git.GitInfo = GitInfo
    internal = types.ModuleType("griffe._internal")
    internal.git = git
    griffe = types.ModuleType("griffe")
    griffe._internal = internal
    monkeypatch.setitem(sys.modules, "griffe", griffe)
    monkeypatch.setitem(sys.modules, "griffe._internal", internal)
    monkeypatch.setitem(sys.modules, "griffe._internal.git", git)
    return git


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            None,
            [
                ("shared-griffe-loader", True),
                ("griffe-gitinfo-cache", True),
                ("grouped-reference-pages", True),
            ],
            id="unset-defaults-to-all",
        ),
        pytest.param(
            "all",
            [
                ("shared-griffe-loader", True),
                ("griffe-gitinfo-cache", True),
                ("grouped-reference-pages", True),
            ],
            id="all",
        ),
        pytest.param("none", [], id="none-selects-nothing"),
        pytest.param("shared-griffe-loader", [("shared-griffe-loader", True)], id="single-csv"),
        pytest.param(
            "griffe-gitinfo-cache,shared-griffe-loader",
            [("griffe-gitinfo-cache", True), ("shared-griffe-loader", True)],
            id="csv-order-preserved",
        ),
        pytest.param("bogus", [("bogus", False)], id="unknown-name-maps-to-none"),
        pytest.param(
            "  shared-griffe-loader , bogus ",
            [("shared-griffe-loader", True), ("bogus", False)],
            id="whitespace-trimmed",
        ),
        pytest.param(
            "shared-griffe-loader,,griffe-gitinfo-cache",
            [("shared-griffe-loader", True), ("griffe-gitinfo-cache", True)],
            id="empty-parts-filtered",
        ),
    ],
)
def test_selected_patches_parsing(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: list[tuple[str, bool]]
) -> None:
    if value is None:
        monkeypatch.delenv("GD_BUILD_PATCHES", raising=False)
    else:
        monkeypatch.setenv("GD_BUILD_PATCHES", value)
    assert [(name, patch is not None) for patch, name in patches_mod.selected_patches()] == expected


def test_skip_reason_unknown_patch() -> None:
    assert patches_mod.skip_reason(None) == "unknown patch name"


def test_skip_reason_probe_returns_reason_and_skips_apply() -> None:
    applied: list[str] = []
    reason = patches_mod.skip_reason(make_patch(lambda: "gate closed", lambda: applied.append("apply")))
    assert reason == "gate closed"
    assert applied == []


def test_skip_reason_probe_raises_is_isolated() -> None:
    def probe() -> str | None:
        raise RuntimeError("boom")

    assert patches_mod.skip_reason(make_patch(probe, lambda: None)) == "RuntimeError: boom"


def test_skip_reason_apply_raises_is_isolated() -> None:
    def apply() -> None:
        raise ValueError("nope")

    assert patches_mod.skip_reason(make_patch(lambda: None, apply)) == "ValueError: nope"


def test_skip_reason_success_returns_none_and_applies() -> None:
    applied: list[bool] = []
    reason = patches_mod.skip_reason(make_patch(lambda: None, lambda: applied.append(True)))
    assert reason is None
    assert applied == [True]


def test_emit_patched(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    patches_mod.emit("shared-griffe-loader", None)
    out = capsys.readouterr()
    assert out.err == "PATCHED: shared-griffe-loader\n"
    assert out.out == ""


def test_emit_unpatched_without_github_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    patches_mod.emit("griffe-gitinfo-cache", "griffe.GitInfo missing")
    out = capsys.readouterr()
    assert out.err == "UNPATCHED: griffe-gitinfo-cache — running STOCK (griffe.GitInfo missing)\n"
    assert out.out == ""


def test_emit_unpatched_under_github_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    patches_mod.emit("griffe-gitinfo-cache", "griffe.GitInfo missing")
    out = capsys.readouterr()
    line = "UNPATCHED: griffe-gitinfo-cache — running STOCK (griffe.GitInfo missing)"
    assert out.err == f"{line}\n"
    assert out.out == f"::warning::{line}\n"


def test_probe_shared_loader_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_shared_loader() is None


def test_probe_shared_loader_version_below_window(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.1")
    assert patches_mod.probe_shared_loader() == "great-docs 0.14.1 is outside [0.15, 0.16)"


def test_probe_shared_loader_missing_make_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch, make_loader=False)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.2")
    assert patches_mod.probe_shared_loader() == "introspect.make_loader missing (upstream fix absent)"


def test_probe_shared_loader_no_loader_param(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch, loader_param=False)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_shared_loader() == "introspect.get_object has no loader parameter"


def test_apply_shared_loader_shares_loader_per_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[object] = []
    introspect = install_introspect(monkeypatch)
    introspect.make_loader = lambda parser: built.append(parser) or f"loader:{parser}"
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    patches_mod.apply_shared_loader()
    first = introspect.get_object("a.py", "python")
    second = introspect.get_object("b.py", "python")
    assert first["loader"] == "loader:python"
    assert second["loader"] == "loader:python"
    assert built == ["python"]


def test_probe_gitinfo_cache_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    install_git(monkeypatch)
    assert patches_mod.probe_gitinfo_cache() is None


def test_probe_gitinfo_cache_missing_gitinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    install_git(monkeypatch, has_gitinfo=False)
    assert patches_mod.probe_gitinfo_cache() == "griffe.GitInfo missing"


def test_probe_gitinfo_cache_not_classmethod(monkeypatch: pytest.MonkeyPatch) -> None:
    install_git(monkeypatch, classmethod_fp=False)
    assert (
        patches_mod.probe_gitinfo_cache()
        == "griffe.GitInfo.from_package is not a classmethod (layout changed)"
    )


def test_apply_gitinfo_cache_memoizes_per_package(monkeypatch: pytest.MonkeyPatch) -> None:
    git = install_git(monkeypatch)
    patches_mod.apply_gitinfo_cache()
    assert git.GitInfo.from_package("pkgA") == "info:pkgA"
    assert git.GitInfo.from_package("pkgA") == "info:pkgA"
    assert git.GitInfo.from_package("pkgB") == "info:pkgB"
    assert git.GitInfo.calls == ["pkgA", "pkgB"]


def test_apply_patches_none_selects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    assert patches_mod.apply_patches() == {}


def test_apply_patches_reports_outcomes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GD_BUILD_PATCHES", "griffe-gitinfo-cache,bogus")
    install_git(monkeypatch)
    outcomes = patches_mod.apply_patches()
    assert outcomes == {"griffe-gitinfo-cache": True, "bogus": False}
    err = capsys.readouterr().err
    assert "PATCHED: griffe-gitinfo-cache\n" in err
    assert "UNPATCHED: bogus — running STOCK (unknown patch name)\n" in err


# --- grouped-reference probe -------------------------------------------------


def probe_fake_build(self: object, page_filter: str = "*") -> None:
    resolve()  # noqa: F821  -- never called; the probe only inspects this source


def probe_fake_build_inlined(self: object, page_filter: str = "*") -> None:
    return None


def install_grouped_great_docs(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A fake great-docs tree shaped like every internal `probe_grouped_reference` gates on"""

    @dataclasses.dataclass
    class Doc:
        anchor: str = ""

    @dataclasses.dataclass
    class Page:
        path: str = ""
        flatten: bool = False
        contents: list[object] = dataclasses.field(default_factory=list)

    @dataclasses.dataclass
    class Section:
        title: str | None = None
        contents: list[object] = dataclasses.field(default_factory=list)

        def replace(self, **changes: object) -> Section:
            return dataclasses.replace(self, **changes)

    class APIReference:
        build = probe_fake_build

    @dataclasses.dataclass
    class RenderDoc:
        level: int = 1
        contained: bool = False
        page_path: str = ""

        def render_title(self) -> None: ...

        def render_summary(self) -> None: ...

    @dataclasses.dataclass
    class RenderDocClass(RenderDoc): ...

    @dataclasses.dataclass
    class RenderDocFunction(RenderDoc): ...

    @dataclasses.dataclass
    class RenderDocAttribute(RenderDoc): ...

    @dataclasses.dataclass
    class RenderDocModule(RenderDoc): ...

    class RenderAPIPage:
        @property
        def _has_one_object(self) -> bool:
            return False

        def render_metadata(self) -> None: ...

    @dataclasses.dataclass
    class Header:
        level: int = 1
        content: object = None
        attr: object = None

    @dataclasses.dataclass
    class Attr:
        identifier: str | None = None

    class GreatDocs:
        def _update_sidebar_from_sections(self) -> None: ...

        def _write_quarto_yml(self, path: object, config: object) -> None: ...

    class Config:
        def should_split_methods(self, method_count: int) -> bool:
            return False

    core = types.ModuleType("great_docs.core")
    core.read_yaml = lambda f: {}
    core.GreatDocs = GreatDocs
    config = types.ModuleType("great_docs.config")
    config.Config = Config
    content = types.ModuleType("great_docs._apiref.content")
    content.Page, content.Section, content.Doc = Page, Section, Doc
    api_reference = types.ModuleType("great_docs._apiref.api_reference")
    api_reference.resolve = lambda *a, **k: []
    api_reference.APIReference = APIReference
    render = types.ModuleType("great_docs._apiref._render")
    render.RenderAPIPage = RenderAPIPage
    render.RenderDocClass = RenderDocClass
    render.RenderDocFunction = RenderDocFunction
    render.RenderDocAttribute = RenderDocAttribute
    render.RenderDocModule = RenderDocModule
    fmt = types.ModuleType("great_docs._apiref._format")
    fmt.markdown_escape = lambda s: s
    blocks = types.ModuleType("great_docs._apiref.pandoc.blocks")
    blocks.Header = Header
    components = types.ModuleType("great_docs._apiref.pandoc.components")
    components.Attr = Attr
    inlines = types.ModuleType("great_docs._apiref.pandoc.inlines")
    inlines.Link = type("Link", (), {})
    pandoc = types.ModuleType("great_docs._apiref.pandoc")
    pandoc.blocks, pandoc.components, pandoc.inlines = blocks, components, inlines
    apiref = types.ModuleType("great_docs._apiref")
    apiref.content, apiref.api_reference, apiref._render, apiref._format, apiref.pandoc = (
        content,
        api_reference,
        render,
        fmt,
        pandoc,
    )
    great_docs = types.ModuleType("great_docs")
    great_docs.core, great_docs.config, great_docs._apiref = core, config, apiref

    modules = {
        "great_docs": great_docs,
        "great_docs.core": core,
        "great_docs.config": config,
        "great_docs._apiref": apiref,
        "great_docs._apiref.content": content,
        "great_docs._apiref.api_reference": api_reference,
        "great_docs._apiref._render": render,
        "great_docs._apiref._format": fmt,
        "great_docs._apiref.pandoc": pandoc,
        "great_docs._apiref.pandoc.blocks": blocks,
        "great_docs._apiref.pandoc.components": components,
        "great_docs._apiref.pandoc.inlines": inlines,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    return SimpleNamespace(
        core=core,
        config=config,
        content=content,
        api_reference=api_reference,
        GreatDocs=GreatDocs,
        Config=Config,
    )


def test_probe_grouped_reference_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    install_grouped_great_docs(monkeypatch)
    assert patches_mod.probe_grouped_reference() is None


def test_probe_grouped_reference_version_below_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.9")
    assert patches_mod.probe_grouped_reference() == "great-docs 0.14.9 is outside [0.15, 0.16)"


def test_probe_grouped_reference_build_ignores_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    env = install_grouped_great_docs(monkeypatch)
    env.api_reference.APIReference.build = probe_fake_build_inlined
    assert patches_mod.probe_grouped_reference() == (
        "APIReference.build no longer calls resolve() (rebind would miss)"
    )


def test_probe_grouped_reference_page_fields_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    env = install_grouped_great_docs(monkeypatch)

    @dataclasses.dataclass
    class Page:
        path: str = ""
        contents: list[object] = dataclasses.field(default_factory=list)

    env.content.Page = Page
    assert patches_mod.probe_grouped_reference() == "content.Page fields changed"


def test_probe_grouped_reference_missing_sidebar_method(monkeypatch: pytest.MonkeyPatch) -> None:
    env = install_grouped_great_docs(monkeypatch)
    monkeypatch.delattr(env.GreatDocs, "_update_sidebar_from_sections")
    assert (
        patches_mod.probe_grouped_reference() == "GreatDocs._update_sidebar_from_sections missing"
    )


def test_probe_grouped_reference_missing_split_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    env = install_grouped_great_docs(monkeypatch)
    monkeypatch.delattr(env.Config, "should_split_methods")
    assert patches_mod.probe_grouped_reference() == (
        "config.Config.should_split_methods missing (big-class splitting gate)"
    )


def force_no_great_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import great_docs` raise regardless of whether it is installed"""
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    monkeypatch.setitem(sys.modules, "great_docs", None)


def test_grouped_reference_degrades_without_great_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    force_no_great_docs(monkeypatch)
    registry = {patch.name: patch for patch in patches_mod.patches()}
    assert patches_mod.skip_reason(registry["grouped-reference-pages"]) is not None


def test_grouped_reference_emits_unpatched_without_great_docs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    force_no_great_docs(monkeypatch)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GD_BUILD_PATCHES", "grouped-reference-pages")
    assert patches_mod.apply_patches() == {"grouped-reference-pages": False}
    assert "UNPATCHED: grouped-reference-pages — running STOCK (" in capsys.readouterr().err
