"""Phase pipeline: resolve/validate, selection matrix, derive, render_plan,
transforms, and apply_plan. All pure/offline."""

from __future__ import annotations

import argparse
import datetime
import json
import tomllib

import pytest
from bootstrap import scaffold
from bootstrap.common import Notice, PlanItem, ScaffoldError, TransformCtx

DATE = datetime.date(2026, 6, 8)


def dests(layer, var_pairs, *, extras=None, features=None, secondary_layer=None):
    r = scaffold.resolve(
        layer, extras or [], features if features is not None else ["docs", "pypi"], var_pairs, DATE, secondary_layer
    )
    return {item.dest for item in scaffold.select_files(r)}


# --- selection matrix ---

# AGENTS.md, CLAUDE.md, and .claude/settings.json now scaffold as cc-guides layout
# dirs (a layout.toml + repo-local *.fragment.* pieces); `cc-guides render` composes
# the artifacts post-write. Shared across every layer (PROJECT_NAME=demo-proj).
FRAGMENT_DESTS = {
    ".claude/fragments/AGENTS.md/layout.toml",
    ".claude/fragments/AGENTS.md/demo-proj-development-guide.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-style.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-hook-style.fragment.md",
    ".claude/fragments/CLAUDE.md/layout.toml",
    ".claude/fragments/.claude/settings.json/layout.toml",
    ".claude/fragments/.claude/settings.json/settings-overrides.fragment.json",
}

BASE_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".mcp.json", ".claude/jj-config.toml",
    ".claude/hooks/packs.toml",  # capt-hook packs manifest, replaces vendored hook .py files
    ".claude/hooks/STYLEGUIDE.md",  # always-shipped capt-hook Python style guide
    ".github/workflows/guides.yml",  # cc-guides caller stub (check + re-render)
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

GO_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".mcp.json", ".claude/jj-config.toml", ".claude/hooks/packs.toml", ".claude/hooks/STYLEGUIDE.md",
    ".github/workflows/guides.yml",
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
    # release scaffolds the goreleaser config + the one-liner workflow + the Releases
    # AGENTS fragment; the cask is published by goreleaser (homebrew_casks:), so there's
    # no formula template.
    assert got == GO_DESTS | {
        ".goreleaser.yaml",
        ".github/workflows/release.yml",
        ".claude/fragments/AGENTS.md/releases.fragment.md",
    }
    assert ".github/formula/demo-proj.rb.tmpl" not in got


def test_go_overrides_base_for_shared_dest(go_var_pairs):
    r = scaffold.resolve("go", [], [], go_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    # the AGENTS.md layout dir + its local fragments override base at the same dests
    assert items[".claude/fragments/AGENTS.md/layout.toml"].src == "go/claude/fragments/AGENTS.md/layout.toml"
    assert (
        items[".claude/fragments/AGENTS.md/demo-proj-development-guide.fragment.md"].src
        == "go/claude/fragments/AGENTS.md/development-guide.fragment.md"
    )
    assert (
        items[".claude/fragments/.claude/settings.json/layout.toml"].src
        == "go/claude/fragments/settings.json/layout.toml"
    )
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
    # routing table; base-conventions.md and the fleet's deployed CLAUDE.md files
    # carry the same text, and the capt-hook `models` pack enforces it —
    # regressing would silently fork template from fleet and hooks.
    claude = (templates_dir.parents[4] / "plugin" / "guides" / "md" / "claude-rules.md").read_text()
    assert "max model/effort level" not in claude
    assert "**Models**" in claude
    assert "| fable-5 | 2 | 9 | 9 |" in claude
    assert "judge the output, not the price tag" in claude
    assert "`xhigh` by default" in claude
    # 2026-07-03 flip: opus-4.8 xhigh is the delegation default — opus is ~2x
    # cheaper AND less capable than fable, so fable→opus is a down-route and
    # escalation flows opus→fable only. Regressing either phrase would re-route
    # implementation subagents back to fable (or resurrect the backwards
    # escalation direction).
    assert "| opus-4.8 | 4 | 8 | 8 |" in claude
    assert "when in doubt, opus" in claude
    assert "when in doubt, fable" not in claude
    assert "escalation after fable misses the bar" not in claude
    # Implementation delegates to opus rather than fable editing inline on the
    # main loop — direct edits are where implementation actually happens (the
    # capt-hook main-loop nudge enforces the same directive).
    assert "rather than editing inline on fable" in claude
    # Sustained hands-on tool-driving (browser automation, QA sweeps) delegates too,
    # not just code edits — the capt-hook browser nudge enforces the same directive.
    assert "hands-on tool-driving" in claude
    # Context-window offload routes by task type, never by the fact of delegation.
    assert "not a routing cue" in claude
    # gpt-5.6-sol lanes: code/diff review + bug diagnosis, and the 2026-07-13
    # implementation split — well-scoped/clearly-bounded/terminal-heavy work routes
    # to sol, ambiguous/large-refactor/long-run stays on opus. Regressing either
    # phrase collapses the split back to the old single implementation lane.
    assert "code/diff review" in claude
    assert "bug diagnosis" in claude
    assert "well-scoped or clearly-bounded implementation" in claude
    assert "routes to gpt-5.6-sol instead" in claude
    assert "terminal/shell-heavy" in claude
    assert "ambiguous or exploratory" in claude
    assert "| fable-5 | 2 | 9 | 9 | Orchestration, design/architecture review" in claude
    assert "synthesis/accept-reject" in claude
    # All prose/writing routes to fable (capt-hook blocks non-fable pins on
    # writing prompts). Dropping the phrase would silently re-open down-routing
    # of docs and user-facing text.
    assert "never down-route writing" in claude
    # 2026-07-03: security review/audit + verification of security-sensitive code
    # route to gpt-5.6-sol; implementing that code stays fable (carve-out must survive).
    # "count as same-tier" keeps the verification-tier rule from contradicting the
    # gpt-5.6-sol lanes — without it agents refuse the routing (observed live).
    assert "security review/audit" in claude
    assert "verification of security-sensitive code" in claude
    assert "very sensitive or error-prone implementation" in claude
    assert "count as same-tier" in claude
    # 2026-07-11: gpt-5.6 family migration — sol is the codex-lane model (fast
    # tier pinned on every variant), luna sanctioned only for rote/bulk. Ultra
    # execution mode (exposed since codex 0.144.0) is explicitly NOT a retry rung;
    # regressing the phrase resurrects it as an escalation rung. A gpt-5.5 remnant
    # means a half-migrated stamp.
    assert "| gpt-5.6-sol | 9 | 8 | 5 |" in claude
    assert "gpt-5.6-luna" in claude
    assert "ultra execution mode" in claude
    assert "is not a retry rung" in claude
    assert "gpt-5.5" not in claude
    conventions = (templates_dir.parent / "reference" / "base-conventions.md").read_text()
    assert "security review/audit" in conventions
    assert "verification of" in conventions and "security-sensitive code" in conventions
    assert "gpt-5.6-sol" in conventions
    assert "gpt-5.5" not in conventions
    codex_skill = (templates_dir.parents[3] / "codex" / "skills" / "codex" / "SKILL.md").read_text()
    assert "security review/audit" in codex_skill
    assert "verification of security-sensitive code" in codex_skill
    assert "gpt-5.5" not in codex_skill
    # The writing-plans "model and effort per phase" clause moved into the cc-guides
    # writing-plans fragment (rendered into AGENTS.md downstream) and is pinned there.


def test_claude_md_check_back_on_the_unexpected(templates_dir):
    # 2026-07: delegated agents must not improvise when the unexpected changes the
    # task's shape — they stop and return findings + 2-4 options for the fable
    # orchestrator to pick; the decision never routes to a cheaper model. Transient
    # failures stay autonomous (AGENTS.md § General Rules), so the carve-out phrase
    # must survive too. base-conventions.md and the codex skill carry the same
    # contract; regressing any copy forks template from fleet.
    claude = (templates_dir.parents[4] / "plugin" / "guides" / "md" / "claude-rules.md").read_text()
    assert "**Check back on the unexpected.**" in claude
    assert "findings plus 2-4 concrete options" in claude
    assert "stay autonomous" in claude
    conventions = (templates_dir.parent / "reference" / "base-conventions.md").read_text()
    assert "the unexpected checks back" in conventions
    skill = templates_dir.parents[3] / "codex" / "skills" / "codex" / "SKILL.md"
    assert "never absorbs a surprise" in skill.read_text()


def test_codex_skill_pins_fast_tier_on_every_exec(templates_dir):
    # Every codex exec in the codex plugin (skill + wrapper agent) must pin the
    # model (gpt-5.6-sol — local config drift must not silently reroute the
    # lane), xhigh, and the fast service tier — without service_tier=fast,
    # xhigh prompts run 10-30+ minutes and get abandoned. Reply files must be
    # mktemp-unique: fixed $$-suffixed /tmp paths caused a live cross-session
    # clobber (PIDs recycle; 2026-07-10). Luna/ultra deviations live in prose
    # only; no example exec line may carry them.
    plugin_root = templates_dir.parents[3] / "codex"
    sources = [
        plugin_root / "skills" / "codex" / "SKILL.md",
        plugin_root / "agents" / "codex-wrapper.md",
    ]
    execs = []
    for src in sources:
        text = src.read_text()
        assert "codex-q-$$" not in text and "codex-r-$$" not in text, src
        assert "mktemp" in text, src
        execs += [line for line in text.splitlines() if "| codex exec" in line]
    assert execs, "expected codex exec invocations in the codex plugin"
    for line in execs:
        assert "-c model=gpt-5.6-sol" in line, line
        assert "-c model_reasoning_effort=xhigh" in line, line
        assert "-c service_tier=fast" in line, line
        # Quiet exec: capture the reply to a file (-o), stream JSONL events, and
        # redirect that stream to a log so only REPLY_FILE:/LOG_FILE: (or a failure
        # tail) reach the conversation. Dropping any of these floods the caller's
        # window with the banner + progress trace.
        assert '-o "' in line, line
        assert "--json" in line, line
        assert "--color never" in line, line
        assert "2>&1" in line, line
    # The reply/log markers and the failure tail (plus the direct-piping recipe's
    # `cat "$R"`) are the only output that reaches the conversation; losing them
    # silently reverts to streaming stdout.
    for src in sources:
        text = src.read_text()
        assert "REPLY_FILE:" in text, src
        assert "LOG_FILE:" in text, src
        assert "|| tail -20" in text, src


def test_codex_scratchpad_fallback_is_non_improvisable(templates_dir):
    # Codex temp files land in the session scratchpad, else a fresh `mktemp -d` —
    # never an invented directory. A repo-relative name (e.g. `.claude-scratch/`)
    # lands in the working tree and gets committed by auto-snapshot, so the base
    # gitignore also excludes it as a backstop.
    plugin_root = templates_dir.parents[3] / "codex"
    for src in (
        plugin_root / "skills" / "codex" / "SKILL.md",
        plugin_root / "agents" / "codex-wrapper.md",
    ):
        text = src.read_text()
        assert "mktemp -d" in text, src
        assert "S=$(mktemp -d)" in text, src
        assert "repo-relative" in text, src
    gitignore = (templates_dir / "base" / "gitignore").read_text()
    assert ".claude-scratch/" in gitignore


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


# --- plugin extra: the canonical binary installer ---


def test_plugin_extra_gating(base_var_pairs, go_var_pairs):
    dest = ".claude/fragments/plugin/scripts/install-binary.sh/layout.toml"
    assert dest not in dests("base", base_var_pairs)
    assert dest in dests("base", base_var_pairs, extras=["plugin"])
    # layer-independent, like every extra
    assert dest in dests("go", go_var_pairs, extras=["plugin"], features=[])


def test_plugin_extra_mode_sections(base_var_pairs, plugin_var_pairs):
    # exactly one of PINNED/LATEST, defaulting to pinned; absent without the extra
    pinned = scaffold.resolve("base", ["plugin"], [], plugin_var_pairs, DATE)
    assert "PINNED" in pinned.enabled_sections and "LATEST" not in pinned.enabled_sections
    latest = scaffold.resolve(
        "base", ["plugin"], [], plugin_var_pairs + ["BINARY_VERSION_MODE=latest"], DATE
    )
    assert "LATEST" in latest.enabled_sections and "PINNED" not in latest.enabled_sections
    plain = scaffold.resolve("base", [], [], base_var_pairs, DATE)
    assert not {"PINNED", "LATEST"} & plain.enabled_sections


def test_bad_binary_version_mode(plugin_var_pairs):
    pairs = plugin_var_pairs + ["BINARY_VERSION_MODE=nightly"]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("base", ["plugin"], [], pairs, DATE)


def test_plugin_installer_renders_pinned(plugin_var_pairs):
    # the layout.toml imports the pinned installer fragment with the binary args;
    # `cc-guides render` (post-write) composes it into the real installer upstream.
    plan, _ = _real_plan("base", plugin_var_pairs, extras=["plugin"])
    toml = plan[".claude/fragments/plugin/scripts/install-binary.sh/layout.toml"]
    assert toml == (
        'fragments = [{ use = "cc-skills:install-binary-pinned", args = '
        '{ binary = "demo-proj", brew = "janedoe/tap/demo-proj", '
        'plugin = "demo-proj", repo = "janedoe/demo-proj" } }]\n\n'
        '[sources.cc-skills]\nsource = "github:yasyf/cc-skills@main"\n'
    )


def test_plugin_installer_renders_latest(plugin_var_pairs):
    plan, _ = _real_plan("base", plugin_var_pairs + ["BINARY_VERSION_MODE=latest"], extras=["plugin"])
    toml = plan[".claude/fragments/plugin/scripts/install-binary.sh/layout.toml"]
    assert toml == (
        'fragments = [{ use = "cc-skills:install-binary-latest", args = '
        '{ binary = "demo-proj", brew = "janedoe/tap/demo-proj", '
        'plugin = "demo-proj", repo = "janedoe/demo-proj" } }]\n\n'
        '[sources.cc-skills]\nsource = "github:yasyf/cc-skills@main"\n'
    )


def test_plugin_installer_missing_tokens_fail_loudly(base_var_pairs):
    # extras have no required-var machinery; the {{BINARY_NAME}} etc. tokens survive the
    # section render unresolved, and the unrendered-placeholder scan fails loudly for them
    with pytest.raises(ScaffoldError):
        _real_plan("base", base_var_pairs, extras=["plugin"])


def test_python_overrides_base_for_shared_dest(py_var_pairs):
    # the AGENTS.md layout dir exists in both layers; the python spec must win.
    r = scaffold.resolve("python", [], ["docs", "pypi"], py_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    assert items[".claude/fragments/AGENTS.md/layout.toml"].src == "python/claude/fragments/AGENTS.md/layout.toml"
    assert (
        items[".claude/fragments/AGENTS.md/demo-proj-style.fragment.md"].src
        == "python/claude/fragments/AGENTS.md/style.fragment.md"
    )
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


def _real_plan(layer, var_pairs, *, features=None, extras=None, secondary_layer=None):
    r = scaffold.resolve(
        layer, extras or [], features if features is not None else ["docs", "pypi"], var_pairs, DATE, secondary_layer
    )
    items = scaffold.select_files(r)
    return scaffold.render_plan(items, r, scaffold.read_template, scaffold.template_exists)


def _license_none(var_pairs):
    return [p for p in var_pairs if not p.startswith("LICENSE_ID=")] + ["LICENSE_ID=none"]


def test_real_templates_render_license_references(base_var_pairs, py_var_pairs):
    plan, notices = _real_plan("python", py_var_pairs)
    assert "MIT License" in plan["LICENSE"]
    assert "License: MIT" in plan["README.md"]
    assert "Licensed under [MIT](LICENSE)." in _real_plan("base", base_var_pairs)[0]["README.md"]
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
    # the README seed carries no provenance envelope anymore — with license none the
    # footer's HAS_LICENSE block drops and the file ends on the footer's TODO line
    assert base_plan["README.md"].endswith("delete this line.\n")


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
    # go AGENTS.md now composes from a layout dir: the shared collaboration guides are
    # cc-skills imports in layout.toml (`cc-guides render` composes them post-write)
    layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
    assert '"cc-skills:ask-before-assuming"' in layout
    assert '"cc-skills:parallelize"' in layout
    assert '"cc-skills:writing-plans"' in layout
    assert '"cc-skills:version-control"' in layout
    assert 'source = "github:yasyf/cc-skills@main"' in layout
    # release on -> the Releases rule ships as its own fragment, listed after version-control
    assert '"releases"' in layout
    assert "**Releases.**" in plan[".claude/fragments/AGENTS.md/releases.fragment.md"]
    assert "brew install janedoe/tap/demo-proj" in plan["README.md"]


def test_packs_toml_pins_guard_packs(base_var_pairs, py_var_pairs, go_var_pairs, swift_var_pairs):
    # Every layer's packs.toml pins the ccx and cc-present guard packs repo-scoped
    # (alongside the plugins' session attach) so every contributor gets the guards.
    for layer, pairs in (
        ("base", base_var_pairs),
        ("python", py_var_pairs),
        ("go", go_var_pairs),
        ("swift", swift_var_pairs),
    ):
        parsed = tomllib.loads(_real_plan(layer, pairs)[0][".claude/hooks/packs.toml"])
        assert parsed["packs"]["ccx"]["source"] == "github:yasyf/cc-context@latest", f"{layer} ccx pin"
        assert parsed["packs"]["cc-present"]["source"] == "github:yasyf/cc-present@latest", f"{layer} cc-present pin"


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
    # release off -> the Releases fragment is not scaffolded and the layout omits it
    assert ".claude/fragments/AGENTS.md/releases.fragment.md" not in plan
    assert '"releases"' not in plan[".claude/fragments/AGENTS.md/layout.toml"]
    # README falls back to go install / task build, no brew line
    assert "brew install" not in plan["README.md"]
    assert "go install github.com/janedoe/demo-proj/cmd/demo-proj@latest" in plan["README.md"]


@pytest.mark.parametrize("layer", ["base", "python"])
def test_real_templates_render_orchestrator_conventions(layer, base_var_pairs, py_var_pairs):
    plan, _ = _real_plan(layer, base_var_pairs if layer == "base" else py_var_pairs)
    # CLAUDE.md now imports the shared cc-skills:claude-rules guide; no local fragment
    claude_layout = plan[".claude/fragments/CLAUDE.md/layout.toml"]
    assert '"cc-skills:claude-rules"' in claude_layout
    assert 'source = "github:yasyf/cc-skills@main"' in claude_layout
    assert ".claude/fragments/CLAUDE.md/claude-specific-rules.fragment.md" not in plan
    # the parallelize/writing-plans guidance rides cc-skills imports in the layout now
    layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
    assert '"cc-skills:parallelize"' in layout
    assert '"cc-skills:writing-plans"' in layout


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


def test_expand_partials_rejects_bare_names():
    # bare-name directives are gone (shared fragments compose through cc-guides layout
    # dirs now); any non-`_partials/` directive is a mistake and must fail loudly.
    for text in (
        "before\n{{> ccx}}\nafter\n",
        "{{> install-binary-pinned binary=x repo=y brew=z plugin=w}}\n",
    ):
        with pytest.raises(ScaffoldError):
            scaffold.expand_partials(text, _missing)


def test_expand_partials_recurses():
    templates = {"_partials/a.md": "A {{> _partials/b.md}}\n", "_partials/b.md": "B\n"}
    assert scaffold.expand_partials("{{> _partials/a.md}}\n", templates.__getitem__) == "A B\n"


def test_expand_partials_unknown_raises():
    with pytest.raises(ScaffoldError):
        scaffold.expand_partials("{{> _partials/missing.md}}", _missing)


def test_expand_partials_cycle_raises():
    templates = {"_partials/a.md": "{{> _partials/b.md}}", "_partials/b.md": "{{> _partials/a.md}}"}
    with pytest.raises(ScaffoldError):
        scaffold.expand_partials("{{> _partials/a.md}}", templates.__getitem__)


def test_real_templates_share_version_control_directive(base_var_pairs, py_var_pairs):
    # the shared collaboration guides are cc-skills imports in every AGENTS layout;
    # their bodies live upstream and `cc-guides render` composes them downstream.
    base_plan, _ = _real_plan("base", base_var_pairs)
    py_plan, _ = _real_plan("python", py_var_pairs)
    for plan in (base_plan, py_plan):
        layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
        style = plan[".claude/fragments/AGENTS.md/demo-proj-style.fragment.md"]
        assert '"cc-skills:version-control"' in layout
        assert "**Version control.**" not in layout  # body NOT inlined at scaffold time
        assert "**Version control.**" not in style
    # no _partials/ seed is ever written as a destination file
    assert not any(d.startswith("_partials") for d in {**base_plan, **py_plan})
    # python lists the pypi-gated Releases fragment right after version-control; base has none
    assert '"cc-skills:version-control",\n  "releases",' in py_plan[".claude/fragments/AGENTS.md/layout.toml"]
    assert "**Releases.**" in py_plan[".claude/fragments/AGENTS.md/releases.fragment.md"]
    assert ".claude/fragments/AGENTS.md/releases.fragment.md" not in base_plan


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


# --- guides.yml caller stub + cc-context marketplace ---

def test_base_emits_guides_yml(base_var_pairs):
    assert ".github/workflows/guides.yml" in dests("base", base_var_pairs)
    gy = _real_plan("base", base_var_pairs)[0][".github/workflows/guides.yml"]
    assert "uses: yasyf/cc-guides@action-v1" in gy
    assert "yasyf/cc-guides/.github/workflows/re-render.yml@action-v1" in gy
    assert "types: [cc-guides-render]" in gy


def test_settings_json_composes_from_pack_fragments(base_var_pairs):
    # settings.json is a cc-guides artifact now: the base layout imports
    # `cc-skills:settings-base` (which carries the cc-context marketplace + enabled
    # plugin) plus a placeholder-free `{}` settings-overrides overlay for
    # repo-specific additions.
    plan, _ = _real_plan("base", base_var_pairs)
    layout = plan[".claude/fragments/.claude/settings.json/layout.toml"]
    assert '"cc-skills:settings-base"' in layout
    assert '"settings-overrides"' in layout
    assert 'source = "github:yasyf/cc-skills@main"' in layout
    assert json.loads(plan[".claude/fragments/.claude/settings.json/settings-overrides.fragment.json"]) == {}


# --- run(): post-write cc-guides render (stubbed on PATH) ---

def _run_args(target, *, layer="base", secondary_layer=None, extras="none", features="", var_pairs, force=False, dry_run=False):
    return argparse.Namespace(
        target=target, layer=layer, secondary_layer=secondary_layer, extras=extras, features=features,
        var=var_pairs, force=force, dry_run=dry_run,
    )


def test_run_invokes_cc_guides_render(tmp_path, cc_guides_stub, base_var_pairs):
    assert scaffold.run(_run_args(tmp_path, var_pairs=base_var_pairs)) == 0
    # the stub wrote its marker in the target dir — proof render ran there (cwd=target)
    assert (tmp_path / ".cc-guides-stub").exists()
    # layout dirs were written and the stub composed their artifacts in place
    assert (tmp_path / ".claude/fragments/AGENTS.md/layout.toml").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()


def test_run_missing_cc_guides_raises(tmp_path, monkeypatch, base_var_pairs, capsys):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(ScaffoldError):
        scaffold.run(_run_args(tmp_path, var_pairs=base_var_pairs))
    assert "brew install yasyf/tap/cc-guides" in capsys.readouterr().err


def test_run_dry_run_skips_render(tmp_path, monkeypatch, base_var_pairs):
    # dry-run writes nothing, so it must not require cc-guides even when absent
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert scaffold.run(_run_args(tmp_path, var_pairs=base_var_pairs, dry_run=True)) == 0
    assert not (tmp_path / ".claude/fragments/AGENTS.md/layout.toml").exists()


# --- Part 2: capt-hook hook styleguide ships in every layer ---


def test_hook_styleguide_shipped_base(base_var_pairs):
    plan, _ = _real_plan("base", base_var_pairs)
    assert ".claude/hooks/STYLEGUIDE.md" in plan
    assert "Hook Style Guide" in plan[".claude/hooks/STYLEGUIDE.md"]
    frag = plan[".claude/fragments/AGENTS.md/demo-proj-hook-style.fragment.md"]
    assert "## Hook Style" in frag
    assert ".claude/hooks/STYLEGUIDE.md" in frag


def test_hook_styleguide_shipped_go(go_var_pairs):
    plan, _ = _real_plan("go", go_var_pairs, features=[])
    assert ".claude/hooks/STYLEGUIDE.md" in plan
    layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
    assert '"demo-proj-hook-style"' in layout
    assert "{{#SECONDARY_STYLE}}" not in layout  # no secondary layer -> section stripped


# --- Part 1: --secondary-layer python lands beside its code without clobbering ---


def _secondary(var_pairs, root="plugin/hooks"):
    return var_pairs + [f"SECONDARY_CODE_ROOT={root}"]


def test_secondary_python_reproduces_cc_context_shape(go_var_pairs):
    # --layer go --secondary-layer python --var SECONDARY_CODE_ROOT=plugin/hooks
    plan, _ = _real_plan("go", _secondary(go_var_pairs), features=[], secondary_layer="python")
    # primary Go styleguide keeps the repo-root STYLEGUIDE.md
    assert "governs the Python" not in plan["STYLEGUIDE.md"]
    assert "this module" in plan["STYLEGUIDE.md"]  # the go root styleguide
    # the secondary python styleguide lands beside the code, not at the root
    assert "governs the Python" in plan["plugin/hooks/STYLEGUIDE.md"]
    assert "plugin/hooks/" in plan["plugin/hooks/STYLEGUIDE.md"]
    # AGENTS ## Python Style pointer references the code-root styleguide
    ptr = plan[".claude/fragments/AGENTS.md/demo-proj-secondary-style.fragment.md"]
    assert "## Python Style" in ptr
    assert "plugin/hooks/STYLEGUIDE.md" in ptr
    # the go layout.toml composes both secondary + hook style fragments (section resolved)
    layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
    assert '"demo-proj-secondary-style"' in layout
    assert '"demo-proj-hook-style"' in layout
    assert "SECONDARY_STYLE" not in layout


def test_secondary_python_dests(go_var_pairs):
    got = dests("go", _secondary(go_var_pairs), features=[], secondary_layer="python")
    assert "plugin/hooks/STYLEGUIDE.md" in got
    assert ".claude/fragments/AGENTS.md/demo-proj-secondary-style.fragment.md" in got
    # the primary root styleguide is still there, unclobbered
    assert "STYLEGUIDE.md" in got


def test_no_secondary_layer_omits_python_style(go_var_pairs):
    got = dests("go", go_var_pairs, features=[])
    assert "plugin/hooks/STYLEGUIDE.md" not in got
    assert ".claude/fragments/AGENTS.md/demo-proj-secondary-style.fragment.md" not in got
    plan, _ = _real_plan("go", go_var_pairs, features=[])
    assert '"demo-proj-secondary-style"' not in plan[".claude/fragments/AGENTS.md/layout.toml"]


def test_secondary_layer_must_differ_from_layer(py_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("python", [], [], _secondary(py_var_pairs), DATE, "python")


def test_secondary_layer_requires_code_root(go_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("go", [], [], go_var_pairs, DATE, "python")


def test_unknown_secondary_layer_rejected(go_var_pairs):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("go", [], [], _secondary(go_var_pairs), DATE, "rust")


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "has space", "trailing/", ".", "a/./b"])
def test_secondary_code_root_rejects_bad_path(go_var_pairs, bad):
    with pytest.raises(ScaffoldError):
        scaffold.resolve("go", [], [], _secondary(go_var_pairs, bad), DATE, "python")


def test_secondary_python_writes_both_styleguides_end_to_end(tmp_path, cc_guides_stub, go_var_pairs):
    args = _run_args(tmp_path, layer="go", secondary_layer="python", var_pairs=_secondary(go_var_pairs))
    assert scaffold.run(args) == 0
    root = (tmp_path / "STYLEGUIDE.md").read_text()
    secondary = (tmp_path / "plugin/hooks/STYLEGUIDE.md").read_text()
    assert "governs the Python" not in root and "this module" in root
    assert "governs the Python" in secondary
    assert (tmp_path / ".claude/hooks/STYLEGUIDE.md").exists()
    assert (tmp_path / ".claude/fragments/AGENTS.md/demo-proj-secondary-style.fragment.md").exists()
    assert (tmp_path / ".claude/fragments/AGENTS.md/demo-proj-hook-style.fragment.md").exists()
