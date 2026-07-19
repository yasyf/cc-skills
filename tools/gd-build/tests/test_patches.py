from __future__ import annotations

import dataclasses
import sys
import types
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from gd_build import patches as patches_mod

CUSTOM_CONDITION_DOCTEST = (
    ">>> class LargeFile(CustomCondition):\n"
    "...     def check(self, evt: BaseHookEvent) -> bool:\n"
    "...         return bool(evt.file and evt.file.path.stat().st_size > 1_000_000)\n"
    "...\n"
    '>>> app.hook(Event.PreToolUse, only_if=[LargeFile()], message="Large file", block=True)'
)


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
                ("metadata-margin-off", True),
                ("fleet-footer", True),
                ("example-doctest-fence", True),
                ("type-annotations", True),
                ("homepage-demo-transform", True),
                ("hero-logo-off", True),
                ("fleet-css", True),
            ],
            id="unset-defaults-to-all",
        ),
        pytest.param(
            "all",
            [
                ("shared-griffe-loader", True),
                ("griffe-gitinfo-cache", True),
                ("grouped-reference-pages", True),
                ("metadata-margin-off", True),
                ("fleet-footer", True),
                ("example-doctest-fence", True),
                ("type-annotations", True),
                ("homepage-demo-transform", True),
                ("hero-logo-off", True),
                ("fleet-css", True),
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


def install_core(monkeypatch: pytest.MonkeyPatch, cls: type) -> types.ModuleType:
    core = types.ModuleType("great_docs.core")
    core.GreatDocs = cls
    great_docs = types.ModuleType("great_docs")
    great_docs.core = core
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs.core", core)
    return core


class _MarginRealShape:
    def _build_metadata_margin(self) -> str:
        margin_sections: list[str] = []
        return "\n".join(margin_sections) if margin_sections else ""


class _MarginNeighbor:
    def _build_metadata_margin(self) -> str:
        margin_sections: list[str] = []
        return "".join(margin_sections)


class _WriteRealShape:
    def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
        header_comment = "# Generated\n"
        with open(quarto_yml, "w") as f:
            f.write(header_comment)
            write_yaml(config, f)  # noqa: F821 — source text only; never executed


class _WriteNeighbor:
    def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
        with open(quarto_yml, "w") as f:
            f.write("# Generated\n")
            f.write(repr(config))


class _HeroRealShape:
    def _build_hero_section(
        self, readme_content: str | None = None
    ) -> tuple[str, str | None]:
        return "", None

    def _create_index_from_readme(self, force_rebuild: bool = False) -> None:
        hero_html, cleaned_content = self._build_hero_section(readme_content)  # noqa: F821, F841 — source text only; never executed
        if cleaned_content is not None:
            readme_content = cleaned_content  # noqa: F841 — source text only; never executed


class _HeroNeighbor:
    def _build_hero_section(
        self, readme_content: str | None = None
    ) -> tuple[str, str | None]:
        return "", None

    def _create_index_from_readme(self, force_rebuild: bool = False) -> None:
        hero_html, cleaned_content = self._build_hero_section()  # noqa: F841 — source text only; never executed
        if cleaned_content is not None:
            readme_content = cleaned_content  # noqa: F841 — source text only; never executed


def test_probe_metadata_margin_off_real_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("great_docs")
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_metadata_margin_off() is None


def test_probe_metadata_margin_off_matches_synthetic_real_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _MarginRealShape)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_metadata_margin_off() is None


def test_probe_metadata_margin_off_self_retires_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _MarginNeighbor)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_metadata_margin_off()
        == "GreatDocs._build_metadata_margin return shape changed"
    )


def test_probe_metadata_margin_off_version_below_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _MarginRealShape)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.9")
    assert (
        patches_mod.probe_metadata_margin_off()
        == "great-docs 0.14.9 is outside [0.15, 0.16)"
    )


def test_probe_metadata_margin_off_missing_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Empty:
        pass

    install_core(monkeypatch, _Empty)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_metadata_margin_off()
        == "GreatDocs._build_metadata_margin missing"
    )


def test_apply_metadata_margin_off_keeps_side_effects_and_empties_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SideEffect:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def _build_metadata_margin(self) -> str:
            self.calls.append("side-effect")
            return "SIDEBAR MARKUP"

        def _create_index_from_readme(self, force_rebuild: bool = False) -> None:
            return None

    install_core(monkeypatch, _SideEffect)
    patches_mod.apply_metadata_margin_off()
    inst = _SideEffect()
    assert inst._build_metadata_margin() == ""
    assert inst.calls == ["side-effect"]


def test_probe_fleet_footer_real_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("great_docs")
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_fleet_footer() is None


def test_probe_fleet_footer_matches_synthetic_real_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _WriteRealShape)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_fleet_footer() is None


def test_probe_fleet_footer_self_retires_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _WriteNeighbor)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_fleet_footer()
        == "GreatDocs._write_quarto_yml serialization shape changed"
    )


def test_probe_fleet_footer_missing_method(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Empty:
        pass

    install_core(monkeypatch, _Empty)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_fleet_footer() == "GreatDocs._write_quarto_yml missing"


def test_merge_page_footer_preserves_existing_center() -> None:
    website = {"page-footer": {"center": "Developed by X."}}
    patches_mod.merge_page_footer(website, "**P** · t", "[A](a)")
    assert website["page-footer"] == {
        "center": "Developed by X.",
        "left": "**P** · t",
        "right": "[A](a)",
    }


def test_merge_page_footer_idempotent_setdefault() -> None:
    website = {"page-footer": {"center": "C"}}
    patches_mod.merge_page_footer(website, "L", "R")
    once = dict(website["page-footer"])
    patches_mod.merge_page_footer(website, "L2", "R2")
    assert website["page-footer"] == once


def test_merge_page_footer_creates_when_absent() -> None:
    website: dict = {}
    patches_mod.merge_page_footer(website, "L", "R")
    assert website["page-footer"] == {"left": "L", "right": "R"}


def test_merge_page_footer_wraps_scalar_footer_as_center() -> None:
    website = {"page-footer": "existing scalar"}
    patches_mod.merge_page_footer(website, "L", "R")
    assert website["page-footer"] == {
        "center": "existing scalar",
        "left": "L",
        "right": "R",
    }


class _FooterConfig:
    display_name = "Captain Hook"
    hero_tagline = None
    pypi = True
    skill_enabled = True


class _FooterInstance:
    _config = _FooterConfig()

    def _get_package_metadata(self) -> dict:
        return {"description": "hooks for Claude Code"}

    def _detect_package_name(self) -> str:
        return "captain-hook"

    def _get_github_repo_info(self) -> tuple[str, str, str]:
        return "yasyf", "captain-hook", "https://github.com/yasyf/captain-hook"


def test_footer_links_parity_with_sidebar() -> None:
    links = patches_mod._footer_links(_FooterInstance())
    assert links == (
        "[PyPI](https://pypi.org/project/captain-hook/) · "
        "[Source](https://github.com/yasyf/captain-hook) · "
        "[Issues](https://github.com/yasyf/captain-hook/issues) · "
        "[Changelog](https://github.com/yasyf/captain-hook/blob/main/CHANGELOG.md) · "
        "[llms.txt](llms.txt) · "
        "[llms-full.txt](llms-full.txt) · "
        "[Skills](skills.html)"
    )


def test_footer_links_drops_pypi_and_skills_when_disabled() -> None:
    class _Cfg(_FooterConfig):
        pypi = False
        skill_enabled = False

    class _Inst(_FooterInstance):
        _config = _Cfg()

    links = patches_mod._footer_links(_Inst())
    assert "PyPI" not in links
    assert "Skills" not in links
    assert links.startswith("[Source](https://github.com/yasyf/captain-hook)")


def test_footer_colophon_uses_description_fallback() -> None:
    inst = _FooterInstance()
    assert (
        patches_mod._footer_colophon(inst, inst._get_package_metadata())
        == "**Captain Hook** · hooks for Claude Code"
    )


def test_apply_fleet_footer_merges_and_preserves_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _Cfg(_FooterConfig):
        pass

    class _Fake(_FooterInstance):
        _config = _Cfg()

        def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
            captured["config"] = config

    install_core(monkeypatch, _Fake)
    patches_mod.apply_fleet_footer()
    cfg = {"website": {"page-footer": {"center": "Developed by X."}}}
    _Fake()._write_quarto_yml("_quarto.yml", cfg)
    footer = cfg["website"]["page-footer"]
    assert captured["config"] is cfg
    assert footer["center"] == "Developed by X."
    assert footer["left"] == "**Captain Hook** · hooks for Claude Code"
    assert "[PyPI](https://pypi.org/project/captain-hook/)" in footer["right"]
    assert "[Skills](skills.html)" in footer["right"]


def test_apply_fleet_footer_skips_config_without_website(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Fake(_FooterInstance):
        def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
            pass

    install_core(monkeypatch, _Fake)
    patches_mod.apply_fleet_footer()
    cfg = {"project": {"type": "website"}}
    _Fake()._write_quarto_yml("_quarto.yml", cfg)
    assert "website" not in cfg


def _stock_shaped_admonition_handler(self: object, el: object) -> object:
    return convert_rst_text(el.value.description)  # noqa: F821 — source text only; never executed


def _changed_admonition_handler(self: object, el: object) -> object:
    return "rendered differently now"


def test_is_pure_doctest_detects_prompt_only_blocks() -> None:
    assert patches_mod._is_pure_doctest(CUSTOM_CONDITION_DOCTEST)
    assert patches_mod._is_pure_doctest(">>> x = 1\n>>> x + 1")
    assert not patches_mod._is_pure_doctest("Some prose.\n\n>>> x = 1")
    assert not patches_mod._is_pure_doctest("Just prose, no doctest here.")
    assert not patches_mod._is_pure_doctest("   \n\n")


def test_probe_example_doctest_version_below_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.9")
    assert (
        patches_mod.probe_example_doctest()
        == "great-docs 0.14.9 is outside [0.15, 0.16)"
    )


def test_probe_example_doctest_real_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("great_docs")
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_example_doctest() is None


def test_probe_example_doctest_matches_synthetic_real_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    monkeypatch.setattr(
        patches_mod,
        "_render_doc_admonition_handler",
        lambda: (None, object(), _stock_shaped_admonition_handler),
    )
    assert patches_mod.probe_example_doctest() is None


def test_probe_example_doctest_self_retires_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    monkeypatch.setattr(
        patches_mod,
        "_render_doc_admonition_handler",
        lambda: (None, object(), _changed_admonition_handler),
    )
    assert (
        patches_mod.probe_example_doctest()
        == "RenderDoc admonition handler render shape changed"
    )


def test_probe_example_doctest_missing_singledispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    monkeypatch.setattr(
        patches_mod, "_render_doc_admonition_handler", lambda: (None, None, None)
    )
    assert (
        patches_mod.probe_example_doctest()
        == "RenderDoc.render_docstring_section singledispatch missing"
    )


def test_probe_example_doctest_missing_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    monkeypatch.setattr(
        patches_mod, "_render_doc_admonition_handler", lambda: (None, object(), None)
    )
    assert (
        patches_mod.probe_example_doctest()
        == "RenderDoc has no DocstringSectionAdmonition handler"
    )


def test_apply_example_doctest_fences_pure_doctest() -> None:
    pytest.importorskip("great_docs")
    import griffe as gf
    from great_docs._apiref._render import doc as doc_mod

    handler = patches_mod._example_admonition_renderer(doc_mod)
    el = gf.DocstringSectionAdmonition(
        kind="example", text=CUSTOM_CONDITION_DOCTEST, title="Example"
    )
    rendered = str(handler(types.SimpleNamespace(level=0), el))

    assert rendered.startswith("```python\n")
    assert rendered.endswith("\n```")
    assert ">>> class LargeFile(CustomCondition):" in rendered
    assert '>>> app.hook(Event.PreToolUse, only_if=[LargeFile()], message="Large file"' in rendered
    assert "“" not in rendered and "”" not in rendered  # straight quotes, no smart curls


def test_apply_example_doctest_note_admonition_stays_stock() -> None:
    pytest.importorskip("great_docs")
    import griffe as gf
    from great_docs._apiref._render import doc as doc_mod

    handler = patches_mod._example_admonition_renderer(doc_mod)
    el = gf.DocstringSectionAdmonition(
        kind="note", text="Be careful with ``paths``.", title="Note"
    )
    rendered = handler(types.SimpleNamespace(level=0), el)

    assert rendered == doc_mod.convert_rst_text("Be careful with ``paths``.")
    assert not isinstance(rendered, doc_mod.CodeBlock)


def test_apply_example_doctest_mixed_prose_uses_docstring_text() -> None:
    pytest.importorskip("great_docs")
    import griffe as gf
    from great_docs._apiref._render import doc as doc_mod

    handler = patches_mod._example_admonition_renderer(doc_mod)
    mixed = "First build a check:\n\n>>> x = 1\n>>> print(x)\n\nThen wire it up."
    el = gf.DocstringSectionAdmonition(kind="example", text=mixed, title="Example")
    rendered = handler(types.SimpleNamespace(level=0), el)

    assert isinstance(rendered, str)
    assert "```python" in rendered  # doctest still fenced
    assert "First build a check" in rendered and "Then wire it up" in rendered


def test_apply_example_doctest_registers_on_singledispatch() -> None:
    pytest.importorskip("great_docs")
    import griffe as gf

    _, sdm, original = patches_mod._render_doc_admonition_handler()
    try:
        patches_mod.apply_example_doctest()
        dispatched = sdm.dispatcher.dispatch(gf.DocstringSectionAdmonition)
        assert dispatched is not original
        el = gf.DocstringSectionAdmonition(
            kind="example", text='>>> print("x")', title="Example"
        )
        assert str(dispatched(types.SimpleNamespace(level=0), el)) == (
            '```python\n>>> print("x")\n```'
        )
    finally:
        sdm.register(gf.DocstringSectionAdmonition)(original)


def test_probe_homepage_demo_real_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("great_docs")
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_homepage_demo() is None


def test_probe_homepage_demo_matches_synthetic_real_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _HeroRealShape)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_homepage_demo() is None


def test_probe_homepage_demo_self_retires_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _HeroNeighbor)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_homepage_demo()
        == "GreatDocs._create_index_from_readme hero seam changed"
    )


def test_probe_homepage_demo_version_below_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _HeroRealShape)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.9")
    assert (
        patches_mod.probe_homepage_demo()
        == "great-docs 0.14.9 is outside [0.15, 0.16)"
    )


def test_probe_homepage_demo_missing_method(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Empty:
        pass

    install_core(monkeypatch, _Empty)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_homepage_demo() == "GreatDocs._build_hero_section missing"
    )


def _hero_instance(project_root: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(project_root=project_root)


def test_transform_demo_image_swaps_for_termshow(tmp_path) -> None:
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo.termshow").write_text("recording")
    body = 'Intro.\n\n<img src="docs/assets/demo.png" alt="Demo" width="700">\n\nMore.\n'
    out = patches_mod._transform_demo_image(_hero_instance(tmp_path), body)
    assert out == (
        'Intro.\n\n{{< termshow file="docs/assets/demo" autoplay="true" >}}\n\nMore.\n'
    )
    assert "docs/assets/demo.png" not in out


def test_transform_demo_image_matches_full_url_src(tmp_path) -> None:
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo.termshow").write_text("recording")
    body = (
        "Intro.\n\n"
        '<img src="https://github.com/yasyf/x/raw/main/docs/assets/demo.png" '
        'alt="Demo" width="700">\n\nMore.\n'
    )
    out = patches_mod._transform_demo_image(_hero_instance(tmp_path), body)
    assert '{{< termshow file="docs/assets/demo" autoplay="true" >}}' in out
    assert "demo.png" not in out


def test_transform_demo_image_drops_when_no_recording(tmp_path) -> None:
    body = 'Intro.\n\n<img src="docs/assets/demo.png" alt="Demo" width="700">\n\nMore.\n'
    out = patches_mod._transform_demo_image(_hero_instance(tmp_path), body)
    assert out == "Intro.\n\nMore.\n"
    assert "termshow" not in out


def test_transform_demo_image_drops_markdown_image(tmp_path) -> None:
    body = "Intro.\n\n![Demo](docs/assets/demo.webp)\n\nMore.\n"
    out = patches_mod._transform_demo_image(_hero_instance(tmp_path), body)
    assert out == "Intro.\n\nMore.\n"


def test_transform_demo_image_consumes_pandoc_attrs(tmp_path) -> None:
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo.termshow").write_text("recording")
    body = "Intro.\n\n![Demo](docs/assets/demo.png){width=700}\n\nMore.\n"
    out = patches_mod._transform_demo_image(_hero_instance(tmp_path), body)
    assert out == (
        'Intro.\n\n{{< termshow file="docs/assets/demo" autoplay="true" >}}\n\nMore.\n'
    )
    assert "width=700" not in out


def test_transform_demo_image_drops_empty_wrapper(tmp_path) -> None:
    body = (
        'Intro.\n\n<p align="center">\n'
        '  <img src="docs/assets/demo.gif" alt="Demo">\n'
        "</p>\n\nMore.\n"
    )
    out = patches_mod._transform_demo_image(_hero_instance(tmp_path), body)
    assert out == "Intro.\n\nMore.\n"
    assert "<p" not in out and "</p>" not in out


def test_transform_demo_image_no_image_is_noop(tmp_path) -> None:
    assert (
        patches_mod._transform_demo_image(
            _hero_instance(tmp_path), "# Title\n\nJust prose, no demo.\n"
        )
        is None
    )


def test_transform_demo_image_ignores_non_demo_screenshot(tmp_path) -> None:
    body = 'Intro.\n\n<img src="docs/assets/architecture.png" alt="Arch">\n\nMore.\n'
    assert patches_mod._transform_demo_image(_hero_instance(tmp_path), body) is None


def test_apply_homepage_demo_transforms_readme_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    body = 'Intro.\n\n<img src="docs/assets/demo.png" alt="Demo">\n\nMore.\n'

    class _Fake:
        project_root = tmp_path

        def _build_hero_section(
            self, readme_content: str | None = None
        ) -> tuple[str, str | None]:
            return "HERO", None

    install_core(monkeypatch, _Fake)
    patches_mod.apply_homepage_demo()
    hero, cleaned = _Fake()._build_hero_section(body)
    assert hero == "HERO"
    assert cleaned == "Intro.\n\nMore.\n"


def test_index_source_demo_keeps_generated_frontmatter_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from great_docs import core

    for name in (
        "_build_metadata_margin",
        "_create_index_from_readme",
        "_build_hero_section",
    ):
        monkeypatch.setattr(core.GreatDocs, name, getattr(core.GreatDocs, name))

    (tmp_path / "great-docs.yml").write_text(
        "module: sample\n"
        "display_name: Sample\n"
        "hero:\n"
        "  name: Sample\n"
        '  tagline: "A sample project."\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "sample"\n'
        'version = "1.0.0"\n'
        'description = "A sample project."\n'
        'requires-python = ">=3.12"\n'
    )
    (tmp_path / "sample").mkdir()
    (tmp_path / "sample" / "__init__.py").write_text("")
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo.png").write_bytes(b"png")
    (tmp_path / "docs" / "assets" / "demo.termshow").write_text("recording")
    (tmp_path / "index.qmd").write_text(
        "---\n"
        'title: "Hand-authored homepage"\n'
        "toc: false\n"
        'body-classes: "gd-homepage"\n'
        "---\n\n"
        "Intro.\n\n"
        "![Demo](docs/assets/demo.png)\n\n"
        "More.\n"
    )
    (tmp_path / "great-docs").mkdir()

    patches_mod.apply_metadata_margin_off()
    patches_mod.apply_homepage_demo()
    core.GreatDocs(str(tmp_path))._create_index_from_readme(force_rebuild=True)

    generated = (tmp_path / "great-docs" / "index.qmd").read_text()
    assert generated.splitlines()[:5] == [
        "---",
        'title: ""',
        "toc: false",
        'body-classes: "gd-homepage"',
        "---",
    ]
    assert patches_mod.TERMSHOW_SHORTCODE in generated
    assert "demo.png" not in generated


def test_apply_homepage_demo_leaves_blended_mode_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class _Fake:
        project_root = tmp_path

        def _build_hero_section(
            self, readme_content: str | None = None
        ) -> tuple[str, str | None]:
            return "HERO", None

    install_core(monkeypatch, _Fake)
    patches_mod.apply_homepage_demo()
    assert _Fake()._build_hero_section() == ("HERO", None)


def test_apply_homepage_demo_preserves_cleaning_without_demo(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class _Fake:
        project_root = tmp_path

        def _build_hero_section(
            self, readme_content: str | None = None
        ) -> tuple[str, str | None]:
            return "HERO", "CLEANED, NO DEMO IMAGE"

    install_core(monkeypatch, _Fake)
    patches_mod.apply_homepage_demo()
    hero, cleaned = _Fake()._build_hero_section("body without a demo image")
    assert (hero, cleaned) == ("HERO", "CLEANED, NO DEMO IMAGE")


def test_apply_homepage_demo_never_crashes_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Fake:
        # No project_root attribute → the transform raises; the wrapper must
        # swallow it and fall back to the stock hero cleaning.
        def _build_hero_section(
            self, readme_content: str | None = None
        ) -> tuple[str, str | None]:
            return "HERO", None

    install_core(monkeypatch, _Fake)
    patches_mod.apply_homepage_demo()
    body = 'Intro.\n\n<img src="docs/assets/demo.png" alt="Demo">\n\nMore.\n'
    assert _Fake()._build_hero_section(body) == ("HERO", None)


class _HeroLogoRealConfig:
    @property
    def hero_logo(self) -> object:
        hero: dict = {}
        val = hero.get("logo")
        return val


class _HeroLogoRealCore:
    def _build_hero_section(
        self, readme_content: str | None = None
    ) -> tuple[str, str | None]:
        logo_config = self._config.hero_logo  # noqa: F821, F841 — source text only; never executed
        if logo_config is None:
            logo_config = self._detect_hero_logo()  # noqa: F821, F841 — source text only; never executed
        return "", None


class _HeroLogoNeighborCore:
    def _build_hero_section(
        self, readme_content: str | None = None
    ) -> tuple[str, str | None]:
        logo_config = self._config.hero_logo  # noqa: F821, F841 — source text only; never executed
        return "", None


def install_hero_modules(
    monkeypatch: pytest.MonkeyPatch, config_cls: type, core_cls: type
) -> None:
    great_docs = types.ModuleType("great_docs")
    config_mod = types.ModuleType("great_docs.config")
    config_mod.Config = config_cls
    core_mod = types.ModuleType("great_docs.core")
    core_mod.GreatDocs = core_cls
    great_docs.config = config_mod
    great_docs.core = core_mod
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs.config", config_mod)
    monkeypatch.setitem(sys.modules, "great_docs.core", core_mod)


def test_probe_hero_logo_off_real_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("great_docs")
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_hero_logo_off() is None


def test_probe_hero_logo_off_matches_synthetic_real_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_hero_modules(monkeypatch, _HeroLogoRealConfig, _HeroLogoRealCore)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_hero_logo_off() is None


def test_probe_hero_logo_off_version_below_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.9")
    assert patches_mod.probe_hero_logo_off() == "great-docs 0.14.9 is outside [0.15, 0.16)"


def test_probe_hero_logo_off_self_retires_on_core_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_hero_modules(monkeypatch, _HeroLogoRealConfig, _HeroLogoNeighborCore)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_hero_logo_off()
        == "GreatDocs._build_hero_section hero-logo fallback shape changed"
    )


def test_probe_hero_logo_off_config_not_a_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Config:
        def hero_logo(self) -> object:  # a method, not a property
            return None

    install_hero_modules(monkeypatch, _Config, _HeroLogoRealCore)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_hero_logo_off() == "Config.hero_logo is not a property"


def test_apply_hero_logo_off_suppresses_unset_and_honors_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Config:
        def __init__(self, value: object) -> None:
            self._value = value

        @property
        def hero_logo(self) -> object:
            return self._value

    config_mod = types.ModuleType("great_docs.config")
    config_mod.Config = _Config
    great_docs = types.ModuleType("great_docs")
    great_docs.config = config_mod
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs.config", config_mod)

    patches_mod.apply_hero_logo_off()
    assert _Config(None).hero_logo is False  # unset → suppressed
    assert _Config("assets/logo.svg").hero_logo == "assets/logo.svg"  # explicit honored
    assert _Config(False).hero_logo is False  # explicit suppression preserved


class _FleetCssRealCore:
    def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
        header_comment = "# Generated\n"
        with open(quarto_yml, "w") as f:
            f.write(header_comment)
            write_yaml(config, f)  # noqa: F821 — source text only; never executed

    def _prepare_build_directory(self) -> None:
        shutil.copy2(css_src, self.project_path / css_src.name)  # noqa: F821 — source text only; never executed


def _make_fleet_css(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    css = tmp_path / "docs/assets/.gd-build/fleet-theme.css"
    css.parent.mkdir(parents=True)
    css.write_text("/* fleet */\n")
    return tmp_path


def test_probe_fleet_css_real_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("great_docs")
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_fleet_css() is None


def test_probe_fleet_css_matches_synthetic_real_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _FleetCssRealCore)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_fleet_css() is None


def test_probe_fleet_css_version_below_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.0")
    assert patches_mod.probe_fleet_css() == "great-docs 0.14.0 is outside [0.15, 0.16)"


def test_probe_fleet_css_self_retires_on_write_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_core(monkeypatch, _WriteNeighbor)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_fleet_css()
        == "GreatDocs._write_quarto_yml serialization shape changed"
    )


def test_probe_fleet_css_missing_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    install_core(monkeypatch, _WriteRealShape)  # has _write_quarto_yml, no _prepare_build_directory
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_fleet_css() == "GreatDocs._prepare_build_directory missing"


def test_merge_fleet_css_copies_and_appends_basename(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_fleet_css(tmp_path, monkeypatch)
    staging = root / "great-docs"
    staging.mkdir()
    inst = types.SimpleNamespace(project_path=staging)
    config = {"format": {"html": {"css": ["site.css"]}}}
    patches_mod._merge_fleet_css(inst, config)
    assert (staging / "fleet-theme.css").read_text() == "/* fleet */\n"
    assert config["format"]["html"]["css"] == ["site.css", "fleet-theme.css"]


def test_merge_fleet_css_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_fleet_css(tmp_path, monkeypatch)
    staging = root / "great-docs"
    staging.mkdir()
    inst = types.SimpleNamespace(project_path=staging)
    config = {"format": {"html": {}}}
    patches_mod._merge_fleet_css(inst, config)
    patches_mod._merge_fleet_css(inst, config)
    assert config["format"]["html"]["css"] == ["fleet-theme.css"]


def test_merge_fleet_css_wraps_scalar_css(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_fleet_css(tmp_path, monkeypatch)
    staging = root / "great-docs"
    staging.mkdir()
    inst = types.SimpleNamespace(project_path=staging)
    config = {"format": {"html": {"css": "site.css"}}}
    patches_mod._merge_fleet_css(inst, config)
    assert config["format"]["html"]["css"] == ["site.css", "fleet-theme.css"]


def test_merge_fleet_css_skips_config_without_format_html(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_fleet_css(tmp_path, monkeypatch)
    staging = root / "great-docs"
    staging.mkdir()
    inst = types.SimpleNamespace(project_path=staging)
    config = {"project": {"type": "website"}}
    patches_mod._merge_fleet_css(inst, config)
    assert "format" not in config
    assert not (staging / "fleet-theme.css").exists()  # no format.html → no copy


def test_merge_fleet_css_skips_when_css_source_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no materialized CSS on disk
    staging = tmp_path / "great-docs"
    staging.mkdir()
    inst = types.SimpleNamespace(project_path=staging)
    config = {"format": {"html": {}}}
    patches_mod._merge_fleet_css(inst, config)
    assert "css" not in config["format"]["html"]
    assert not (staging / "fleet-theme.css").exists()


def test_apply_fleet_css_wraps_and_merges(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_fleet_css(tmp_path, monkeypatch)
    staging = root / "great-docs"
    staging.mkdir()
    captured: dict = {}

    class _Fake:
        project_path = staging

        def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
            captured["config"] = config

    install_core(monkeypatch, _Fake)
    patches_mod.apply_fleet_css()
    cfg = {"format": {"html": {"css": ["site.css"]}}}
    _Fake()._write_quarto_yml(str(staging / "_quarto.yml"), cfg)
    assert captured["config"] is cfg
    assert cfg["format"]["html"]["css"] == ["site.css", "fleet-theme.css"]
    assert (staging / "fleet-theme.css").read_text() == "/* fleet */\n"


def test_apply_fleet_css_never_crashes_the_build(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_fleet_css(tmp_path, monkeypatch)

    class _Fake:
        # No project_path → the copy raises; the wrapper must swallow it and
        # still delegate to the stock _write_quarto_yml.
        def _write_quarto_yml(self, quarto_yml: object, config: dict) -> None:
            config["written"] = True

    install_core(monkeypatch, _Fake)
    patches_mod.apply_fleet_css()
    cfg = {"format": {"html": {}}}
    _Fake()._write_quarto_yml("_quarto.yml", cfg)
    assert cfg["written"] is True
