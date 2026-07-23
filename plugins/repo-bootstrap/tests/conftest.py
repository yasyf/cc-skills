"""Shared fixtures. Imports the bootstrap package directly from the skill's scripts/.

These tests live at the PLUGIN root, never under skills/.../templates/ (a path
there would itself get scaffolded — templates/python/tests/test_cli.py is a
shipped template, not one of these).
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "repo-bootstrap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

TEMPLATES = SCRIPTS.parent / "templates"

# A stub `cc-guides` binary for the post-write render step, so scaffolds run offline
# and independent of any installed cc-guides. It proves it ran in the target dir (a
# marker file lands in cwd) and composes each `.claude/fragments/<target>/` layout dir
# into a stub `<target>` artifact (real composition needs the network). Tests must NOT
# depend on a real cc-guides on the machine.
_STUB_CC_GUIDES = r"""#!/bin/sh
[ "$1" = "render" ] || exit 0
: > .cc-guides-stub
find .claude/fragments -name layout.toml 2>/dev/null | while IFS= read -r lay; do
  dir="${lay%/layout.toml}"
  target="${dir#./}"
  target="${target#.claude/fragments/}"
  mkdir -p "$(dirname "$target")"
  if [ "$target" = ".pre-commit-config.yaml" ]; then
    printf 'repos: []\n' > "$target"
  else
    echo "# stub-rendered $target from $dir" > "$target"
  fi
  echo "rendered $dir -> $target" >> .cc-guides-stub
done
echo "stub cc-guides render complete"
"""


@pytest.fixture
def templates_dir() -> Path:
    return TEMPLATES


@pytest.fixture
def cc_guides_stub(tmp_path_factory, monkeypatch) -> Path:
    stub_dir = tmp_path_factory.mktemp("cc-guides-bin")
    exe = stub_dir / "cc-guides"
    exe.write_text(_STUB_CC_GUIDES)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")
    return stub_dir


@pytest.fixture
def fixed_date() -> datetime.date:
    return datetime.date(2026, 6, 8)


@pytest.fixture
def base_var_pairs() -> list[str]:
    return [
        "PROJECT_NAME=demo-proj",
        "DESCRIPTION=A demo project.",
        "AUTHOR_NAME=Jane Doe",
        "AUTHOR_EMAIL=jane@example.com",
        "GITHUB_USER=janedoe",
        "LICENSE_ID=MIT",
    ]


@pytest.fixture
def py_var_pairs(base_var_pairs: list[str]) -> list[str]:
    return base_var_pairs + [
        "DIST_NAME=demo-proj",
        "PACKAGE=demo_proj",
        "PYTHON_PIN=3.12",
        "PYTHON_MIN=3.10",
    ]


@pytest.fixture
def go_var_pairs(base_var_pairs: list[str]) -> list[str]:
    return base_var_pairs + ["GO_VERSION=1.26"]


@pytest.fixture
def plugin_var_pairs(base_var_pairs: list[str]) -> list[str]:
    return base_var_pairs + [
        "BINARY_NAME=demo-proj",
        "RELEASE_REPO=janedoe/demo-proj",
        "BREW_PACKAGE=janedoe/tap/demo-proj",
        "PLUGIN_NAME=demo-proj",
    ]


@pytest.fixture
def swift_var_pairs(base_var_pairs: list[str]) -> list[str]:
    return base_var_pairs + [
        "MODULE_NAME=DemoProj",
        "SWIFT_TOOLS_VERSION=6.2",
    ]


@pytest.fixture
def swift_app_var_pairs(base_var_pairs: list[str]) -> list[str]:
    return base_var_pairs + [
        "MODULE_NAME=DemoProj",
        "BUNDLE_ID_PREFIX=com.janedoe",
        "IOS_DEPLOYMENT_TARGET=26.0",
    ]


@pytest.fixture
def bun_var_pairs(base_var_pairs: list[str]) -> list[str]:
    return base_var_pairs + ["BUN_VERSION=1.3.14"]
