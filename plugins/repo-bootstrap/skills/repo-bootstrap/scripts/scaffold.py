#!/usr/bin/env python3
"""Render repo-bootstrap templates into a target repo.

Stdlib only — this runs before the target project has any dependencies.
Layer rules: python implies base; python overrides same-destination base files;
.gitignore is concatenated (base + python); hooks are additive.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
DIST_NAME_RE = re.compile(r"^([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])$")
PY_VERSION_RE = re.compile(r"^3\.\d+$")

REQUIRED_BASE = ("PROJECT_NAME", "DESCRIPTION", "AUTHOR_NAME", "AUTHOR_EMAIL", "GITHUB_USER", "LICENSE_ID")
REQUIRED_PYTHON = ("DIST_NAME", "PACKAGE", "PYTHON_PIN", "PYTHON_MIN")
KNOWN_VARS = frozenset(REQUIRED_BASE) | frozenset(REQUIRED_PYTHON)
DERIVED_VARS = ("REPO_URL", "DOCS_URL", "PY_TARGET", "YEAR")

BASE_FILES = {
    "AGENTS.md": "base/AGENTS.md",
    "CLAUDE.md": "base/CLAUDE.md",
    "STYLEGUIDE.md": "base/STYLEGUIDE.md",
    "README.md": "base/README.md",
    "CHANGELOG.md": "base/CHANGELOG.md",
    ".mcp.json": "base/mcp.json",
    ".claude/settings.json": "base/claude/settings.json",
    ".claude/jj-config.toml": "base/claude/jj-config.toml",
    ".claude/hooks/__init__.py": "base/claude/hooks/__init__.py",
    ".claude/hooks/audit.py": "base/claude/hooks/audit.py",
    ".claude/hooks/commands.py": "base/claude/hooks/commands.py",
    ".claude/hooks/stewardship.py": "base/claude/hooks/stewardship.py",
}

PYTHON_FILES = {
    "AGENTS.md": "python/AGENTS.md",
    "STYLEGUIDE.md": "python/STYLEGUIDE.md",
    "README.md": "python/README.md",
    ".claude/settings.json": "python/claude/settings.json",
    "pyproject.toml": "python/pyproject.toml",
    ".python-version": "python/python-version",
    "great-docs.yml": "python/great-docs.yml",
    ".claude/hooks/testing.py": "python/claude/hooks/testing.py",
    ".claude/hooks/style.py": "python/claude/hooks/style.py",
    ".claude/hooks/toolchain.py": "python/claude/hooks/toolchain.py",
    ".github/workflows/ci.yml": "python/github/workflows/ci.yml",
    ".github/workflows/docs.yml": "python/github/workflows/docs.yml",
    ".github/workflows/release-pypi.yml": "python/github/workflows/release-pypi.yml",
    "{{PACKAGE}}/__init__.py": "python/package/__init__.py",
    "{{PACKAGE}}/__main__.py": "python/package/__main__.py",
    "{{PACKAGE}}/cli.py": "python/package/cli.py",
    "{{PACKAGE}}/py.typed": "python/package/py.typed",
    "tests/__init__.py": "python/tests/__init__.py",
    "tests/test_cli.py": "python/tests/test_cli.py",
}

EXTRA_FILES = {
    "superset": {".superset/config.json": "extras/superset-config.json"},
    "env": {".env": "extras/env"},
}


class ScaffoldError(SystemExit):
    def __init__(self, message: str) -> None:
        print(f"ERROR: {message}", file=sys.stderr)
        super().__init__(1)


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


def validate(variables: dict[str, str], layer: str, extras: list[str]) -> None:
    required = set(REQUIRED_BASE) | (set(REQUIRED_PYTHON) if layer == "python" else set())
    if missing := sorted(required - variables.keys()):
        raise ScaffoldError(f"missing required vars: {', '.join(missing)}")
    if (package := variables.get("PACKAGE")) and not package.isidentifier():
        raise ScaffoldError(f"PACKAGE must be a valid Python identifier, got {package!r}")
    if (dist := variables.get("DIST_NAME")) and not DIST_NAME_RE.match(dist):
        raise ScaffoldError(f"DIST_NAME must be a valid PyPI project name, got {dist!r}")
    for key in ("PYTHON_PIN", "PYTHON_MIN"):
        if (version := variables.get(key)) and not PY_VERSION_RE.match(version):
            raise ScaffoldError(f"{key} must look like 3.X, got {version!r}")


def derive(variables: dict[str, str]) -> dict[str, str]:
    derived = {
        "REPO_URL": f"https://github.com/{variables['GITHUB_USER']}/{variables['PROJECT_NAME']}",
        "DOCS_URL": f"https://{variables['GITHUB_USER']}.github.io/{variables['PROJECT_NAME']}/",
        "YEAR": str(datetime.date.today().year),
    }
    if python_min := variables.get("PYTHON_MIN"):
        derived["PY_TARGET"] = "py" + python_min.replace(".", "")
    return variables | derived


def render(src: str, variables: dict[str, str]) -> str:
    text = (TEMPLATES / src).read_text()
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    if leftover := sorted({m.group(0) for m in PLACEHOLDER.finditer(text)}):
        raise ScaffoldError(f"unrendered placeholders in {src}: {', '.join(leftover)}")
    return text


def strip_uv_setup(config: str) -> str:
    parsed = json.loads(config)
    parsed["setup"] = [line for line in parsed["setup"] if not line.startswith("uv ")]
    return json.dumps(parsed, indent=2) + "\n"


def build_plan(layer: str, extras: list[str], variables: dict[str, str]) -> dict[str, str]:
    sources = dict(BASE_FILES)
    if layer == "python":
        sources |= PYTHON_FILES
    for extra in extras:
        sources |= EXTRA_FILES[extra]

    plan = {
        dest.replace("{{PACKAGE}}", variables.get("PACKAGE", "")): render(src, variables)
        for dest, src in sources.items()
    }

    gitignore = render("base/gitignore", variables)
    if layer == "python":
        gitignore += "\n" + render("python/gitignore", variables)
    plan[".gitignore"] = gitignore

    license_id = variables["LICENSE_ID"]
    license_src = f"base/LICENSE-{license_id}"
    if (TEMPLATES / license_src).exists():
        plan["LICENSE"] = render(license_src, variables)
    else:
        print(
            f"MANUAL  LICENSE — fetch it yourself: "
            f"curl -fsS https://raw.githubusercontent.com/spdx/license-list-data/main/text/{license_id}.txt > LICENSE"
        )

    if "superset" in extras and layer == "base":
        plan[".superset/config.json"] = strip_uv_setup(plan[".superset/config.json"])

    return plan


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("."))
    parser.add_argument("--layer", choices=("base", "python"), default="base")
    parser.add_argument("--extras", default="", help=f"comma-separated: {', '.join(EXTRA_FILES)}")
    parser.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--force", action="store_true", help="overwrite conflicting files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    extras = [e for e in args.extras.split(",") if e]
    if unknown := sorted(set(extras) - EXTRA_FILES.keys()):
        raise ScaffoldError(f"unknown extras: {', '.join(unknown)}; known: {', '.join(EXTRA_FILES)}")

    variables = parse_vars(args.var)
    validate(variables, args.layer, extras)
    plan = build_plan(args.layer, extras, derive(variables))
    return apply_plan(plan, args.target, args.force, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
