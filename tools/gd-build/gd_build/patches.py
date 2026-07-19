"""Version-gated great-docs performance monkeypatches.

Each patch is gated on the exact great-docs / griffe internals it rebinds and
degrades to a stock build (never a failure) when its gate does not hold. The
patches are applied by `gd_build.cli` before it delegates to `great-docs build`;
importing this module patches nothing — every effect lives under `apply_patches`,
so the pre_render scripts great-docs runs in Quarto subprocesses never see a
patched interpreter.

Disable with `GD_BUILD_PATCHES=none`, or select a subset with a comma-separated
list of patch names (default `all`).
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version


@dataclass(frozen=True, slots=True)
class Patch:
    name: str
    verified_window: str
    probe: Callable[[], str | None]
    apply: Callable[[], None]
    expected_savings: str
    upstream_ref: str


def probe_shared_loader() -> str | None:
    from great_docs._apiref import introspect

    dist = version("great-docs")
    if not (m := re.match(r"(\d+)\.(\d+)", dist)) or (int(m[1]), int(m[2])) != (0, 15):
        return f"great-docs {dist} is outside [0.15, 0.16)"
    if not hasattr(introspect, "make_loader"):
        return "introspect.make_loader missing (upstream fix absent)"
    if "loader" not in inspect.signature(introspect.get_object).parameters:
        return "introspect.get_object has no loader parameter"
    return None


def apply_shared_loader() -> None:
    from great_docs._apiref import introspect

    original = introspect.get_object
    signature = inspect.signature(original)
    loaders: dict[object, object] = {}

    @functools.wraps(original)
    def get_object(*args: object, **kwargs: object) -> object:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        if bound.arguments["loader"] is None:
            parser = bound.arguments["parser"]
            if parser not in loaders:
                loaders[parser] = introspect.make_loader(parser)
            bound.arguments["loader"] = loaders[parser]
        return original(*bound.args, **bound.kwargs)

    introspect.get_object = get_object


def probe_gitinfo_cache() -> str | None:
    from griffe._internal import git

    if not hasattr(git, "GitInfo"):
        return "griffe.GitInfo missing"
    if not isinstance(git.GitInfo.__dict__.get("from_package"), classmethod):
        return "griffe.GitInfo.from_package is not a classmethod (layout changed)"
    return None


def apply_gitinfo_cache() -> None:
    from griffe._internal import git

    original = git.GitInfo.__dict__["from_package"].__func__
    cache: dict[object, object] = {}

    def from_package(cls: type, package: object) -> object:
        if package not in cache:
            cache[package] = original(cls, package)
        return cache[package]

    git.GitInfo.from_package = classmethod(from_package)


def probe_grouped_reference() -> str | None:
    dist = version("great-docs")
    if not (m := re.match(r"(\d+)\.(\d+)", dist)) or (int(m[1]), int(m[2])) != (0, 15):
        return f"great-docs {dist} is outside [0.15, 0.16)"

    import great_docs.core as core
    from great_docs._apiref import _format, api_reference, content
    from great_docs._apiref._render import (
        RenderAPIPage,
        RenderDocAttribute,
        RenderDocClass,
        RenderDocFunction,
        RenderDocModule,
    )
    from great_docs._apiref.pandoc import inlines
    from great_docs._apiref.pandoc.blocks import Header
    from great_docs._apiref.pandoc.components import Attr
    from great_docs.config import Config
    from great_docs.core import GreatDocs

    if not callable(getattr(api_reference, "resolve", None)):
        return "api_reference.resolve is not the rebindable resolve global"
    build = api_reference.APIReference.build
    if "page_filter" not in inspect.signature(build).parameters:
        return "APIReference.build signature changed (no page_filter)"
    if "resolve(" not in inspect.getsource(build):
        return "APIReference.build no longer calls resolve() (rebind would miss)"

    if not {"path", "flatten", "contents"} <= {f.name for f in dataclasses.fields(content.Page)}:
        return "content.Page fields changed"
    if not hasattr(content.Section, "replace") or "title" not in {
        f.name for f in dataclasses.fields(content.Section)
    }:
        return "content.Section shape changed"
    if "anchor" not in {f.name for f in dataclasses.fields(content.Doc)}:
        return "content.Doc has no anchor field"

    for cls in (RenderDocClass, RenderDocFunction, RenderDocAttribute, RenderDocModule):
        if not {"page_path", "contained", "level"} <= {f.name for f in dataclasses.fields(cls)}:
            return f"{cls.__name__} render fields changed"
        if not callable(getattr(cls, "render_title", None)):
            return f"{cls.__name__}.render_title missing"
        if not callable(getattr(cls, "render_summary", None)):
            return f"{cls.__name__}.render_summary missing"
    if not callable(getattr(RenderAPIPage, "render_metadata", None)):
        return "RenderAPIPage.render_metadata missing"
    if not isinstance(getattr(RenderAPIPage, "_has_one_object", None), property):
        return "RenderAPIPage._has_one_object is not a property"

    if "attr" not in {f.name for f in dataclasses.fields(Header)}:
        return "pandoc Header has no attr field"
    if "identifier" not in {f.name for f in dataclasses.fields(Attr)}:
        return "pandoc Attr has no identifier field"
    if not callable(getattr(inlines, "Link", None)):
        return "pandoc.inlines.Link missing"
    if not callable(getattr(_format, "markdown_escape", None)):
        return "_apiref._format.markdown_escape missing"

    if not callable(getattr(Config, "should_split_methods", None)):
        return "config.Config.should_split_methods missing (big-class splitting gate)"
    if not callable(getattr(GreatDocs, "_update_sidebar_from_sections", None)):
        return "GreatDocs._update_sidebar_from_sections missing"
    if not callable(getattr(GreatDocs, "_write_quarto_yml", None)):
        return "GreatDocs._write_quarto_yml missing"
    if not callable(getattr(core, "read_yaml", None)):
        return "great_docs.core.read_yaml missing"
    return None


def apply_grouped_reference() -> None:
    from gd_build import grouped_reference

    grouped_reference.install()


def patches() -> tuple[Patch, ...]:
    return (
        Patch(
            name="shared-griffe-loader",
            verified_window="great-docs >=0.15,<0.16 with introspect.make_loader and get_object(loader=)",
            probe=probe_shared_loader,
            apply=apply_shared_loader,
            expected_savings="~5.5 min/build (API discovery 331.75s -> 2.32s)",
            upstream_ref="great_docs get_object(loader=) shared per-parser loader",
        ),
        Patch(
            name="griffe-gitinfo-cache",
            verified_window="griffe._internal.git.GitInfo.from_package classmethod",
            probe=probe_gitinfo_cache,
            apply=apply_gitinfo_cache,
            expected_savings="~71% of discovery git-subprocess cost",
            upstream_ref="griffe GitInfo.from_package per-package memoization",
        ),
        Patch(
            name="grouped-reference-pages",
            verified_window=(
                "great-docs >=0.15,<0.16 with api_reference.resolve global, "
                "APIReference.build(page_filter=), content.Page/Section/Doc shape, "
                "RenderDoc*/RenderAPIPage render hooks, pandoc Header.attr/Attr.identifier, "
                "GreatDocs._update_sidebar_from_sections + _write_quarto_yml"
            ),
            probe=probe_grouped_reference,
            apply=apply_grouped_reference,
            expected_savings="~184 per-symbol reference pages -> ~12 anchored group pages",
            upstream_ref="great_docs grouped anchored reference pages (resolve-tree collapse)",
        ),
    )


def selected_patches() -> list[tuple[Patch | None, str]]:
    registry = {patch.name: patch for patch in patches()}
    match (os.environ.get("GD_BUILD_PATCHES") or "all").strip():
        case "none":
            return []
        case "all":
            return [(patch, name) for name, patch in registry.items()]
        case names:
            return [(registry.get(n), n) for n in filter(None, (part.strip() for part in names.split(",")))]


def skip_reason(patch: Patch | None) -> str | None:
    if patch is None:
        return "unknown patch name"
    try:
        if (reason := patch.probe()) is not None:
            return reason
        patch.apply()
        return None
    except Exception as exc:  # Patch isolation: any failure degrades to a stock build, never breaking docs.
        return f"{type(exc).__name__}: {exc}"


def emit(name: str, reason: str | None) -> None:
    if reason is None:
        print(f"PATCHED: {name}", file=sys.stderr)
        return
    line = f"UNPATCHED: {name} — running STOCK ({reason})"
    print(line, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::{line}")


def apply_patches() -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for patch, name in selected_patches():
        reason = skip_reason(patch)
        emit(name, reason)
        outcomes[name] = reason is None
    return outcomes
