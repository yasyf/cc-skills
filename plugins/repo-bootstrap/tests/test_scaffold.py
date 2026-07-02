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


# --- go layer selection ---

GO_DESTS = {
    "AGENTS.md", "CLAUDE.md", "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".mcp.json", ".claude/settings.json", ".claude/jj-config.toml", ".claude/hooks/packs.toml",
    ".gitignore", "LICENSE", ".editorconfig", ".golangci.yml", "Taskfile.yml",
    ".pre-commit-config.yaml", ".github/workflows/ci.yml",
    "go.mod", "cmd/demo-proj/main.go",
    "internal/cli/root.go", "internal/cli/hello.go", "internal/cli/hello_test.go",
    "internal/version/version.go", "internal/log/log.go",
}


def test_go_selection_no_release(go_var_pairs):
    got = dests("go", go_var_pairs, features=[])
    assert got == GO_DESTS
    # {{PROJECT_NAME}} in the dest path is substituted, not left literal
    assert "cmd/{{PROJECT_NAME}}/main.go" not in got


def test_go_release_feature_gates(go_var_pairs):
    got = dests("go", go_var_pairs, features=["release"])
    # default release scaffolds only the goreleaser config + the one-liner workflow;
    # the cask is published by goreleaser (homebrew_casks:), so there's no formula template.
    assert got == GO_DESTS | {
        ".goreleaser.yaml",
        ".github/workflows/release.yml",
    }
    assert ".github/formula/demo-proj.rb.tmpl" not in got


def test_go_overrides_base_for_shared_dest(go_var_pairs):
    r = scaffold.resolve("go", [], [], go_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    assert items["AGENTS.md"].src == "go/AGENTS.md"
    assert items["README.md"].src == "go/README.md"
    assert items["STYLEGUIDE.md"].src == "go/STYLEGUIDE.md"
    assert items[".claude/hooks/packs.toml"].src == "go/claude/hooks/packs.toml"


def test_go_module_path_derived(go_var_pairs):
    assert scaffold.resolve("go", [], [], go_var_pairs, DATE).variables["MODULE_PATH"] == "github.com/janedoe/demo-proj"


def test_module_path_absent_without_go(base_var_pairs, py_var_pairs):
    assert "MODULE_PATH" not in scaffold.resolve("base", [], [], base_var_pairs, DATE).variables
    assert "MODULE_PATH" not in scaffold.resolve("python", [], ["docs"], py_var_pairs, DATE).variables


@pytest.mark.parametrize("version", ["1", "1.x", "2026"], ids=["major-only", "non-numeric", "not-go"])
def test_bad_go_version(go_var_pairs, version):
    pairs = [p for p in go_var_pairs if not p.startswith("GO_VERSION=")] + [f"GO_VERSION={version}"]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("go", [], [], pairs, DATE)


def test_go_version_patch_allowed(go_var_pairs):
    pairs = [p for p in go_var_pairs if not p.startswith("GO_VERSION=")] + ["GO_VERSION=1.26.2"]
    assert scaffold.resolve("go", [], [], pairs, DATE).variables["GO_VERSION"] == "1.26.2"


def test_go_silently_drops_python_features(go_var_pairs):
    # docs/pypi are python-only; requesting them on go drops them silently (no error)
    r = scaffold.resolve("go", [], ["docs", "pypi", "release"], go_var_pairs, DATE)
    assert r.features == ("release",)
    assert r.enabled_sections == frozenset({"FEATURE_RELEASE", "HAS_LICENSE"})


def test_python_silently_drops_go_release(py_var_pairs):
    r = scaffold.resolve("python", [], ["docs", "pypi", "release"], py_var_pairs, DATE)
    assert r.features == ("docs", "pypi")
    assert "FEATURE_RELEASE" not in r.enabled_sections


def test_unknown_feature_raises_for_go(go_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("go", [], ["telemetry"], go_var_pairs, DATE)


def test_go_ci_action_major_matches_v2_config(templates_dir):
    # The go CI lint job and the .golangci.yml schema must stay coupled: the
    # config is golangci-lint v2, so the action major must be one that supports
    # v2 (>= v7). golangci-lint-action@v6 is restricted to golangci-lint v1 and
    # cannot parse a v2 config (nor lint a modern Go module) — that mismatch is
    # the recurring CI break this guards against. v8 also runs on the deprecated
    # Node-20 runtime; v9 moved to Node 24 while keeping v2-config support.
    ci = (templates_dir / "go/github/workflows/ci.yml").read_text()
    cfg = (templates_dir / "go/golangci.yml").read_text()
    assert 'version: "2"' in cfg
    assert "golangci/golangci-lint-action@v9" in ci
    assert "golangci-lint-action@v6" not in ci
    assert "golangci-lint-action@v8" not in ci


def test_claude_md_routes_models_not_max_effort(templates_dir):
    # The blanket "max model/effort" rule was replaced (2026-07) by the Models
    # routing table (fable default, sonnet over haiku, /codex lanes, effort
    # xhigh); base-conventions.md and the fleet's deployed CLAUDE.md files carry
    # the same text, and the capt-hook `models` pack enforces it — regressing to
    # the old line would silently fork template from fleet and hooks.
    claude = (templates_dir / "base/CLAUDE.md").read_text()
    assert "max model/effort level" not in claude
    assert "**Models**" in claude
    assert "| fable-5 | 2 | 9 | 9 |" in claude
    assert "judge the output, not the price tag" in claude
    assert "`xhigh` by default" in claude


def test_codex_skill_pins_fast_tier_on_every_exec(templates_dir):
    # The /codex skill must pin xhigh + the fast service tier on every codex
    # exec invocation — without service_tier=fast, xhigh prompts run 10-30+
    # minutes and get abandoned. No invocation may drop the flags.
    skill = templates_dir.parents[3] / "codex" / "skills" / "codex" / "SKILL.md"
    execs = [line for line in skill.read_text().splitlines() if "codex exec" in line and "|" in line]
    assert execs, "expected codex exec invocations in the codex SKILL.md"
    for line in execs:
        assert "-c model_reasoning_effort=xhigh" in line, line
        assert "-c service_tier=fast" in line, line


# --- release: pypi caller -> shared reusable workflow ---


def test_pypi_release_workflow_uses_reusable_workflow(py_var_pairs):
    # The caller delegates the build to the shared reusable workflow, then runs the OIDC
    # publish + github-release IN THIS repo — PyPI Trusted Publishing matches job_workflow_ref,
    # so publish must run in the caller, not inside the reusable workflow.
    wf = _real_plan("python", py_var_pairs)[0][".github/workflows/release-pypi.yml"]
    assert "janedoe/homebrew-tap/.github/workflows/release-pypi-build.yml@pypi-v1" in wf
    assert "secrets: inherit" in wf
    assert "dist-name: demo-proj" in wf
    assert 'python-version: "3.12"' in wf
    # publish runs in the caller (OIDC, in this repo's workflow context)
    assert "pypa/gh-action-pypi-publish@release/v1" in wf
    assert "environment: pypi" in wf
    assert "id-token: write" in wf
    # github-release uses the reusable workflow's tag output
    assert "needs.build.outputs.tag" in wf
    # the tag-driven trigger + never-cancel concurrency stay in the caller
    assert 'tags: ["v*"]' in wf
    assert "cancel-in-progress: false" in wf
    # the gate + build logic live in the reusable workflow, not inline
    assert "git merge-base" not in wf
    assert "uv version --frozen" not in wf


def test_pypi_maturin_off_by_default(py_var_pairs):
    # default python features (docs, pypi) leave maturin off — no native-wheel input
    wf = _real_plan("python", py_var_pairs)[0][".github/workflows/release-pypi.yml"]
    assert "maturin: true" not in wf


def test_pypi_maturin_feature_adds_input(py_var_pairs):
    wf = _real_plan("python", py_var_pairs, features=["docs", "pypi", "maturin"])[0][
        ".github/workflows/release-pypi.yml"
    ]
    assert "maturin: true" in wf


def test_maturin_needs_pypi_to_have_effect(py_var_pairs):
    # maturin only toggles a section inside the pypi-gated caller; with pypi off there is
    # no release file to carry it (and selecting maturin alone is not an error).
    plan, _ = _real_plan("python", py_var_pairs, features=["maturin"])
    assert ".github/workflows/release-pypi.yml" not in plan


def test_maturin_is_opt_in():
    # maturin must stay out of the omitted-`--features` default so a pure-Python scaffold
    # never silently turns on native-wheel builds; docs/pypi remain on by default.
    from bootstrap.manifest import FEATURES

    maturin = next(f for f in FEATURES if f.name == "maturin")
    assert maturin.default is False
    assert maturin.layers == ("python",)
    assert maturin.section == "FEATURE_MATURIN"
    assert all(f.default for f in FEATURES if f.name in ("docs", "pypi"))
    # the go release feature is likewise opt-in — omitting --features must not enable it
    assert next(f for f in FEATURES if f.name == "release").default is False


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


def test_gitignore_concat_base_plus_go():
    rendered = {"base/gitignore": "BASE", "go/gitignore": "GO"}
    out = scaffold.gitignore_concat(_ctx(("base", "go"), render=rendered.__getitem__), None)
    assert out == "BASE\nGO"


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


def test_license_badge_doubles_dashes(base_var_pairs):
    # shields.io reads single dashes as the label/message/color separators, so a
    # dashed license id must double them in the badge URL; the alt text stays readable.
    pairs = [p for p in base_var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=PolyForm-Noncommercial-1.0.0"]
    readme = _real_plan("base", pairs)[0]["README.md"]
    assert "badge/License-PolyForm--Noncommercial--1.0.0-blue.svg" in readme
    assert "[![License: PolyForm-Noncommercial-1.0.0]" in readme
    # a dash-free id needs no doubling
    mit = _real_plan("base", base_var_pairs)[0]["README.md"]
    assert "badge/License-MIT-blue.svg" in mit


def test_great_docs_pypi_widget_follows_feature(py_var_pairs):
    assert "pypi: true" in _real_plan("python", py_var_pairs)[0]["great-docs.yml"]
    assert "pypi: false" in _real_plan("python", py_var_pairs, features=["docs"])[0]["great-docs.yml"]


def test_real_templates_render_go(go_var_pairs):
    plan, notices = _real_plan("go", go_var_pairs, features=["release"])
    assert notices == []
    # go.mod carries the derived module path + go version
    assert "module github.com/janedoe/demo-proj" in plan["go.mod"]
    assert "go 1.26" in plan["go.mod"]
    # the cmd dir dest was substituted from {{PROJECT_NAME}}
    assert plan["cmd/demo-proj/main.go"].startswith("// Command demo-proj")
    assert "{{MODULE_PATH}}/internal/cli" not in plan["cmd/demo-proj/main.go"]
    # go AGENTS.md pulls in the shared collaboration partials
    agents = plan["AGENTS.md"]
    assert "## Ask Before Assuming" in agents
    assert "one subagent call is fine" in agents  # from the parallelize partial
    assert "## Writing Plans" in agents
    # FEATURE_RELEASE sections render with release on
    assert "**Releases.**" in agents
    assert "brew install janedoe/tap/demo-proj" in plan["README.md"]


def test_go_goreleaser_template_tokens_survive(go_var_pairs):
    gor = _real_plan("go", go_var_pairs, features=["release"])[0][".goreleaser.yaml"]
    # goreleaser Go-template tokens (spaces/dots) are NOT bootstrap placeholders — pass through
    assert "{{ .Version }}" in gor
    assert "{{ .Commit }}" in gor
    # bootstrap placeholders ARE rendered
    assert "github.com/janedoe/demo-proj/internal/version.Version={{ .Version }}" in gor
    assert "project_name: demo-proj" in gor


def test_go_goreleaser_cask_block(go_var_pairs):
    # The default distribution is a native Homebrew cask published by goreleaser itself;
    # the HOMEBREW_TAP_TOKEN env token survives rendering and the tap owner/name are filled.
    gor = _real_plan("go", go_var_pairs, features=["release"])[0][".goreleaser.yaml"]
    assert "homebrew_casks:" in gor
    assert "{{ .Env.HOMEBREW_TAP_TOKEN }}" in gor
    assert "name: demo-proj" in gor  # cask name (PROJECT_NAME substituted)
    assert "owner: janedoe" in gor  # tap repo owner (GITHUB_USER substituted)
    assert "name: homebrew-tap" in gor


def test_go_goreleaser_notarize_block(go_var_pairs):
    gor = _real_plan("go", go_var_pairs, features=["release"])[0][".goreleaser.yaml"]
    assert "notarize:" in gor
    # the env-guard (non-empty, not isEnvSet) and all five MACOS_* env tokens pass through untouched
    assert "enabled: '{{ if envOrDefault \"MACOS_SIGN_P12\" \"\" }}true{{ else }}false{{ end }}'" in gor
    for tok in ("MACOS_SIGN_P12", "MACOS_SIGN_PASSWORD", "MACOS_NOTARY_ISSUER_ID",
                "MACOS_NOTARY_KEY_ID", "MACOS_NOTARY_KEY"):
        assert "{{ .Env." + tok + " }}" in gor
    # the notarize ids: list has PROJECT_NAME substituted (8-space indent, distinct from the cask binaries list)
    assert "ids:\n        - demo-proj" in gor


def test_go_release_workflow_uses_reusable_workflow(go_var_pairs):
    # release.yml is a one-liner that forwards to the shared reusable workflow and inherits
    # every secret (HOMEBREW_TAP_TOKEN + the five MACOS_*); it no longer names them inline.
    wf = _real_plan("go", go_var_pairs, features=["release"])[0][".github/workflows/release.yml"]
    assert "janedoe/homebrew-tap/.github/workflows/release-go.yml@v1" in wf
    assert "secrets: inherit" in wf
    # the old inline goreleaser job + per-secret env are gone
    assert "MACOS_SIGN_P12" not in wf
    assert "goreleaser/goreleaser-action" not in wf


def test_go_no_release_drops_goreleaser_and_release_section(go_var_pairs):
    plan, _ = _real_plan("go", go_var_pairs, features=[])
    assert ".goreleaser.yaml" not in plan
    assert ".github/workflows/release.yml" not in plan
    assert "**Releases.**" not in plan["AGENTS.md"]
    # README falls back to go install / task build, no brew line
    assert "brew install" not in plan["README.md"]
    assert "go install github.com/janedoe/demo-proj/cmd/demo-proj@latest" in plan["README.md"]


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
