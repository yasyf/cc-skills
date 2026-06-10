"""Phase pipeline: resolve/validate, selection matrix, derive, render_plan,
transforms, and apply_plan. All pure/offline."""

from __future__ import annotations

import datetime
import json

import pytest
from bootstrap import scaffold
from bootstrap.common import Notice, PlanItem, ScaffoldError, TransformCtx

DATE = datetime.date(2026, 6, 8)


def dests(layer, var_pairs, *, extras=None, features=None):
    r = scaffold.resolve(layer, extras or [], features if features is not None else ["docs", "pypi"], var_pairs, DATE)
    return {item.dest for item in scaffold.select_files(r)}


# --- selection matrix ---

BASE_DESTS = {
    "AGENTS.md", "CLAUDE.md", "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".mcp.json", ".claude/settings.json", ".claude/jj-config.toml",
    ".claude/hooks/__init__.py",
    ".claude/hooks/commands.py", ".claude/hooks/stewardship.py",
    ".claude/hooks/prompts.py", ".claude/hooks/docs.py", ".claude/hooks/tasks.py",
    ".gitignore", "LICENSE",
}


def test_base_selection_exact(base_var_pairs):
    assert dests("base", base_var_pairs) == BASE_DESTS


def test_base_ignores_features(base_var_pairs):
    # features are python-only; passing them in base changes nothing
    assert dests("base", base_var_pairs, features=["docs", "pypi"]) == BASE_DESTS


def test_python_both_features_substitutes_package(py_var_pairs):
    got = dests("python", py_var_pairs)
    assert "demo_proj/cli.py" in got and "demo_proj/__init__.py" in got
    assert ".claude/ty-quiet.toml" in got  # python-only ty silence config (absent from BASE_DESTS)
    assert "great-docs.yml" in got  # docs
    assert ".github/workflows/release-pypi.yml" in got  # pypi
    assert got >= BASE_DESTS  # python implies base
    assert "{{PACKAGE}}/cli.py" not in got


def test_python_docs_only_drops_pypi(py_var_pairs):
    got = dests("python", py_var_pairs, features=["docs"])
    assert "great-docs.yml" in got
    assert ".github/workflows/docs.yml" in got
    assert ".github/workflows/release-pypi.yml" not in got


def test_python_pypi_only_drops_docs(py_var_pairs):
    got = dests("python", py_var_pairs, features=["pypi"])
    assert ".github/workflows/release-pypi.yml" in got
    for docs_only in ("great-docs.yml", "docs/scripts/fix_color_swatch.py", ".github/workflows/docs.yml"):
        assert docs_only not in got


def test_python_no_features_drops_all_gated(py_var_pairs):
    got = dests("python", py_var_pairs, features=[])
    for gated in ("great-docs.yml", "docs/scripts/fix_color_swatch.py",
                  ".github/workflows/docs.yml", ".github/workflows/release-pypi.yml"):
        assert gated not in got


# --- release gate: tag must be on main ---


def test_release_workflow_gates_build_on_tag_on_main(templates_dir):
    wf = (templates_dir / "python/github/workflows/release-pypi.yml").read_text()
    # the gate job exists and checks the tagged commit's ancestry against main
    assert "verify-tag-on-main:" in wf
    assert "git merge-base --is-ancestor" in wf
    # build (and therefore the whole publish chain) depends on the gate
    assert "  build:\n    needs: verify-tag-on-main\n" in wf


def test_extras_gating(base_var_pairs):
    assert ".env" not in dests("base", base_var_pairs)
    assert ".env" in dests("base", base_var_pairs, extras=["env"])
    assert ".superset/config.json" in dests("base", base_var_pairs, extras=["superset"])


def test_python_overrides_base_for_shared_dest(py_var_pairs):
    # AGENTS.md exists in both layers; the python spec must win.
    r = scaffold.resolve("python", [], ["docs", "pypi"], py_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    assert items["AGENTS.md"].src == "python/AGENTS.md"
    assert items["README.md"].src == "python/README.md"


# --- resolve / validate ---

def test_unknown_var(base_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("base", [], [], ["BOGUS=1", *base_var_pairs], DATE)


def test_var_must_be_key_value():
    with pytest.raises(ScaffoldError):
        scaffold.resolve("base", [], [], ["PROJECT_NAME"], DATE)


def test_missing_required(base_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("python", [], [], base_var_pairs, DATE)  # no DIST_NAME/PACKAGE/...


def test_bad_package(py_var_pairs):
    pairs = [p for p in py_var_pairs if not p.startswith("PACKAGE=")] + ["PACKAGE=not-an-identifier"]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("python", [], [], pairs, DATE)


def test_bad_dist_name(py_var_pairs):
    pairs = [p for p in py_var_pairs if not p.startswith("DIST_NAME=")] + ["DIST_NAME=_bad_"]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("python", [], [], pairs, DATE)


def test_bad_python_min(py_var_pairs):
    pairs = [p for p in py_var_pairs if not p.startswith("PYTHON_MIN=")] + ["PYTHON_MIN=3"]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("python", [], [], pairs, DATE)


def test_unknown_extra(base_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("base", ["nope"], [], base_var_pairs, DATE)


def test_unknown_feature(py_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("python", [], ["telemetry"], py_var_pairs, DATE)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("none", []), ("superset,env", ["superset", "env"]), ("env", ["env"])],
    ids=["none", "both", "single"],
)
def test_parse_extras(raw, expected):
    assert scaffold.parse_extras(raw) == expected


@pytest.mark.parametrize("raw", ["", ",", "none,superset"], ids=["empty", "only-commas", "none-mixed"])
def test_parse_extras_rejects(raw):
    with pytest.raises(ScaffoldError):
        scaffold.parse_extras(raw)


# --- derive (clock injected) ---

def test_derive_vars_uses_injected_clock(base_var_pairs):
    r = scaffold.resolve("base", [], [], base_var_pairs, datetime.date(1999, 1, 1))
    assert r.variables["YEAR"] == "1999"
    assert r.variables["REPO_URL"] == "https://github.com/janedoe/demo-proj"
    assert r.variables["DOCS_URL"] == "https://janedoe.github.io/demo-proj/"
    assert "PY_TARGET" not in r.variables  # no PYTHON_MIN in base


def test_py_target_derived(py_var_pairs):
    r = scaffold.resolve("python", [], ["docs", "pypi"], py_var_pairs, DATE)
    assert r.variables["PY_TARGET"] == "py310"


# --- transforms ---

def _ctx(layers, *, render=None, exists=None, variables=None):
    return TransformCtx(
        layers=layers,
        variables=variables or {"LICENSE_ID": "MIT"},
        enabled_sections=frozenset(),
        render=render or (lambda src: f"<{src}>"),
        template_exists=exists or (lambda src: True),
    )


def test_strip_uv_setup_strips_for_base():
    config = json.dumps({"setup": ["uv sync", "echo hi", "uv build"]})
    out = scaffold.strip_uv_setup(_ctx(("base",)), config)
    assert json.loads(out)["setup"] == ["echo hi"]


def test_strip_uv_setup_noops_for_python():
    config = json.dumps({"setup": ["uv sync", "echo hi"]})
    out = scaffold.strip_uv_setup(_ctx(("base", "python")), config)
    assert out == config  # unchanged passthrough


def test_gitignore_concat_base_only():
    rendered = {"base/gitignore": "BASE", "python/gitignore": "PY"}
    out = scaffold.gitignore_concat(_ctx(("base",), render=rendered.__getitem__), None)
    assert out == "BASE"


def test_gitignore_concat_base_plus_python():
    rendered = {"base/gitignore": "BASE", "python/gitignore": "PY"}
    out = scaffold.gitignore_concat(_ctx(("base", "python"), render=rendered.__getitem__), None)
    assert out == "BASE\nPY"


def test_license_renders_when_template_exists():
    out = scaffold.license_or_notice(_ctx(("base",), exists=lambda src: True), None)
    assert out == "<base/LICENSE-MIT>"


def test_license_returns_notice_when_absent():
    out = scaffold.license_or_notice(
        _ctx(("base",), variables={"LICENSE_ID": "Apache-2.0"}, exists=lambda src: False), None
    )
    assert isinstance(out, Notice)
    assert out.text.startswith("MANUAL  LICENSE")
    assert "Apache-2.0.txt" in out.text


# --- render_plan with injected templates (no filesystem) ---

def test_render_plan_injected(monkeypatch):
    templates = {
        "base/gitignore": "node_modules\n",
        "base/LICENSE-MIT": "MIT for {{PROJECT_NAME}}\n",
        "foo.txt": "hello {{PROJECT_NAME}} {{#FEATURE_DOCS}}+docs{{/FEATURE_DOCS}}\n",
    }
    r = scaffold.resolve("base", [], [], [
        "PROJECT_NAME=demo", "DESCRIPTION=d", "AUTHOR_NAME=a",
        "AUTHOR_EMAIL=e", "GITHUB_USER=g", "LICENSE_ID=MIT",
    ], DATE)
    items = [
        PlanItem("foo.txt", "foo.txt", None),
        PlanItem(".gitignore", None, "gitignore"),
        PlanItem("LICENSE", None, "license"),
    ]
    plan, notices = scaffold.render_plan(items, r, templates.__getitem__, lambda s: s in templates)
    assert plan["foo.txt"] == "hello demo \n"
    assert plan[".gitignore"] == "node_modules\n"
    assert plan["LICENSE"] == "MIT for demo\n"
    assert notices == []


def test_render_plan_unrendered_placeholder_raises():
    r = scaffold.resolve("base", [], [], [
        "PROJECT_NAME=demo", "DESCRIPTION=d", "AUTHOR_NAME=a",
        "AUTHOR_EMAIL=e", "GITHUB_USER=g", "LICENSE_ID=MIT",
    ], DATE)
    items = [PlanItem("x.txt", "x.txt", None)]
    with pytest.raises(ScaffoldError):
        scaffold.render_plan(items, r, lambda s: "{{NOPE}}", lambda s: True)


# --- apply_plan ---

def test_apply_writes_then_skips(tmp_path, capsys):
    plan = {"a.txt": "hi\n", "sub/b.txt": "yo\n"}
    assert scaffold.apply_plan(plan, tmp_path, force=False, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "WROTE  a.txt" in out and "WROTE  sub/b.txt" in out
    assert (tmp_path / "a.txt").read_text() == "hi\n"

    assert scaffold.apply_plan(plan, tmp_path, force=False, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "SKIP    a.txt" in out and "WROTE" not in out


def test_apply_conflict_without_force(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("different\n")
    code = scaffold.apply_plan({"a.txt": "hi\n"}, tmp_path, force=False, dry_run=False)
    assert code == 1
    err = capsys.readouterr().err
    assert "CONFLICT  a.txt exists with different content" in err
    assert (tmp_path / "a.txt").read_text() == "different\n"  # untouched


def test_apply_force_overwrites(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("different\n")
    assert scaffold.apply_plan({"a.txt": "hi\n"}, tmp_path, force=True, dry_run=False) == 0
    assert (tmp_path / "a.txt").read_text() == "hi\n"
    assert "WROTE  a.txt" in capsys.readouterr().out


def test_apply_dry_run_writes_nothing(tmp_path, capsys):
    assert scaffold.apply_plan({"a.txt": "hi\n"}, tmp_path, force=False, dry_run=True) == 0
    assert not (tmp_path / "a.txt").exists()
    assert "WOULD WRITE  a.txt" in capsys.readouterr().out
