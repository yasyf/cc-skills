"""Declarative manifest for the scaffolder — data only, no logic.

Adding a layer / feature / extra / var / file is a matter of appending a record
here; the engine in ``scaffold.py`` contains no per-file branching. ``src`` paths
must match the template tree under ``../templates`` exactly.
"""

from __future__ import annotations

from .common import DerivedVar, Feature, FileSpec, Layer, VarSpec

# Layer precedence: earlier layers are overridden by later ones at the same dest.
LAYERS = (
    Layer("base"),
    Layer("python", implies=("base",)),
)
LAYER_ORDER = tuple(layer.name for layer in LAYERS)

# Optional python features. Each maps to a {{#FEATURE_*}} section token and may
# gate whole files (see FILES below). Default: both enabled.
FEATURES = (
    Feature("docs", "FEATURE_DOCS"),
    Feature("pypi", "FEATURE_PYPI"),
)

# Optional extra layers, selectable in any layer via --extras.
EXTRAS = ("superset", "env")

VARS = (
    VarSpec("PROJECT_NAME", ("base", "python")),
    VarSpec("DESCRIPTION", ("base", "python")),
    VarSpec("AUTHOR_NAME", ("base", "python")),
    VarSpec("AUTHOR_EMAIL", ("base", "python")),
    VarSpec("GITHUB_USER", ("base", "python")),
    VarSpec("LICENSE_ID", ("base", "python"), validate="license_id"),
    # PACKAGE is validated before DIST_NAME to match the legacy check order.
    VarSpec("PACKAGE", ("python",), validate="identifier"),
    VarSpec("DIST_NAME", ("python",), validate="dist_name"),
    VarSpec("PYTHON_PIN", ("python",), validate="py_version"),
    VarSpec("PYTHON_MIN", ("python",), validate="py_version"),
)

DERIVED = (
    DerivedVar("REPO_URL", lambda v, now: f"https://github.com/{v['GITHUB_USER']}/{v['PROJECT_NAME']}"),
    DerivedVar("DOCS_URL", lambda v, now: f"https://{v['GITHUB_USER']}.github.io/{v['PROJECT_NAME']}/"),
    DerivedVar("YEAR", lambda v, now: str(now.year)),
    DerivedVar("PY_TARGET", lambda v, now: ("py" + v["PYTHON_MIN"].replace(".", "")) if v.get("PYTHON_MIN") else None),
)

FILES = (
    # --- base layer ---
    FileSpec("AGENTS.md", "base/AGENTS.md", "base"),
    FileSpec("CLAUDE.md", "base/CLAUDE.md", "base"),
    FileSpec("STYLEGUIDE.md", "base/STYLEGUIDE.md", "base"),
    FileSpec("README.md", "base/README.md", "base"),
    FileSpec("CHANGELOG.md", "base/CHANGELOG.md", "base"),
    FileSpec(".mcp.json", "base/mcp.json", "base"),
    FileSpec(".claude/settings.json", "base/claude/settings.json", "base"),
    FileSpec(".claude/jj-config.toml", "base/claude/jj-config.toml", "base"),
    # capt-hook hooks ship as the `general` builtin pack; the project enables it
    # via packs.toml instead of vendoring the hook files. See reference/hooks.md.
    FileSpec(".claude/hooks/packs.toml", "base/claude/hooks/packs.toml", "base"),
    # synthesized base files (no single template src)
    FileSpec(".gitignore", None, "base", transform="gitignore"),
    FileSpec("LICENSE", None, "base", transform="license"),
    # --- python layer (overrides base where dest collides) ---
    FileSpec("AGENTS.md", "python/AGENTS.md", "python"),
    FileSpec("STYLEGUIDE.md", "python/STYLEGUIDE.md", "python"),
    FileSpec("README.md", "python/README.md", "python"),
    FileSpec(".claude/settings.json", "python/claude/settings.json", "python"),
    FileSpec(".claude/ty-quiet.toml", "python/claude/ty-quiet.toml", "python"),
    FileSpec("pyproject.toml", "python/pyproject.toml", "python"),
    FileSpec(".python-version", "python/python-version", "python"),
    # python layer adds the `python` builtin pack on top of `general` (overrides
    # the base packs.toml at the same dest with both packs enabled).
    FileSpec(".claude/hooks/packs.toml", "python/claude/hooks/packs.toml", "python"),
    FileSpec(".github/workflows/ci.yml", "python/github/workflows/ci.yml", "python"),
    FileSpec(".pre-commit-config.yaml", "python/pre-commit-config.yaml", "python"),
    FileSpec("{{PACKAGE}}/__init__.py", "python/package/__init__.py", "python"),
    FileSpec("{{PACKAGE}}/__main__.py", "python/package/__main__.py", "python"),
    FileSpec("{{PACKAGE}}/cli.py", "python/package/cli.py", "python"),
    FileSpec("{{PACKAGE}}/py.typed", "python/package/py.typed", "python"),
    FileSpec("tests/__init__.py", "python/tests/__init__.py", "python"),
    FileSpec("tests/conftest.py", "python/tests/conftest.py", "python"),
    FileSpec("tests/test_cli.py", "python/tests/test_cli.py", "python"),
    # feature-gated python files (content-level feature diffs live in templates)
    FileSpec("great-docs.yml", "python/great-docs.yml", "python", feature="docs"),
    FileSpec("docs/scripts/fix_color_swatch.py", "python/docs/scripts/fix_color_swatch.py", "python", feature="docs"),
    FileSpec("docs/scripts/native_reference_titles.py", "python/docs/scripts/native_reference_titles.py", "python", feature="docs"),
    FileSpec(".github/workflows/docs.yml", "python/github/workflows/docs.yml", "python", feature="docs"),
    FileSpec(".github/workflows/release-pypi.yml", "python/github/workflows/release-pypi.yml", "python", feature="pypi"),
    # --- extras (apply in any layer) ---
    FileSpec(".env", "extras/env", "base", extra="env"),
    FileSpec(".superset/config.json", "extras/superset-config.json", "base", extra="superset", transform="superset_strip"),
)
