"""The scaffold engine: a pure phase pipeline with I/O only at the edges.

    resolve (argv, clock)  ->  select_files (pure)  ->  render_plan (pure given
    read_template)  ->  [validate folded into render]  ->  apply_plan (I/O)

``select_files`` and ``render_plan`` know nothing about the special cases
(.gitignore concat, LICENSE fallback, superset uv-strip) — each is a named entry
in ``TRANSFORMS``. Template access is injected so rendering stays pure.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import render as _render
from .common import (
    BUNDLE_ID_PREFIX_RE,
    DIST_NAME_RE,
    GO_VERSION_RE,
    IOS_VERSION_RE,
    PARTIAL,
    PY_VERSION_RE,
    SWIFT_TOOLS_VERSION_RE,
    FileSpec,
    Notice,
    PlanItem,
    ResolveResult,
    ScaffoldError,
    TransformCtx,
)
from .manifest import DERIVED, EXTRAS, FEATURES, FILES, LAYER_ORDER, LAYERS, VARS

TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"

KNOWN_VARS = frozenset(v.name for v in VARS)
_VALIDATORS = tuple((v.name, v.validate) for v in VARS if v.validate)
_FEATURE_NAMES = tuple(f.name for f in FEATURES)
_LAYER_INDEX = {name: i for i, name in enumerate(LAYER_ORDER)}


# --- Phase 1: resolve (I/O edge — argv values + clock) ---


def parse_vars(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not value:
            raise ScaffoldError(f"--var must be KEY=VALUE, got {pair!r}")
        if key not in KNOWN_VARS:
            raise ScaffoldError(f"unknown var {key!r}; known: {', '.join(sorted(KNOWN_VARS))}")
        out[key] = value
    return out


def parse_extras(raw: str) -> list[str]:
    if raw == "none":
        return []
    extras = [e for e in raw.split(",") if e]
    if not extras or "none" in extras:
        raise ScaffoldError(f"--extras must be 'none' or a comma-separated subset of: {', '.join(EXTRAS)}")
    return extras


def validate_vars(variables: dict[str, str], layer: str) -> None:
    required = {v.name for v in VARS if layer in v.required_in}
    if missing := sorted(required - variables.keys()):
        raise ScaffoldError(f"missing required vars: {', '.join(missing)}")
    for name, kind in _VALIDATORS:
        value = variables.get(name)
        if not value:
            continue
        if kind == "identifier" and not value.isidentifier():
            raise ScaffoldError(f"{name} must be a valid Python identifier, got {value!r}")
        if kind == "dist_name" and not DIST_NAME_RE.match(value):
            raise ScaffoldError(f"{name} must be a valid PyPI project name, got {value!r}")
        if kind == "py_version" and not PY_VERSION_RE.match(value):
            raise ScaffoldError(f"{name} must look like 3.X, got {value!r}")
        if kind == "go_version" and not GO_VERSION_RE.match(value):
            raise ScaffoldError(f"{name} must look like 1.X or 1.X.Y, got {value!r}")
        if kind == "swift_tools_version" and not SWIFT_TOOLS_VERSION_RE.match(value):
            raise ScaffoldError(f"{name} must look like 6.2, got {value!r}")
        if kind == "ios_version" and not IOS_VERSION_RE.match(value):
            raise ScaffoldError(f"{name} must look like 26 or 26.0, got {value!r}")
        if kind == "bundle_id_prefix" and not BUNDLE_ID_PREFIX_RE.match(value):
            raise ScaffoldError(f"{name} must be a reverse-DNS prefix like com.yasyf, got {value!r}")
        if kind == "license_id" and value.lower() == "none" and value != "none":
            raise ScaffoldError(f"{name} must be lowercase 'none' for no license, got {value!r}")
    # Cross-var check: SPM target names must be unique, and the executable target is
    # named PROJECT_NAME while the library is MODULE_NAME — a collision fails
    # `swift build` with a confusing manifest error, so fail fast here instead.
    if layer in ("swift", "swift-app") and variables.get("MODULE_NAME") == variables.get("PROJECT_NAME"):
        raise ScaffoldError("MODULE_NAME must differ from PROJECT_NAME (they name distinct Swift targets)")


def derive_vars(variables: dict[str, str], now: date) -> dict[str, str]:
    out = dict(variables)
    for dv in DERIVED:
        value = dv.fn(variables, now)
        if value is not None:
            out[dv.name] = value
    return out


def expand_layers(layer: str) -> tuple[str, ...]:
    implied = next((lyr.implies for lyr in LAYERS if lyr.name == layer), ())
    active = set(implied) | {layer}
    return tuple(name for name in LAYER_ORDER if name in active)


def resolve(layer: str, extras: list[str], features: list[str], var_pairs: list[str], now: date) -> ResolveResult:
    if unknown := sorted(set(extras) - set(EXTRAS)):
        raise ScaffoldError(f"unknown extras: {', '.join(unknown)}; known: {', '.join(EXTRAS)}")
    # An unknown feature name is an error; a known feature requested outside its
    # layer is silently dropped — base has always ignored docs/pypi this way, so
    # the argparse "all features" default reduces to each layer's own set.
    if unknown := sorted(set(features) - set(_FEATURE_NAMES)):
        raise ScaffoldError(f"unknown features: {', '.join(unknown)}; known: {', '.join(_FEATURE_NAMES)}")
    applicable = {f.name for f in FEATURES if layer in f.layers}
    features = [f for f in features if f in applicable]

    variables = parse_vars(var_pairs)
    validate_vars(variables, layer)
    variables = derive_vars(variables, now)

    enabled = {f.section for f in FEATURES if f.name in features}
    # HAS_LICENSE is var-derived, unlike FEATURE_*: it applies in every layer.
    if variables["LICENSE_ID"] != "none":
        enabled.add("HAS_LICENSE")
    return ResolveResult(
        layers=expand_layers(layer),
        features=tuple(features),
        enabled_sections=frozenset(enabled),
        extras=tuple(extras),
        variables=variables,
    )


# --- Phase 2: select_files (pure declarative filter) ---


def select_files(r: ResolveResult) -> list[PlanItem]:
    chosen: dict[str, tuple[int, FileSpec]] = {}
    for spec in FILES:
        if spec.layer not in r.layers:
            continue
        if spec.feature is not None and spec.feature not in r.features:
            continue
        if spec.extra is not None and spec.extra not in r.extras:
            continue
        # Resolve any {{VAR}} token in the destination path (e.g. {{PACKAGE}} for
        # python, {{PROJECT_NAME}} for go's cmd/<name>/main.go). The {{ }} bounds
        # mean a var name that prefixes another can't partially match.
        dest = spec.dest
        for key, value in r.variables.items():
            dest = dest.replace("{{" + key + "}}", value)
        precedence = _LAYER_INDEX[spec.layer]
        # last-writer-wins by dest, ordered by explicit layer precedence
        if dest not in chosen or precedence >= chosen[dest][0]:
            chosen[dest] = (precedence, spec)
    return [PlanItem(dest, spec.src, spec.transform) for dest, (_, spec) in chosen.items()]


# --- Phase 3: render_plan (pure given an injected template reader) ---


def expand_partials(text: str, read: Callable[[str], str], _stack: tuple[str, ...] = ()) -> str:
    """Inline ``{{> path}}`` directives (raw, pre-render) so a partial shares the
    including file's variable/feature context. Recursive, cycle-guarded."""

    def repl(m: re.Match[str]) -> str:
        path = m.group(1)
        if path in _stack:
            raise ScaffoldError(f"partial include cycle: {' -> '.join((*_stack, path))}")
        try:
            body = read(path)
        except FileNotFoundError:
            raise ScaffoldError(f"unknown partial {path!r}")
        expanded = expand_partials(body, read, (*_stack, path))
        # The directive sits on its own line; drop the fragment's own trailing
        # newline so its line break isn't doubled with the directive line's.
        return expanded[:-1] if expanded.endswith("\n") else expanded

    return PARTIAL.sub(repl, text)


def render_plan(
    items: list[PlanItem],
    r: ResolveResult,
    read_template: Callable[[str], str],
    template_exists: Callable[[str], bool],
) -> tuple[dict[str, str], list[Notice]]:
    def render_src(src: str) -> str:
        raw = expand_partials(read_template(src), read_template)
        text = _render.render(raw, r.variables, r.enabled_sections)
        if leftover := _render.find_unrendered_sections(text):
            raise ScaffoldError(f"unbalanced conditional sections in {src}: {', '.join(leftover)}")
        if leftover := _render.find_unrendered_placeholders(text):
            raise ScaffoldError(f"unrendered placeholders in {src}: {', '.join(leftover)}")
        return text

    ctx = TransformCtx(r.layers, r.variables, r.enabled_sections, render_src, template_exists)
    plan: dict[str, str] = {}
    notices: list[Notice] = []
    for item in items:
        content = render_src(item.src) if item.src is not None else None
        if item.transform is not None:
            result = TRANSFORMS[item.transform](ctx, content)
            if isinstance(result, Notice):
                notices.append(result)
                continue
            content = result
        assert content is not None  # a src=None item must carry a synthesizing transform
        plan[item.dest] = content
    return plan, notices


# --- Special cases as named transforms ---


def gitignore_concat(ctx: TransformCtx, content: str | None) -> str:
    text = ctx.render("base/gitignore")
    if "python" in ctx.layers:
        text += "\n" + ctx.render("python/gitignore")
    if "go" in ctx.layers:
        text += "\n" + ctx.render("go/gitignore")
    # Both swift layers share one fragment (Xcode + SwiftPM + XcodeBuildMCP state).
    if "swift" in ctx.layers or "swift-app" in ctx.layers:
        text += "\n" + ctx.render("swift/gitignore")
    return text


def license_or_notice(ctx: TransformCtx, content: str | None) -> str | Notice:
    license_id = ctx.variables["LICENSE_ID"]
    if license_id == "none":
        return Notice("NONE    LICENSE — none chosen; delete any existing LICENSE file")
    src = f"base/LICENSE-{license_id}"
    if ctx.template_exists(src):
        return ctx.render(src)
    return Notice(
        f"MANUAL  LICENSE — fetch it yourself: "
        f"curl -fsS https://raw.githubusercontent.com/spdx/license-list-data/main/text/{license_id}.txt > LICENSE"
    )


def strip_uv_setup(ctx: TransformCtx, content: str | None) -> str:
    assert content is not None
    if "python" in ctx.layers:
        return content
    parsed = json.loads(content)
    parsed["setup"] = [line for line in parsed["setup"] if not line.startswith("uv ")]
    return json.dumps(parsed, indent=2) + "\n"


TRANSFORMS: dict[str, Callable[[TransformCtx, str | None], str | Notice]] = {
    "gitignore": gitignore_concat,
    "license": license_or_notice,
    "superset_strip": strip_uv_setup,
}


# --- Phase 5: apply_plan (I/O edge) ---


def apply_plan(plan: dict[str, str], target: Path, force: bool, dry_run: bool) -> int:
    conflicts = [
        dest for dest, content in sorted(plan.items()) if (p := target / dest).exists() and p.read_text() != content
    ]
    if conflicts and not force:
        for dest in conflicts:
            print(f"CONFLICT  {dest} exists with different content", file=sys.stderr)
        print("Nothing written. Resolve the conflicts or re-run with --force.", file=sys.stderr)
        return 1

    for dest, content in sorted(plan.items()):
        path = target / dest
        if path.exists() and path.read_text() == content:
            print(f"SKIP    {dest}")
            continue
        action = "WOULD WRITE" if dry_run else "WROTE"
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        print(f"{action}  {dest}")
    return 0


# --- Production I/O edges + CLI entry ---


def read_template(src: str) -> str:
    return (TEMPLATES / src).read_text()


def template_exists(src: str) -> bool:
    return (TEMPLATES / src).exists()


def run(args: argparse.Namespace) -> int:
    extras = parse_extras(args.extras)
    features = [f for f in args.features.split(",") if f]
    r = resolve(args.layer, extras, features, args.var, datetime.date.today())
    items = select_files(r)
    plan, notices = render_plan(items, r, read_template, template_exists)
    code = apply_plan(plan, args.target, args.force, args.dry_run)
    for notice in notices:
        print(notice.text)
    return code
