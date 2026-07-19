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

import functools
import inspect
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version

MARGIN_EMPTY_RETURN = 'return "\\n".join(margin_sections) if margin_sections else ""'
ADMONITION_STOCK_RETURN = "return convert_rst_text(el.value.description)"
DOCTEST_LINE_RE = re.compile(r"^\s*(?:>>>|\.\.\.)")


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


def _within_window() -> str | None:
    dist = version("great-docs")
    if not (m := re.match(r"(\d+)\.(\d+)", dist)) or (int(m[1]), int(m[2])) != (0, 15):
        return f"great-docs {dist} is outside [0.15, 0.16)"
    return None


def probe_metadata_margin_off() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    from great_docs import core

    fn = getattr(core.GreatDocs, "_build_metadata_margin", None)
    if fn is None:
        return "GreatDocs._build_metadata_margin missing"
    if MARGIN_EMPTY_RETURN not in inspect.getsource(fn):
        return "GreatDocs._build_metadata_margin return shape changed"
    return None


def apply_metadata_margin_off() -> None:
    from great_docs import core

    original = core.GreatDocs._build_metadata_margin

    @functools.wraps(original)
    def _build_metadata_margin(self: object) -> str:
        # Keep the side effects (contributing/roadmap/security .qmd pages); drop the sidebar markup.
        original(self)
        return ""

    core.GreatDocs._build_metadata_margin = _build_metadata_margin


def _footer_colophon(instance: object, metadata: dict) -> str:
    name = instance._config.display_name or instance._detect_package_name() or ""
    tagline = instance._config.hero_tagline
    if tagline is None:
        tagline = metadata.get("description", "")
    name_md = f"**{name}**" if name else ""
    if name_md and tagline:
        return f"{name_md} · {tagline}"
    return name_md or tagline or ""


def _footer_links(instance: object) -> str:
    parts: list[str] = []
    pypi = instance._config.pypi
    # Mirror _build_metadata_margin: only an explicit False disables the PyPI link.
    if pypi is not False:
        package_name = instance._detect_package_name()
        pypi_url = (
            pypi
            if isinstance(pypi, str)
            else (f"https://pypi.org/project/{package_name}/" if package_name else None)
        )
        if pypi_url:
            parts.append(f"[PyPI]({pypi_url})")
    _owner, _repo, base_url = instance._get_github_repo_info()
    if base_url:
        parts.append(f"[Source]({base_url})")
        parts.append(f"[Issues]({base_url}/issues)")
        parts.append(f"[Changelog]({base_url}/blob/main/CHANGELOG.md)")
    parts.append("[llms.txt](llms.txt)")
    parts.append("[llms-full.txt](llms-full.txt)")
    if instance._config.skill_enabled:
        parts.append("[Skills](skills.html)")
    return " · ".join(parts)


def merge_page_footer(website: dict, left: str, right: str) -> None:
    footer = website.get("page-footer")
    if not isinstance(footer, dict):
        footer = {"center": footer} if footer else {}
    if left:
        footer.setdefault("left", left)
    if right:
        footer.setdefault("right", right)
    if footer:
        website["page-footer"] = footer


def probe_fleet_footer() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    from great_docs import core

    fn = getattr(core.GreatDocs, "_write_quarto_yml", None)
    if fn is None:
        return "GreatDocs._write_quarto_yml missing"
    src = inspect.getsource(fn)
    if "f.write(header_comment)" not in src or "write_yaml(config, f)" not in src:
        return "GreatDocs._write_quarto_yml serialization shape changed"
    return None


def apply_fleet_footer() -> None:
    from great_docs import core

    original = core.GreatDocs._write_quarto_yml

    @functools.wraps(original)
    def _write_quarto_yml(self: object, quarto_yml: object, config: dict) -> object:
        website = config.get("website")
        if isinstance(website, dict):
            metadata = self._get_package_metadata()
            merge_page_footer(
                website, _footer_colophon(self, metadata), _footer_links(self)
            )
        return original(self, quarto_yml, config)

    core.GreatDocs._write_quarto_yml = _write_quarto_yml


def _is_pure_doctest(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(DOCTEST_LINE_RE.match(line) for line in lines)


def _render_doc_admonition_handler() -> tuple[object, object, object]:
    import griffe as gf
    from great_docs._apiref._render import doc as doc_mod

    for cls in doc_mod.RenderDoc.__mro__:
        sdm = cls.__dict__.get("render_docstring_section")
        if sdm is not None:
            handler = sdm.dispatcher.registry.get(gf.DocstringSectionAdmonition)
            return doc_mod, sdm, handler
    return doc_mod, None, None


def _example_admonition_renderer(doc_mod: object) -> Callable[[object, object], object]:
    def render_admonition(self: object, el: object) -> object:
        # Google "Example:" sections parse as admonitions; griffe never fences their
        # doctests, so stock convert_rst_text flattens them and Pandoc curls the quotes.
        if (el.title or "").lower().startswith("example"):
            description = el.value.description
            if _is_pure_doctest(description):
                return doc_mod.CodeBlock(description, doc_mod.Attr(classes=["python"]))
            return doc_mod.convert_docstring_text(description, heading_level=self.level + 1)
        return doc_mod.convert_rst_text(el.value.description)

    return render_admonition


def probe_example_doctest() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    _, sdm, handler = _render_doc_admonition_handler()
    if sdm is None:
        return "RenderDoc.render_docstring_section singledispatch missing"
    if handler is None:
        return "RenderDoc has no DocstringSectionAdmonition handler"
    if ADMONITION_STOCK_RETURN not in inspect.getsource(handler):
        return "RenderDoc admonition handler render shape changed"
    return None


def apply_example_doctest() -> None:
    import griffe as gf

    doc_mod, sdm, _ = _render_doc_admonition_handler()
    sdm.register(gf.DocstringSectionAdmonition)(_example_admonition_renderer(doc_mod))


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
            name="metadata-margin-off",
            verified_window="great-docs >=0.15,<0.16 with GreatDocs._build_metadata_margin empty-margin return",
            probe=probe_metadata_margin_off,
            apply=apply_metadata_margin_off,
            expected_savings="full-width homepage (sidebar dropped; links relocated to fleet footer)",
            upstream_ref="great_docs GreatDocs._build_metadata_margin returns '' (page-creation side effects kept)",
        ),
        Patch(
            name="fleet-footer",
            verified_window="great-docs >=0.15,<0.16 with GreatDocs._write_quarto_yml header+write_yaml serialization",
            probe=probe_fleet_footer,
            apply=apply_fleet_footer,
            expected_savings="compact site footer merged into the author/funding page-footer",
            upstream_ref="great_docs GreatDocs._write_quarto_yml merges left colophon + right links",
        ),
        Patch(
            name="example-doctest-fence",
            verified_window="great-docs >=0.15,<0.16 with RenderDoc admonition handler convert_rst_text(el.value.description)",
            probe=probe_example_doctest,
            apply=apply_example_doctest,
            expected_savings="Example: doctest admonitions fenced as python (straight quotes) instead of flattened prose",
            upstream_ref="great_docs RenderDoc.render_docstring_section example-admonition doctest fencing",
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
            return [
                (registry.get(n), n)
                for n in filter(None, (part.strip() for part in names.split(",")))
            ]


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
