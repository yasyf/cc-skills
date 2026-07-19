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
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version

MARGIN_EMPTY_RETURN = 'return "\\n".join(margin_sections) if margin_sections else ""'
ADMONITION_STOCK_RETURN = "return convert_rst_text(el.value.description)"
DOCTEST_LINE_RE = re.compile(r"^\s*(?:>>>|\.\.\.)")
RENDER_ANNOTATION_RETURN = "return pretty_code(str(_render(annotation)))"
RENDER_ANNOTATION_INITVAR = 'ann.canonical_name == "InitVar"'
SIGNATURE_ANNOTATION_FSTRING = 'f": {el.annotation}"'
MANIFEST_ITEM_NAME = "InventoryItem(name=name_path"
HERO_SEAM_CALL = "hero_html, cleaned_content = self._build_hero_section(readme_content)"
HERO_SEAM_APPLY = "readme_content = cleaned_content"
HERO_LOGO_READ = "logo_config = self._config.hero_logo"
HERO_LOGO_FALLBACK = "logo_config = self._detect_hero_logo()"
HERO_LOGO_PROP_SRC = 'val = hero.get("logo")'
CSS_STAGING_COPY = "shutil.copy2(css_src, self.project_path / css_src.name)"

# Fleet-standard homepage demo assets: a raster screenshot swapped for a termshow
# recording (docs/assets/demo.termshow), rendered via the great-docs shortcode.
TERMSHOW_SHORTCODE = '{{< termshow file="docs/assets/demo" autoplay="true" >}}'
DEMO_SRC = r"docs/assets/demo\.(?:png|gif|webp)"
DEMO_IMG_RE = re.compile(
    r"<img\b[^>]*?\ssrc\s*=\s*[\"'][^\"']*" + DEMO_SRC + r"[^\"']*[\"'][^>]*>"
    r"|!\[[^\]]*\]\([^)]*" + DEMO_SRC + r"[^)]*\)"
)
# A tight demo wrapper: the image is the element's only child (only whitespace between).
WRAP_OPEN_RE = re.compile(r"(?P<open><(?P<tag>p|figure|div)\b[^>]*>)\s*\Z", re.IGNORECASE)
WRAP_CLOSE_RE = re.compile(r"\A\s*(?P<close></(?P<tag>p|figure|div)>)", re.IGNORECASE)

# Manifest object names captured pre-render; linkify only tokens that resolve here.
DOCUMENTED_NAMES: set[str] = set()


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


def format_annotation(ann: object, *, documented: set[str], mode: str) -> str:
    """Render a griffe annotation expression to great-docs markup.

    Two layers over griffe's source form: modernize (`Optional[X]` -> `X | None`,
    `Union[A, B]` -> `A | B`, `typing.` prefix stripped) always; and, for
    `mode == "annotation"`, linkify — a name token whose canonical path is a
    documented object becomes a great-docs interlink (``[](`~pkg.Name`)``) that
    post-render resolves to a reference-page link. `mode == "signature"` renders
    bare, link-free source for the fenced `python` block (pandoc never processes
    link markup there).
    """
    import griffe as gf
    from great_docs._apiref._format import markdown_escape, repr_obj
    from great_docs._apiref.pandoc.inlines import InterLink

    def members(sl: object) -> list[object]:
        return list(sl.elements) if isinstance(sl, gf.ExprTuple) else [sl]

    def render(a: object) -> str:
        if a is None:
            return ""
        if isinstance(a, str):
            return a if mode == "signature" else repr_obj(a)
        if isinstance(a, gf.ExprName):
            if mode == "annotation" and a.canonical_path in documented:
                return str(InterLink(target=f"~{a.canonical_path}"))
            return a.name if mode == "signature" else markdown_escape(a.name)
        assert isinstance(a, gf.Expr)
        if isinstance(a, gf.ExprSubscript):
            if a.canonical_name == "InitVar":  # mirror stock doc.py InitVar unwrap
                return render(a.slice)
            if a.canonical_path == "typing.Optional":
                return f"{render(a.slice)} | None"
            if a.canonical_path == "typing.Union":
                return " | ".join(render(m) for m in members(a.slice))
        if isinstance(a, gf.ExprAttribute) and a.canonical_path.startswith("typing."):
            return a.canonical_name
        path = a.canonical_path
        if path and path[0] == "~":
            return a.canonical_name
        return "".join(render(x) for x in a)

    return render(ann)


def probe_type_annotations() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    import griffe as gf
    from great_docs._apiref import collect
    from great_docs._apiref._render import doc as doc_mod
    from great_docs._apiref._render import mixin_call
    from great_docs._apiref.pandoc.inlines import InterLink

    for cls in ("ExprName", "ExprSubscript", "ExprTuple", "ExprAttribute"):
        if not hasattr(gf, cls):
            return f"griffe.{cls} missing"
    ra_src = inspect.getsource(doc_mod.RenderDoc.render_annotation)
    if RENDER_ANNOTATION_RETURN not in ra_src or RENDER_ANNOTATION_INITVAR not in ra_src:
        return "RenderDoc.render_annotation shape changed"
    rsp_src = inspect.getsource(mixin_call.RenderDocCallMixin.render_signature_parameter)
    if SIGNATURE_ANNOTATION_FSTRING not in rsp_src:
        return "render_signature_parameter annotation f-string changed"
    if not callable(getattr(collect, "build_manifest", None)):
        return "collect.build_manifest missing"
    if MANIFEST_ITEM_NAME not in inspect.getsource(collect._ManifestBuilder):
        return "manifest inventory-item name shape changed"
    if str(InterLink(target="~x.y")) != "[](`~x.y`)":
        return "InterLink interlink markup shape changed"
    return None


def apply_type_annotations() -> None:
    from great_docs._apiref import collect
    from great_docs._apiref._format import pretty_code
    from great_docs._apiref._render import doc as doc_mod
    from great_docs._apiref._render import mixin_call

    import griffe as gf

    original_bm = collect.build_manifest

    @functools.wraps(original_bm)
    def build_manifest(*args: object, **kwargs: object) -> object:
        manifest = original_bm(*args, **kwargs)
        DOCUMENTED_NAMES.clear()
        DOCUMENTED_NAMES.update(item.name for item in manifest.items if item.name)
        return manifest

    collect.build_manifest = build_manifest

    original_ra = doc_mod.RenderDoc.render_annotation

    @functools.wraps(original_ra)
    def render_annotation(self: object, annotation: object = None) -> str:
        if annotation is None:
            if not (
                isinstance(self.obj, gf.Attribute)
                or (isinstance(self.obj, gf.Alias) and self.obj.is_attribute)
            ):
                msg = f"Cannot render annotation for type {type(self.obj)}."
                raise TypeError(msg)
            annotation = self.obj.annotation
        try:
            rendered = format_annotation(
                annotation, documented=DOCUMENTED_NAMES, mode="annotation"
            )
            return pretty_code(str(rendered))
        except Exception:  # A patch must never crash a build; fall back to stock render.
            return original_ra(self, annotation)

    doc_mod.RenderDoc.render_annotation = render_annotation

    original_rsp = mixin_call.RenderDocCallMixin.render_signature_parameter

    @functools.wraps(original_rsp)
    def render_signature_parameter(self: object, el: object) -> str:
        result = original_rsp(self, el)
        if self.show_signature_annotation and el.annotation is not None:
            try:
                modern = format_annotation(
                    el.annotation, documented=DOCUMENTED_NAMES, mode="signature"
                )
            except Exception:  # Keep the stock signature rather than crash the build.
                return result
            result = result.replace(f": {el.annotation}", f": {modern}", 1)
        return result

    mixin_call.RenderDocCallMixin.render_signature_parameter = render_signature_parameter


def _find_demo_span(content: str) -> tuple[int, int, bool] | None:
    """Locate the homepage demo image in `content`.

    Returns `(start, end, standalone)` spanning the demo `<img>`/markdown image —
    widened to a tight `<p>`/`<figure>`/`<div align>` wrapper when the image is
    that element's only child, and to whole lines when the unit sits alone on its
    line(s). `standalone` reports that whole-line case. `None` when no demo image.
    """
    m = DEMO_IMG_RE.search(content)
    if m is None:
        return None
    start, end = m.start(), m.end()
    open_m = WRAP_OPEN_RE.search(content[:start])
    close_m = WRAP_CLOSE_RE.match(content[end:])
    if (
        open_m is not None
        and close_m is not None
        and open_m["tag"].lower() == close_m["tag"].lower()
        and (open_m["tag"].lower() != "div" or "align" in open_m["open"].lower())
    ):
        start = open_m.start("open")
        end += close_m.end("close")
    line_start = content.rfind("\n", 0, start) + 1
    nl = content.find("\n", end)
    line_end = len(content) if nl == -1 else nl
    if not content[line_start:start].strip() and not content[end:line_end].strip():
        return line_start, (line_end if nl == -1 else nl + 1), True
    return start, end, False


def _transform_demo_image(instance: object, content: str) -> str | None:
    """Swap the homepage demo screenshot for a termshow shortcode, or drop it.

    Returns the rewritten body, or `None` when the body carries no demo image (so
    the stock hero cleaning is left untouched). The demo screenshot is replaced by
    a `termshow` shortcode when `docs/assets/demo.termshow` exists at the project
    root, and otherwise removed — the README's fenced quickstart blocks carry the
    page. READMEs on disk are never edited; only the generated index sees this.
    """
    span = _find_demo_span(content)
    if span is None:
        return None
    start, end, standalone = span
    termshow = instance.project_root / "docs" / "assets" / "demo.termshow"
    if termshow.is_file():
        replacement = TERMSHOW_SHORTCODE + ("\n" if standalone else "")
    else:
        replacement = ""
    rewritten = content[:start] + replacement + content[end:]
    return re.sub(r"\n{3,}", "\n\n", rewritten)


def probe_homepage_demo() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    from great_docs import core

    hero = getattr(core.GreatDocs, "_build_hero_section", None)
    if hero is None:
        return "GreatDocs._build_hero_section missing"
    if "readme_content" not in inspect.signature(hero).parameters:
        return "GreatDocs._build_hero_section readme_content parameter missing"
    caller = getattr(core.GreatDocs, "_create_index_from_readme", None)
    if caller is None:
        return "GreatDocs._create_index_from_readme missing"
    src = inspect.getsource(caller)
    if HERO_SEAM_CALL not in src or HERO_SEAM_APPLY not in src:
        return "GreatDocs._create_index_from_readme hero seam changed"
    return None


def apply_homepage_demo() -> None:
    from great_docs import core

    original = core.GreatDocs._build_hero_section

    @functools.wraps(original)
    def _build_hero_section(
        self: object, readme_content: str | None = None
    ) -> tuple[str, str | None]:
        hero_html, cleaned_content = original(self, readme_content)
        if not readme_content:  # blended-mode call carries no body — leave it stock.
            return hero_html, cleaned_content
        try:
            base = cleaned_content if cleaned_content is not None else readme_content
            transformed = _transform_demo_image(self, base)
        except Exception:  # A patch must never crash a build; fall back to stock cleaning.
            return hero_html, cleaned_content
        if transformed is None:
            return hero_html, cleaned_content
        return hero_html, transformed

    core.GreatDocs._build_hero_section = _build_hero_section


def probe_hero_logo_off() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    from great_docs import config, core

    prop = config.Config.__dict__.get("hero_logo")
    if not isinstance(prop, property):
        return "Config.hero_logo is not a property"
    if HERO_LOGO_PROP_SRC not in inspect.getsource(prop.fget):
        return "Config.hero_logo return shape changed"
    hero = getattr(core.GreatDocs, "_build_hero_section", None)
    if hero is None:
        return "GreatDocs._build_hero_section missing"
    src = inspect.getsource(hero)
    if HERO_LOGO_READ not in src or HERO_LOGO_FALLBACK not in src:
        return "GreatDocs._build_hero_section hero-logo fallback shape changed"
    return None


def apply_hero_logo_off() -> None:
    from great_docs import config

    original = config.Config.__dict__["hero_logo"].fget

    @functools.wraps(original)
    def hero_logo(self: object) -> object:
        # Unset hero.logo suppresses the mascot fallback chain; explicit values win.
        value = original(self)
        return False if value is None else value

    config.Config.hero_logo = property(hero_logo)


def _merge_fleet_css(instance: object, config: dict) -> None:
    from gd_build import fleet_assets

    fmt = config.get("format")
    html = fmt.get("html") if isinstance(fmt, dict) else None
    if not isinstance(html, dict):
        return
    css_src = fleet_assets.CSS_DEST
    if not css_src.is_file():
        return
    dest = instance.project_path / css_src.name
    if not dest.exists():
        shutil.copy2(css_src, dest)
    existing = html.get("css")
    if isinstance(existing, str):
        css_list = [existing]
    elif isinstance(existing, list):
        css_list = list(existing)
    else:
        css_list = []
    if css_src.name not in css_list:
        css_list.append(css_src.name)
    html["css"] = css_list


def probe_fleet_css() -> str | None:
    if (reason := _within_window()) is not None:
        return reason
    from great_docs import core

    wq = getattr(core.GreatDocs, "_write_quarto_yml", None)
    if wq is None:
        return "GreatDocs._write_quarto_yml missing"
    if "write_yaml(config, f)" not in inspect.getsource(wq):
        return "GreatDocs._write_quarto_yml serialization shape changed"
    prep = getattr(core.GreatDocs, "_prepare_build_directory", None)
    if prep is None:
        return "GreatDocs._prepare_build_directory missing"
    if CSS_STAGING_COPY not in inspect.getsource(prep):
        return "GreatDocs CSS staging-copy shape changed (project_path basename copy)"
    return None


def apply_fleet_css() -> None:
    from great_docs import core

    original = core.GreatDocs._write_quarto_yml

    @functools.wraps(original)
    def _write_quarto_yml(self: object, quarto_yml: object, config: dict) -> object:
        try:
            _merge_fleet_css(self, config)
        except Exception:  # A patch must never crash a build; fall back to stock config.
            pass
        return original(self, quarto_yml, config)

    core.GreatDocs._write_quarto_yml = _write_quarto_yml


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
        Patch(
            name="type-annotations",
            verified_window="great-docs >=0.15,<0.16 with RenderDoc.render_annotation _render/InitVar, signature f\": {el.annotation}\", collect.build_manifest",
            probe=probe_type_annotations,
            apply=apply_type_annotations,
            expected_savings="modern union annotations (Optional[X]->X | None) + type cross-links to reference pages",
            upstream_ref="great_docs render_annotation + render_signature_parameter modernize/linkify against the manifest inventory",
        ),
        Patch(
            name="homepage-demo-transform",
            verified_window="great-docs >=0.15,<0.16 with _create_index_from_readme hero seam (_build_hero_section(readme_content) -> cleaned_content)",
            probe=probe_homepage_demo,
            apply=apply_homepage_demo,
            expected_savings="homepage demo screenshot swapped for a termshow terminal animation (or dropped when no recording)",
            upstream_ref="great_docs GreatDocs._build_hero_section demo-image -> termshow shortcode rewrite",
        ),
        Patch(
            name="hero-logo-off",
            verified_window="great-docs >=0.15,<0.16 with Config.hero_logo property + _build_hero_section hero-logo fallback",
            probe=probe_hero_logo_off,
            apply=apply_hero_logo_off,
            expected_savings="fleet hero renders name + tagline with no mascot <img> unless the repo sets hero.logo",
            upstream_ref="great_docs Config.hero_logo returns False when unset (suppresses the auto-detect/navbar-logo fallback)",
        ),
        Patch(
            name="fleet-css",
            verified_window="great-docs >=0.15,<0.16 with _write_quarto_yml write_yaml + _prepare_build_directory css basename copy",
            probe=probe_fleet_css,
            apply=apply_fleet_css,
            expected_savings="fleet design-system CSS staged to the build root and merged into format.html.css",
            upstream_ref="great_docs GreatDocs._write_quarto_yml merges the materialized fleet-theme.css by basename",
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
