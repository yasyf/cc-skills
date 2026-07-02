"""Shared fixtures. Imports the bootstrap package directly from the skill's scripts/.

These tests live at the PLUGIN root, never under skills/.../templates/ (a path
there would itself get scaffolded — templates/python/tests/test_cli.py is a
shipped template, not one of these).
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "repo-bootstrap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

TEMPLATES = SCRIPTS.parent / "templates"


@pytest.fixture
def templates_dir() -> Path:
    return TEMPLATES


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
