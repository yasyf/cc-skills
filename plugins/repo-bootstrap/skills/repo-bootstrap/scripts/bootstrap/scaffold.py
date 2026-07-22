"""The scaffold engine: a pure phase pipeline with I/O only at the edges.

    resolve (argv, clock)  ->  select_files (pure)  ->  render_plan (pure given
    read_template)  ->  [validate folded into render]  ->  apply_plan (I/O)

``select_files`` and ``render_plan`` know nothing about the special cases
(LICENSE fallback, superset uv-strip) — each is a named entry in ``TRANSFORMS``.
Template access is injected so rendering stays pure.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import render as _render
from .common import (
    BUN_VERSION_RE,
    BUNDLE_ID_PREFIX_RE,
    CODE_ROOT_RE,
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
from .manifest import DERIVED, EXTRAS, FEATURES, FILES, LAYER_ORDER, LAYERS, SECONDARY_LAYERS, VARS

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
        if kind == "bun_version" and not BUN_VERSION_RE.match(value):
            raise ScaffoldError(f"{name} must look like 1.3.14, got {value!r}")
        if kind == "bundle_id_prefix" and not BUNDLE_ID_PREFIX_RE.match(value):
            raise ScaffoldError(f"{name} must be a reverse-DNS prefix like com.yasyf, got {value!r}")
        if kind == "license_id" and value.lower() == "none" and value != "none":
            raise ScaffoldError(f"{name} must be lowercase 'none' for no license, got {value!r}")
        if kind == "binary_version_mode" and value not in ("pinned", "latest"):
            raise ScaffoldError(f"{name} must be 'pinned' or 'latest', got {value!r}")
        if kind == "code_root" and (not CODE_ROOT_RE.fullmatch(value) or {".", ".."} & set(value.split("/"))):
            raise ScaffoldError(f"{name} must be a repo-root-relative subdir like plugin/hooks, got {value!r}")
    # Cross-var checks for the swift layers:
    # - SPM target names must be unique, and the executable target is named
    #   PROJECT_NAME while the library is MODULE_NAME — a collision fails
    #   `swift build` with a confusing manifest error. Case-INSENSITIVE, because
    #   Sources/<MODULE_NAME>/ and Sources/<PROJECT_NAME>/ land in one physical
    #   directory on macOS's default case-insensitive APFS (so `fusekit` /
    #   `Fusekit` breaks exactly like an exact match).
    if layer in ("swift", "swift-app"):
        module = variables.get("MODULE_NAME", "")
        project = variables.get("PROJECT_NAME", "")
        if module.lower() == project.lower():
            raise ScaffoldError(
                "MODULE_NAME must differ from PROJECT_NAME beyond letter case (they name distinct "
                "Swift targets, and case-insensitive filesystems merge their Sources/ dirs) — "
                "for a single-word project, suffix the module (e.g. FusekitKit)"
            )
    # - BUNDLE_ID derives as <prefix>.<PROJECT_NAME>, and CFBundleIdentifier allows
    #   only alphanumerics, hyphens, and periods — an underscore repo name would
    #   scaffold an app that fails at App ID registration, long after verify.
    if layer == "swift-app" and not re.fullmatch(r"[A-Za-z0-9-]+", variables.get("PROJECT_NAME", "")):
        raise ScaffoldError(
            "PROJECT_NAME must contain only alphanumerics and hyphens for swift-app "
            "(it becomes the bundle id suffix, which forbids underscores)"
        )


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


def resolve(
    layer: str,
    extras: list[str],
    features: list[str],
    var_pairs: list[str],
    now: date,
    secondary_layer: str | None = None,
) -> ResolveResult:
    if unknown := sorted(set(extras) - set(EXTRAS)):
        raise ScaffoldError(f"unknown extras: {', '.join(unknown)}; known: {', '.join(EXTRAS)}")
    # An unknown feature name is an error; a known feature requested outside its
    # layer is silently dropped — base has always ignored docs/pypi this way, so
    # the argparse "all features" default reduces to each layer's own set.
    if unknown := sorted(set(features) - set(_FEATURE_NAMES)):
        raise ScaffoldError(f"unknown features: {', '.join(unknown)}; known: {', '.join(_FEATURE_NAMES)}")
    if secondary_layer is not None:
        if secondary_layer not in SECONDARY_LAYERS:
            raise ScaffoldError(f"unknown secondary layer {secondary_layer!r}; known: {', '.join(SECONDARY_LAYERS)}")
        if secondary_layer == layer:
            raise ScaffoldError(f"--secondary-layer {secondary_layer} must differ from --layer {layer}")
    applicable = {f.name for f in FEATURES if layer in f.layers}
    features = [f for f in features if f in applicable]
    variables = parse_vars(var_pairs)
    validate_vars(variables, layer)
    if secondary_layer is not None and "SECONDARY_CODE_ROOT" not in variables:
        raise ScaffoldError("--secondary-layer requires --var SECONDARY_CODE_ROOT=<dir>")
    variables = derive_vars(variables, now)

    enabled = {f.section for f in FEATURES if f.name in features}
    # HAS_LICENSE is var-derived, unlike FEATURE_*: it applies in every layer.
    if variables["LICENSE_ID"] != "none":
        enabled.add("HAS_LICENSE")
    # The plugin extra's installer resolves its target release from plugin.json
    # (PINNED, the default) or the releases/latest redirect (LATEST). Var-derived
    # like HAS_LICENSE — extras carry no section tokens of their own.
    if "plugin" in extras:
        enabled.add("LATEST" if variables.get("BINARY_VERSION_MODE") == "latest" else "PINNED")
    # A secondary layer's `## <Lang> Style` fragment is gated by this section in every
    # layout.toml.
    if secondary_layer is not None:
        enabled.add("SECONDARY_STYLE")
    return ResolveResult(
        layers=expand_layers(layer),
        features=tuple(features),
        enabled_sections=frozenset(enabled),
        extras=tuple(extras),
        variables=variables,
        secondary_layer=secondary_layer,
    )


# --- Phase 2: select_files (pure declarative filter) ---


def select_files(r: ResolveResult) -> list[PlanItem]:
    chosen: dict[str, tuple[int, FileSpec]] = {}
    secondary: list[tuple[str, FileSpec]] = []
    for spec in FILES:
        # A secondary-layer spec is gated by --secondary-layer, not layer membership,
        # so it lands beside a different primary layer's files without joining r.layers.
        if spec.secondary_layer is not None:
            if spec.secondary_layer != r.secondary_layer:
                continue
        elif spec.layer not in r.layers:
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
        if spec.secondary_layer is not None:
            secondary.append((dest, spec))
            continue
        precedence = _LAYER_INDEX[spec.layer]
        # last-writer-wins by dest, ordered by explicit layer precedence
        if dest not in chosen or precedence >= chosen[dest][0]:
            chosen[dest] = (precedence, spec)
    # Static same-dest collisions are the intended base→language override; a secondary
    # dest colliding (case-folded) is a SECONDARY_CODE_ROOT mistake, e.g. .claude/hooks.
    folded = {dest.casefold(): dest for dest in chosen}
    for dest, spec in secondary:
        if (hit := folded.get(dest.casefold())) is not None:
            raise ScaffoldError(f"SECONDARY_CODE_ROOT places {dest} onto the planned {hit}; pick a different code root")
        folded[dest.casefold()] = dest
        chosen[dest] = (_LAYER_INDEX[spec.layer], spec)
    return [PlanItem(dest, spec.src, spec.transform) for dest, (_, spec) in chosen.items()]


# --- Phase 3: render_plan (pure given an injected template reader) ---


def expand_partials(text: str, read: Callable[[str], str], _stack: tuple[str, ...] = ()) -> str:
    """Inline ``{{> _partials/…}}`` directives (raw, pre-render) so a README seed
    shares the including file's variable/feature context. Recursive, cycle-guarded.

    ``_partials/`` seeds are the only partial mechanism left: shared cc-guides
    fragments are now composed by ``cc-guides render`` through the scaffolded
    ``.claude/fragments/<target>/`` layout dirs, never inlined here. Any other
    ``{{> …}}`` directive is therefore a mistake and fails loudly."""

    def repl(m: re.Match[str]) -> str:
        path = m.group(1)
        if not path.startswith("_partials/"):
            raise ScaffoldError(f"unknown partial directive {{{{> {path}}}}}; only _partials/ seeds are supported")
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
    "license": license_or_notice,
    "superset_strip": strip_uv_setup,
}


# --- Phase 5: apply_plan (I/O edge) ---


def verify_plan_paths(plan: dict[str, str], target: Path) -> None:
    """Reject a plan that would write outside ``target`` or trip over itself, before any I/O.

    Four lanes, each a real escape or mid-write crash otherwise: a symlinked ancestor
    carries a write out of the target (resolve-containment); two destinations differing
    only by case address one file on a case-insensitive filesystem; one destination
    nests inside another planned file (its ``mkdir`` hits the file); an existing
    non-directory ancestor blocks its subtree the same way on a re-run.
    """
    root = target.resolve()
    folded: dict[str, str] = {}
    for dest in sorted(plan):
        if (prior := folded.get(dest.casefold())) is not None:
            raise ScaffoldError(f"destinations {prior} and {dest} address the same file on a case-insensitive filesystem")
        folded[dest.casefold()] = dest
        if not (target / dest).resolve().is_relative_to(root):
            raise ScaffoldError(f"destination {dest} resolves outside the target directory")
    for dest in sorted(plan):
        parts = dest.split("/")
        parent = target
        for i, part in enumerate(parts[:-1]):
            ancestor = "/".join(parts[: i + 1])
            if ancestor.casefold() in folded:
                raise ScaffoldError(f"destination {dest} nests inside the planned file {ancestor}")
            parent = parent / part
            if parent.exists() and not parent.is_dir():
                raise ScaffoldError(f"destination {dest} needs directory {ancestor}, which exists as a file")


def apply_plan(plan: dict[str, str], target: Path, force: bool, dry_run: bool) -> int:
    verify_plan_paths(plan, target)
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
            # A scaffolded shell script (scripts/test.sh) must be runnable as
            # `scripts/test.sh …`; write_text alone leaves it non-executable.
            if dest.endswith(".sh"):
                path.chmod(0o755)
        print(f"{action}  {dest}")
    return 0


# --- Production I/O edges + CLI entry ---


def read_template(src: str) -> str:
    return (TEMPLATES / src).read_text()


def template_exists(src: str) -> bool:
    return (TEMPLATES / src).exists()


def render_sources(target: Path, force: bool) -> None:
    """Compose every ``.claude/fragments/<target>/`` layout dir the scaffold wrote
    (AGENTS.md, CLAUDE.md, .gitignore, .claude/settings.json, .mcp.json, and the plugin
    installer) into its artifact via a full ``cc-guides render``. cc-guides resolves the imported
    shared fragments from ``github:yasyf/cc-skills@main`` and stamps each artifact.
    On a fresh scaffold the artifacts do not exist yet; retrofitting an existing repo
    needs ``force`` because cc-guides refuses to overwrite a JSON artifact its lock
    does not list. Hard-required: the shared fragment bodies live upstream, so there
    is no Python fallback."""
    exe = shutil.which("cc-guides")
    if exe is None:
        raise ScaffoldError("cc-guides not found on PATH; install it with `brew install yasyf/tap/cc-guides`")
    cmd = [exe, "render", "--force"] if force else [exe, "render"]
    proc = subprocess.run(cmd, cwd=target, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ScaffoldError(f"cc-guides render failed: {proc.stderr.strip() or proc.stdout.strip()}")
    if proc.stdout.strip():
        print(proc.stdout.strip())


def run(args: argparse.Namespace) -> int:
    extras = parse_extras(args.extras)
    features = [f for f in args.features.split(",") if f]
    r = resolve(args.layer, extras, features, args.var, datetime.date.today(), args.secondary_layer)
    items = select_files(r)
    plan, notices = render_plan(items, r, read_template, template_exists)
    code = apply_plan(plan, args.target, args.force, args.dry_run)
    for notice in notices:
        print(notice.text)
    # Every X.src.<ext> just written renders to its sibling artifact in place.
    if code == 0 and not args.dry_run:
        render_sources(args.target, args.force)
    return code
