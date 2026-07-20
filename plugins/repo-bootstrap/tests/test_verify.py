"""verify subcommand: offline checks (leftover tokens, LICENSE) and an opt-in
end-to-end tier that scaffolds a real python repo and runs the full verify."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from bootstrap import verify

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "repo-bootstrap" / "scripts"
BOOTSTRAP = SCRIPTS / "bootstrap.py"

BASE_VARS = [
    "--var", "PROJECT_NAME=demo-proj",
    "--var", "DESCRIPTION=A demo project.",
    "--var", "AUTHOR_NAME=Jane Doe",
    "--var", "AUTHOR_EMAIL=jane@example.com",
    "--var", "GITHUB_USER=janedoe",
    "--var", "LICENSE_ID=MIT",
]
# PYTHON_MIN must satisfy the docs feature's great-docs floor (>=3.11).
PY_VARS = BASE_VARS + [
    "--var", "DIST_NAME=demo-proj",
    "--var", "PACKAGE=demo_proj",
    "--var", "PYTHON_PIN=3.12",
    "--var", "PYTHON_MIN=3.11",
]


# --- offline check units ---

def test_no_leftover_tokens_pass(tmp_path, monkeypatch):
    (tmp_path / "clean.md").write_text("all good here\n")
    monkeypatch.chdir(tmp_path)
    ok, _ = verify._no_leftover_tokens()
    assert ok


def test_no_leftover_tokens_fail(tmp_path, monkeypatch):
    (tmp_path / "bad.md").write_text("oops {{PROJECT_NAME}} left in\n")
    monkeypatch.chdir(tmp_path)
    ok, output = verify._no_leftover_tokens()
    assert not ok
    assert "{{PROJECT_NAME}}" in output


def test_no_leftover_tokens_skips_ignored_dirs(tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "x.txt").write_text("{{LEFT}}\n")
    monkeypatch.chdir(tmp_path)
    ok, _ = verify._no_leftover_tokens()
    assert ok  # dist/ is excluded


def test_license_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not verify._license_check(False)[0]
    (tmp_path / "LICENSE").write_text("")
    assert not verify._license_check(False)[0]  # empty fails
    (tmp_path / "LICENSE").write_text("MIT License\n")
    assert verify._license_check(False)[0]


def test_license_check_no_license(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert verify._license_check(True)[0]  # absent passes
    (tmp_path / "LICENSE").write_text("MIT License\n")
    ok, output = verify._license_check(True)
    assert not ok
    assert "delete it" in output
    (tmp_path / "LICENSE").unlink()
    (tmp_path / "LICENSE").symlink_to(tmp_path / "gone")
    assert not verify._license_check(True)[0]  # dangling symlink is still a LICENSE entry


@pytest.mark.parametrize("ext", ["webp", "png"], ids=["webp", "legacy-png"])
def test_missing_banner_note(tmp_path, monkeypatch, ext):
    monkeypatch.chdir(tmp_path)
    assert verify._missing_banner_note() is None  # no README at all
    (tmp_path / "README.md").write_text(f"# demo\n\n![demo banner](docs/assets/readme-banner.{ext})\n")
    note = verify._missing_banner_note()
    assert note is not None
    assert "docs/assets/readme-banner" in note
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / f"readme-banner.{ext}").write_bytes(b"\x89PNG")
    assert verify._missing_banner_note() is None  # banner present


def test_missing_banner_note_no_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("# demo\n\nno banner reference\n")
    assert verify._missing_banner_note() is None  # escape hatch removed the line


def test_missing_demo_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert verify._missing_demo_note() is None  # no README at all
    (tmp_path / "README.md").write_text('# demo\n\n<img src="docs/assets/demo.png" alt="demo run" width="700">\n')
    note = verify._missing_demo_note()
    assert note is not None
    assert "docs/assets/demo.png" in note
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo.png").write_bytes(b"\x89PNG")
    note = verify._missing_demo_note()  # asset present, no committed generator
    assert note is not None
    assert "generator" in note
    (tmp_path / "docs" / "scripts").mkdir(parents=True)
    (tmp_path / "docs" / "scripts" / "demo.sh").write_text("freeze --execute 'demo --help'\n")
    assert verify._missing_demo_note() is None  # asset + generator present


def test_missing_demo_note_tape_generator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("# demo\n\n![demo](docs/assets/demo.svg)\n")
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "demo.svg").write_text("<svg/>")
    assert verify._missing_demo_note() is not None  # no generator yet
    (tmp_path / ".cli-demo").mkdir()
    (tmp_path / ".cli-demo" / "demo.tape").write_text('Type "demo"\nEnter\n')
    assert verify._missing_demo_note() is None  # tape counts as the generator


def test_missing_demo_note_no_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("# demo\n\n```text\nfenced output fallback\n```\n")
    assert verify._missing_demo_note() is None  # escape hatch: no demo reference


def test_missing_social_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert verify._missing_social_note() is None  # no banner: escape hatch stays silent
    (tmp_path / "docs" / "assets").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "readme-banner.webp").write_bytes(b"RIFF")
    note = verify._missing_social_note()
    assert note is not None
    assert "docs/assets/social-preview.jpg" in note
    assert "--from-logo" in note
    (tmp_path / "docs" / "assets" / "social-preview.jpg").write_bytes(b"\xff\xd8")
    assert verify._missing_social_note() is None  # both present


# --- opt-in end-to-end (needs the uv toolchain + network for capt-hook) ---

@pytest.mark.uv
@pytest.mark.parametrize(
    ("features", "license_id"),
    [("docs,pypi", "MIT"), ("", "none")],
    ids=["all-features-mit", "bare-no-license"],
)
def test_end_to_end_python(tmp_path, features, license_id, cc_guides_stub):
    if not shutil.which("uv"):
        pytest.skip("uv not installed")
    # Don't let the test-runner's own venv leak into the scaffolded project's uv runs.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    py_vars = [f"LICENSE_ID={license_id}" if v == "LICENSE_ID=MIT" else v for v in PY_VARS]
    # Phase 0 precondition: a bootstrapped project is a git repo on main, which the
    # prek hook-config check (`uvx prek prepare-hooks`) needs to find a git root.
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    scaffold = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "scaffold", "--target", str(tmp_path),
         "--layer", "python", "--extras", "none", "--features", features, *py_vars],
        capture_output=True, text=True, env=env,
    )
    assert scaffold.returncode == 0, scaffold.stdout + scaffold.stderr

    verify_flags = ["--no-license"] if license_id == "none" else []
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "verify", "--layer", "python", "--target", str(tmp_path), *verify_flags],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout


# --- swift check units ---

def test_swift_executable_name_parse(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Package.swift").write_text(
        'let package = Package(\n'
        '    products: [\n'
        '        .library(name: "DemoProj", targets: ["DemoProj"]),\n'
        '        .executable(name: "demo-proj", targets: ["demo-proj"]),\n'
        '    ],\n'
        ')\n'
    )
    match = verify._SWIFT_EXECUTABLE_RE.search((tmp_path / "Package.swift").read_text())
    assert match is not None
    assert match.group(1) == "demo-proj"  # picks the executable, not the library


def test_swift_executable_name_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Package.swift").write_text('let package = Package(products: [.library(name: "Lib", targets: ["Lib"])])\n')
    ok, output = verify._swift_binary_smoke()
    assert not ok
    assert "executable" in output


def test_no_leftover_tokens_skips_spm_build_dir(tmp_path, monkeypatch):
    (tmp_path / ".build" / "checkouts").mkdir(parents=True)
    (tmp_path / ".build" / "checkouts" / "dep.md").write_text("{{LEFT}}\n")
    monkeypatch.chdir(tmp_path)
    ok, _ = verify._no_leftover_tokens()
    assert ok  # .build/ (SPM dependency checkouts) is excluded


GO_VARS = BASE_VARS + ["--var", "GO_VERSION=1.26"]


@pytest.mark.go
def test_end_to_end_go(tmp_path, cc_guides_stub):
    if not shutil.which("go"):
        pytest.skip("go not installed")
    if not shutil.which("uv"):
        pytest.skip("uv not installed (verify runs the capt-hook inline tests via uvx)")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    scaffold = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "scaffold", "--target", str(tmp_path),
         "--layer", "go", "--extras", "none", "--features", "release", *GO_VARS],
        capture_output=True, text=True, env=env,
    )
    assert scaffold.returncode == 0, scaffold.stdout + scaffold.stderr

    # `go mod tidy` resolves cobra and writes go.sum — the scaffold ships neither.
    tidy = subprocess.run(["go", "mod", "tidy"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert tidy.returncode == 0, tidy.stdout + tidy.stderr

    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "verify", "--layer", "go", "--target", str(tmp_path)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout


SWIFT_VARS = BASE_VARS + [
    "--var", "MODULE_NAME=DemoProj",
    "--var", "SWIFT_TOOLS_VERSION=6.2",
]

SWIFT_APP_VARS = BASE_VARS + [
    "--var", "MODULE_NAME=DemoProj",
    "--var", "BUNDLE_ID_PREFIX=com.janedoe",
    "--var", "IOS_DEPLOYMENT_TARGET=26.0",
]


@pytest.mark.swift
def test_end_to_end_swift(tmp_path, cc_guides_stub):
    if not shutil.which("swift"):
        pytest.skip("swift not installed")
    if not shutil.which("uv"):
        pytest.skip("uv not installed (verify runs the capt-hook inline tests via uvx)")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    scaffold = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "scaffold", "--target", str(tmp_path),
         "--layer", "swift", "--extras", "none", "--features", "release", *SWIFT_VARS],
        capture_output=True, text=True, env=env,
    )
    assert scaffold.returncode == 0, scaffold.stdout + scaffold.stderr

    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "verify", "--layer", "swift", "--target", str(tmp_path)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout
    # Package.resolved is written by the build — the SKILL tells users to commit it.
    assert (tmp_path / "Package.resolved").is_file()


@pytest.mark.xcode
def test_end_to_end_swift_app(tmp_path, cc_guides_stub):
    """Minutes-slow on first run (simulator-platform build). Requires full Xcode;
    with Xcode present but the iOS platform component not downloaded, verify
    NOTE-skips the build and this test still passes (structure is validated)."""
    if subprocess.run(["xcodebuild", "-version"], capture_output=True).returncode != 0:
        pytest.skip("Xcode not installed (CLT-only xcodebuild stub)")
    if not shutil.which("uv"):
        pytest.skip("uv not installed (verify runs the capt-hook inline tests via uvx)")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    scaffold = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "scaffold", "--target", str(tmp_path),
         "--layer", "swift-app", "--extras", "none", "--features", "", *SWIFT_APP_VARS],
        capture_output=True, text=True, env=env,
    )
    assert scaffold.returncode == 0, scaffold.stdout + scaffold.stderr

    # The committed pbxproj must parse as a real project with both targets.
    listing = subprocess.run(
        ["xcodebuild", "-list", "-project", "demo-proj.xcodeproj"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert "demo-proj" in listing.stdout and "demo-projTests" in listing.stdout

    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "verify", "--layer", "swift-app", "--target", str(tmp_path)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout
