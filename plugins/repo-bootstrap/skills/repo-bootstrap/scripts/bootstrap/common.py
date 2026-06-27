"""Shared records, regexes, and helpers for the bootstrap engine.

STDLIB ONLY. This package runs under the system ``python3`` before ``uv`` has
created any environment, so it must never import third-party modules.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date

# --- Regexes (single source of truth, shared by render/validate/check-name/verify) ---

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
# Mustache-style conditional sections, gated on enabled tokens (FEATURE_* from
# python features; HAS_LICENSE derived from LICENSE_ID, active in every layer).
# Block form consumes the whole tag line (and its newline); inline form stays
# on one line. {{#NAME}} keeps the body when NAME is enabled, {{^NAME}} when not.
SECTION_BLOCK = re.compile(
    r"^[ \t]*\{\{([#^])([A-Z_]+)\}\}[ \t]*\n(.*?)^[ \t]*\{\{/\2\}\}[ \t]*\n",
    re.DOTALL | re.MULTILINE,
)
SECTION_INLINE = re.compile(r"\{\{([#^])([A-Z_]+)\}\}(.*?)\{\{/\2\}\}")
SECTION_LEFTOVER = re.compile(r"\{\{[#^/][A-Z_]+\}\}")
# Mustache-style partial: {{> path/under/templates}}, inlined at scaffold time
# before sections/placeholders render. Partials never become a destination file.
PARTIAL = re.compile(r"\{\{>\s*([^}]+?)\s*\}\}")
DIST_NAME_RE = re.compile(r"^([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])$")
PY_VERSION_RE = re.compile(r"^3\.\d+$")
GO_VERSION_RE = re.compile(r"^1\.\d+(\.\d+)?$")


class ScaffoldError(SystemExit):
    """Fatal user error: prints ``ERROR: ...`` to stderr and exits 1."""

    def __init__(self, message: str) -> None:
        print(f"ERROR: {message}", file=sys.stderr)
        super().__init__(1)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, capturing combined text output. Never raises on nonzero."""
    return subprocess.run(cmd, capture_output=True, text=True)


# --- Manifest records (frozen data) ---


@dataclass(frozen=True)
class FileSpec:
    """One template -> destination mapping.

    ``dest`` may contain ``{{PACKAGE}}``. ``src=None`` means the content is
    synthesized by ``transform`` rather than read from a template.
    """

    dest: str
    src: str | None
    layer: str
    feature: str | None = None
    extra: str | None = None
    transform: str | None = None


@dataclass(frozen=True)
class Layer:
    name: str
    implies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feature:
    name: str
    section: str
    # Layers this feature is offered in. A request for a feature outside its
    # layers is silently dropped (see scaffold.resolve), like base ignoring docs.
    layers: tuple[str, ...] = ()
    # Whether an omitted ``--features`` enables it. Opt-in features (default=False,
    # e.g. maturin) are absent from the "all for the layer" default and must be
    # named explicitly, so a plain python scaffold stays pure-Python.
    default: bool = True


@dataclass(frozen=True)
class VarSpec:
    name: str
    required_in: tuple[str, ...]
    validate: str | None = None  # one of: identifier, dist_name, py_version, license_id


@dataclass(frozen=True)
class DerivedVar:
    name: str
    fn: Callable[[Mapping[str, str], date], str | None]


# --- Result records ---


@dataclass(frozen=True)
class ResolveResult:
    layers: tuple[str, ...]
    features: tuple[str, ...]
    enabled_sections: frozenset[str]
    extras: tuple[str, ...]
    variables: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanItem:
    dest: str
    src: str | None
    transform: str | None


@dataclass(frozen=True)
class Notice:
    text: str


@dataclass(frozen=True)
class TransformCtx:
    layers: tuple[str, ...]
    variables: Mapping[str, str]
    enabled_sections: frozenset[str]
    render: Callable[[str], str]
    template_exists: Callable[[str], bool]
