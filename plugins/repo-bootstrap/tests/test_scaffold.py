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
    ".claude/hooks/packs.toml",  # capt-hook packs manifest, replaces vendored hook .py files
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
    assert "docs/scripts/native_reference_titles.py" in got
    assert ".github/workflows/docs.yml" in got
    assert ".github/workflows/release-pypi.yml" not in got


def test_python_pypi_only_drops_docs(py_var_pairs):
    got = dests("python", py_var_pairs, features=["pypi"])
    assert ".github/workflows/release-pypi.yml" in got
    for docs_only in ("great-docs.yml", "docs/scripts/fix_color_swatch.py",
                      "docs/scripts/native_reference_titles.py", ".github/workflows/docs.yml"):
        assert docs_only not in got


def test_python_no_features_drops_all_gated(py_var_pairs):
    got = dests("python", py_var_pairs, features=[])
    for gated in ("great-docs.yml", "docs/scripts/fix_color_swatch.py",
                  "docs/scripts/native_reference_titles.py",
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


def test_ty_runs_via_prek_hook_warning_only(templates_dir):
    cfg = (templates_dir / "python/pre-commit-config.yaml").read_text()
    assert "astral-sh/ty-pre-commit" in cfg
    assert "- id: ty" in cfg
    ci = (templates_dir / "python/github/workflows/ci.yml").read_text()
    assert "uvx prek run ty --all-files" in ci
    py = (templates_dir / "python/pyproject.toml").read_text()
    assert "ty>=" not in py  # the hook rev, not the dev extra, pins ty
    assert 'all = "warn"' in py  # warning-only: ty never blocks


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


def test_resolve_enables_has_license(base_var_pairs, py_var_pairs):
    # base previously hardcoded empty sections; HAS_LICENSE must apply in both layers
    assert "HAS_LICENSE" in scaffold.resolve("base", [], [], base_var_pairs, DATE).enabled_sections
    assert "HAS_LICENSE" in scaffold.resolve("python", [], ["docs"], py_var_pairs, DATE).enabled_sections
    # non-bundled SPDX ids (the MANUAL path) still carry license references
    manual = [p for p in base_var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=Apache-2.0"]
    assert "HAS_LICENSE" in scaffold.resolve("base", [], [], manual, DATE).enabled_sections


def test_resolve_license_none_disables_has_license(base_var_pairs):
    pairs = [p for p in base_var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=none"]
    assert "HAS_LICENSE" not in scaffold.resolve("base", [], [], pairs, DATE).enabled_sections


def test_resolve_rejects_license_none_case_variants(base_var_pairs):
    pairs = [p for p in base_var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=None"]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("base", [], [], pairs, DATE)


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


def test_license_none_returns_notice():
    out = scaffold.license_or_notice(_ctx(("base",), variables={"LICENSE_ID": "none"}), None)
    assert isinstance(out, Notice)
    assert out.text.startswith("NONE    LICENSE")


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


def _real_plan(layer, var_pairs, *, features=None):
    r = scaffold.resolve(layer, [], features if features is not None else ["docs", "pypi"], var_pairs, DATE)
    items = scaffold.select_files(r)
    return scaffold.render_plan(items, r, scaffold.read_template, scaffold.template_exists)


def _license_none(var_pairs):
    return [p for p in var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=none"]


def test_real_templates_render_license_references(base_var_pairs, py_var_pairs):
    plan, notices = _real_plan("python", py_var_pairs)
    assert "MIT License" in plan["LICENSE"]
    assert "License: MIT" in plan["README.md"]
    assert "## License" in _real_plan("base", base_var_pairs)[0]["README.md"]
    assert 'license = "MIT"' in plan["pyproject.toml"]
    assert 'license-files = ["LICENSE"]' in plan["pyproject.toml"]
    assert notices == []


def test_real_templates_render_license_none(base_var_pairs, py_var_pairs):
    plan, notices = _real_plan("python", _license_none(py_var_pairs))
    assert "LICENSE" not in plan
    assert len(notices) == 1 and notices[0].text.startswith("NONE    LICENSE")
    assert "License" not in plan["README.md"]
    assert "license" not in plan["pyproject.toml"]

    base_plan, _ = _real_plan("base", _license_none(base_var_pairs))
    assert "License" not in base_plan["README.md"]
    assert base_plan["README.md"].endswith("addresses it.\n")  # no trailing blank section


def test_real_templates_render_manual_license(py_var_pairs):
    # non-bundled SPDX id: MANUAL notice instead of a LICENSE file, but every
    # license reference stays — this is what separates Apache-2.0 from none
    pairs = [p for p in py_var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=Apache-2.0"]
    plan, notices = _real_plan("python", pairs)
    assert "LICENSE" not in plan
    assert len(notices) == 1 and notices[0].text.startswith("MANUAL  LICENSE")
    assert "License: Apache-2.0" in plan["README.md"]
    assert 'license = "Apache-2.0"' in plan["pyproject.toml"]
    assert 'license-files = ["LICENSE"]' in plan["pyproject.toml"]


def test_great_docs_pypi_widget_follows_feature(py_var_pairs):
    assert "pypi: true" in _real_plan("python", py_var_pairs)[0]["great-docs.yml"]
    assert "pypi: false" in _real_plan("python", py_var_pairs, features=["docs"])[0]["great-docs.yml"]


@pytest.mark.parametrize("layer", ["base", "python"])
def test_real_templates_render_orchestrator_conventions(layer, base_var_pairs, py_var_pairs):
    plan, _ = _real_plan(layer, base_var_pairs if layer == "base" else py_var_pairs)
    assert "## Plan Execution & Orchestration" in plan["CLAUDE.md"]
    assert "one subagent call is fine" in plan["AGENTS.md"]
    assert "required in every plan" in plan["AGENTS.md"]
    assert "act directly" not in plan["AGENTS.md"]


def test_render_plan_unrendered_placeholder_raises():
    r = scaffold.resolve("base", [], [], [
        "PROJECT_NAME=demo", "DESCRIPTION=d", "AUTHOR_NAME=a",
        "AUTHOR_EMAIL=e", "GITHUB_USER=g", "LICENSE_ID=MIT",
    ], DATE)
    items = [PlanItem("x.txt", "x.txt", None)]
    with pytest.raises(ScaffoldError):
        scaffold.render_plan(items, r, lambda s: "{{NOPE}}", lambda s: True)


# --- partial includes (shared fragments) ---

def _missing(src):
    raise FileNotFoundError(src)


def test_expand_partials_inlines_and_strips_trailing_newline():
    templates = {"_partials/p.md": "SHARED\n"}
    out = scaffold.expand_partials("before\n{{> _partials/p.md}}\nafter\n", templates.__getitem__)
    # the partial's own trailing newline is dropped so the directive line's newline isn't doubled
    assert out == "before\nSHARED\nafter\n"


def test_expand_partials_identity_without_directive():
    assert scaffold.expand_partials("plain\n", {}.__getitem__) == "plain\n"


def test_expand_partials_recurses():
    templates = {"a.md": "A {{> b.md}}\n", "b.md": "B\n"}
    assert scaffold.expand_partials("{{> a.md}}\n", templates.__getitem__) == "A B\n"


def test_expand_partials_unknown_raises():
    with pytest.raises(ScaffoldError):
        scaffold.expand_partials("{{> missing.md}}", _missing)


def test_expand_partials_cycle_raises():
    templates = {"a.md": "{{> b.md}}", "b.md": "{{> a.md}}"}
    with pytest.raises(ScaffoldError):
        scaffold.expand_partials("{{> a.md}}", templates.__getitem__)


def test_real_templates_share_version_control_partial(base_var_pairs, py_var_pairs):
    base_plan, _ = _real_plan("base", base_var_pairs)
    py_plan, _ = _real_plan("python", py_var_pairs)
    for agents in (base_plan["AGENTS.md"], py_plan["AGENTS.md"]):
        assert "**Version control.**" in agents
        assert "**Watch CI after every push.**" in agents
        assert "jj git push" in agents and "gh run watch" in agents
    # the fragment is render-only — never written as a destination file
    assert not any(d.startswith("_partials") for d in {**base_plan, **py_plan})
    # python keeps the pypi-gated Releases rule right after the shared partial,
    # separated by exactly one blank line (the inlined fragment's trailing newline
    # is stripped, so it isn't doubled); base carries no Releases rule
    assert "register before watching.)\n\n**Releases.**" in py_plan["AGENTS.md"]
    assert "**Releases.**" not in base_plan["AGENTS.md"]


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
