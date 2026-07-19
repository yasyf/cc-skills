from __future__ import annotations

import sys
import types
from collections.abc import Callable

import pytest

from gd_build import patches as patches_mod


def make_patch(
    probe: Callable[[], str | None], apply: Callable[[], None]
) -> patches_mod.Patch:
    return patches_mod.Patch(
        name="fake",
        verified_window="",
        probe=probe,
        apply=apply,
        expected_savings="",
        upstream_ref="",
    )


def install_introspect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    make_loader: bool = True,
    loader_param: bool = True,
) -> types.ModuleType:
    introspect = types.ModuleType("great_docs._apiref.introspect")
    if make_loader:
        introspect.make_loader = lambda parser: f"loader:{parser}"
    if loader_param:

        def get_object(
            path: object, parser: object, loader: object = None
        ) -> dict[str, object]:
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


def install_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_gitinfo: bool = True,
    classmethod_fp: bool = True,
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
                ("metadata-margin-off", True),
                ("fleet-footer", True),
                ("example-doctest-fence", True),
            ],
            id="unset-defaults-to-all",
        ),
        pytest.param(
            "all",
            [
                ("shared-griffe-loader", True),
                ("griffe-gitinfo-cache", True),
                ("metadata-margin-off", True),
                ("fleet-footer", True),
                ("example-doctest-fence", True),
            ],
            id="all",
        ),
        pytest.param("none", [], id="none-selects-nothing"),
        pytest.param(
            "shared-griffe-loader", [("shared-griffe-loader", True)], id="single-csv"
        ),
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
    assert [
        (name, patch is not None) for patch, name in patches_mod.selected_patches()
    ] == expected


def test_skip_reason_unknown_patch() -> None:
    assert patches_mod.skip_reason(None) == "unknown patch name"


def test_skip_reason_probe_returns_reason_and_skips_apply() -> None:
    applied: list[str] = []
    reason = patches_mod.skip_reason(
        make_patch(lambda: "gate closed", lambda: applied.append("apply"))
    )
    assert reason == "gate closed"
    assert applied == []


def test_skip_reason_probe_raises_is_isolated() -> None:
    def probe() -> str | None:
        raise RuntimeError("boom")

    assert (
        patches_mod.skip_reason(make_patch(probe, lambda: None)) == "RuntimeError: boom"
    )


def test_skip_reason_apply_raises_is_isolated() -> None:
    def apply() -> None:
        raise ValueError("nope")

    assert (
        patches_mod.skip_reason(make_patch(lambda: None, apply)) == "ValueError: nope"
    )


def test_skip_reason_success_returns_none_and_applies() -> None:
    applied: list[bool] = []
    reason = patches_mod.skip_reason(
        make_patch(lambda: None, lambda: applied.append(True))
    )
    assert reason is None
    assert applied == [True]


def test_emit_patched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert (
        out.err
        == "UNPATCHED: griffe-gitinfo-cache — running STOCK (griffe.GitInfo missing)\n"
    )
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


def test_probe_shared_loader_version_below_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_introspect(monkeypatch)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.1")
    assert (
        patches_mod.probe_shared_loader() == "great-docs 0.14.1 is outside [0.15, 0.16)"
    )


def test_probe_shared_loader_missing_make_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_introspect(monkeypatch, make_loader=False)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.2")
    assert (
        patches_mod.probe_shared_loader()
        == "introspect.make_loader missing (upstream fix absent)"
    )


def test_probe_shared_loader_no_loader_param(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch, loader_param=False)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert (
        patches_mod.probe_shared_loader()
        == "introspect.get_object has no loader parameter"
    )


def test_apply_shared_loader_shares_loader_per_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_apply_gitinfo_cache_memoizes_per_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


# ── metadata-margin-off ──────────────────────────────────────────────────


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

    install_core(monkeypatch, _SideEffect)
    patches_mod.apply_metadata_margin_off()
    inst = _SideEffect()
    assert inst._build_metadata_margin() == ""
    assert inst.calls == ["side-effect"]


# ── fleet-footer ─────────────────────────────────────────────────────────


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


# ── example-doctest-fence ────────────────────────────────────────────────

CUSTOM_CONDITION_DOCTEST = (
    ">>> class LargeFile(CustomCondition):\n"
    "...     def check(self, evt: BaseHookEvent) -> bool:\n"
    "...         return bool(evt.file and evt.file.path.stat().st_size > 1_000_000)\n"
    "...\n"
    '>>> app.hook(Event.PreToolUse, only_if=[LargeFile()], message="Large file", block=True)'
)


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
