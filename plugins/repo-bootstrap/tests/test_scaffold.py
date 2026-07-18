"""Phase pipeline: resolve/validate, selection matrix, derive, render_plan,
transforms, and apply_plan. All pure/offline."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import select
import shutil
import signal
import subprocess
import threading
import time
import tomllib
from pathlib import Path

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

# AGENTS.md, CLAUDE.md, .claude/settings.json, .mcp.json, and .claude/capt-hook.toml scaffold
# as cc-guides layout dirs (a layout.toml + repo-local *.fragment.* pieces); `cc-guides render`
# composes the artifacts post-write. Shared across every layer (PROJECT_NAME=demo-proj).
FRAGMENT_DESTS = {
    ".claude/fragments/AGENTS.md/layout.toml",
    ".claude/fragments/AGENTS.md/demo-proj-development-guide.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-style.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-hook-style.fragment.md",
    ".claude/fragments/CLAUDE.md/layout.toml",
    ".claude/fragments/.claude/settings.json/layout.toml",
    ".claude/fragments/.claude/settings.json/settings-overrides.fragment.json",
    ".claude/fragments/.mcp.json/layout.toml",
    ".claude/fragments/.mcp.json/mcp-overrides.fragment.json",
    ".claude/fragments/.claude/capt-hook.toml/layout.toml",  # capt-hook pack enablement layout
}

BASE_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".claude/jj-config.toml",
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
    assert ".claude/fragments/great-docs.yml/layout.toml" in got  # docs
    assert ".github/workflows/release-pypi.yml" in got  # pypi
    assert got >= BASE_DESTS  # python implies base
    assert "{{PACKAGE}}/cli.py" not in got


def test_python_docs_only_drops_pypi(py_var_pairs):
    got = dests("python", py_var_pairs, features=["docs"])
    assert ".claude/fragments/great-docs.yml/layout.toml" in got
    assert ".claude/fragments/.github/workflows/docs.yml/layout.toml" in got
    # the standalone docs scripts are gone — gd-build materializes them at build time
    assert "docs/scripts/native_reference_titles.py" not in got
    assert "docs/scripts/fix_color_swatch.py" not in got
    assert ".github/workflows/release-pypi.yml" not in got


def test_python_pypi_only_drops_docs(py_var_pairs):
    got = dests("python", py_var_pairs, features=["pypi"])
    assert ".github/workflows/release-pypi.yml" in got
    for docs_only in (".claude/fragments/great-docs.yml/layout.toml",
                      ".claude/fragments/great-docs.yml/great-docs-repo.fragment.yml",
                      ".claude/fragments/.github/workflows/docs.yml/layout.toml"):
        assert docs_only not in got


def test_python_no_features_drops_all_gated(py_var_pairs):
    got = dests("python", py_var_pairs, features=[])
    for gated in (".claude/fragments/great-docs.yml/layout.toml",
                  ".claude/fragments/.github/workflows/docs.yml/layout.toml",
                  ".claude/fragments/.github/workflows/docs.yml/docs-build-preamble.fragment.yml",
                  ".github/workflows/release-pypi.yml"):
        assert gated not in got


# --- go layer selection ---

GO_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".claude/jj-config.toml", ".claude/hooks/STYLEGUIDE.md",
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
    assert (
        items[".claude/fragments/.claude/capt-hook.toml/layout.toml"].src
        == "go/claude/fragments/capt-hook.toml/layout.toml"
    )


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
    # v5 2026-07-14 decision-density split: sol owns bounded decision-light impl,
    # opus keeps ambiguous/decision-dense — regressing collapses the lanes.
    assert "code/diff review" in claude
    assert "bug diagnosis" in claude
    assert "the default implementation lane for bounded, decision-light changes to existing code" in claude
    assert "fans out to gpt-5.6-sol" in claude
    assert "terminal/shell-heavy" in claude
    assert "ambiguous, exploratory, long-horizon, decision-dense, or large net-new" in claude
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
    # gpt-5.6 lanes v6 (cc-notes routing-defaults experiment): sol Cost 3, large
    # net-new stays opus; recon lane defaults to luna. Ultra is not a retry rung.
    assert "| gpt-5.6-sol | 3 | 8 | 5 |" in claude
    assert "gpt-5.6-luna" in claude
    assert "recon lane" in claude
    assert "net-new code stay on opus" in claude
    assert "ultra execution mode" in claude
    assert "is not a retry rung" in claude
    assert "gpt-5.5" not in claude
    conventions = (templates_dir.parent / "reference" / "base-conventions.md").read_text()
    assert "security review/audit" in conventions
    assert "verification of" in conventions and "security-sensitive code" in conventions
    assert "gpt-5.6-sol" in conventions
    assert "recon lane" in conventions
    assert "gpt-5.5" not in conventions
    codex_skill = (templates_dir.parents[3] / "codex" / "skills" / "codex" / "SKILL.md").read_text()
    assert "security review/audit" in codex_skill
    assert "verification of security-sensitive code" in codex_skill
    assert "recon lane" in codex_skill
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


def test_codex_ask_pins_fast_tier_and_quiet_exec(templates_dir):
    # codex-ask pins model/effort/fast-tier + quiet-exec flags and runs codex
    # detached (print-first + --await); skill and wrapper route through it.
    plugin_root = templates_dir.parents[3] / "codex"
    script = plugin_root / "bin" / "codex-ask"
    assert os.access(script, os.X_OK), script
    text = script.read_text()
    for needle in (
        "MODEL=gpt-5.6-sol",
        "MODEL=gpt-5.6-luna",
        "EFFORT=xhigh",
        '-c model="$MODEL"',
        '-c model_reasoning_effort="$EFFORT"',
        "-c service_tier=fast",
        "developer_instructions=",
        "--sandbox danger-full-access",
        '-o "$R.tmp"',
        "--json",
        "--color never",
        "2>&1",
        "REPLY_FILE:",
        "LOG_FILE:",
        "tail -20",
        "unset OPENAI_API_KEY",
        "TMPDIR=/tmp",
        "S=$(mint_scratch codex-ask)",
        'mktemp -d "$TMPDIR/$1.XXXXXX"',
        "codex-q-XXXXXX",
        "codex-r-XXXXXX",
        # v2: detached-survivable launch + --await recovery
        "AWAIT:",
        "--await",
        "set -m",
        'mv -f "$S/status.tmp" "$S/status"',
        '"$S/pid"',
        # v2: exit 0 + empty reply is a silent codex death, treated as failure
        "codex exited 0 but wrote no reply",
        # v3 disk-truth: staged reply, meta wipe, collect, completion marker
        'mv -f "$R.tmp" "$R"',
        'rm -f "$S/status" "$S/pid" "$S/meta"',
        "--collect",
        '"type":"turn.started"',
        '"type":"turn.completed"',
        "died mid-turn",
        # v3 opt-in schema passthrough (verdict lanes only)
        "--schema",
        "--output-schema",
    ):
        assert needle in text, needle
    assert "codex-q-$$" not in text and "codex-r-$$" not in text
    # print-first: the recovery paths echo before codex is ever invoked
    assert text.index('echo "REPLY_FILE: ') < text.index("codex exec -c")
    skill_md = plugin_root / "skills" / "codex" / "SKILL.md"
    wrapper_md = plugin_root / "agents" / "codex-wrapper.md"
    for src in (skill_md, wrapper_md):
        t = src.read_text()
        assert "codex-ask" in t, src
        recipe_lines = [line for line in t.splitlines() if "| codex exec" in line or "codex exec -c" in line]
        assert not recipe_lines, (src, recipe_lines)
        assert "REPLY_FILE:" in t, src
        assert "LOG_FILE:" in t, src
        # v2 docs: --await documented
        assert "--await" in t, src
    # check-back contract pinned verbatim in each (phrased differently per file)
    assert "never absorbs a surprise" in skill_md.read_text()
    assert "Never absorb a surprise" in wrapper_md.read_text()
    # wrapper bans backgrounding the codex call (orphaned-verdict failure mode)
    assert "run_in_background" in wrapper_md.read_text()


def test_codex_ask_scratch_is_non_improvisable(templates_dir, tmp_path):
    # Scratch paths are absolute-by-construction (live .scratch/codex leak,
    # 2026-07-13); a stub codex proves flags + OPENAI_API_KEY unset end to end.
    script = templates_dir.parents[3] / "codex" / "bin" / "codex-ask"
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        'echo "STUB_ARGS: $*"\n'
        'echo "STUB_KEY: ${OPENAI_API_KEY:-UNSET}"\n'
        '[ -n "$out" ] && echo pong > "$out"\n'
    )
    stub.chmod(0o755)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        "OPENAI_API_KEY": "sk-dummy",
        "TMPDIR": str(tmpdir),
    }
    # pytest runs inside a Claude session; a leaked id + a real on-PATH
    # cc-transcript would redirect scratch files into the live session scratchpad.
    env.pop("CLAUDE_CODE_SESSION_ID", None)

    def ask(*args, env=env):
        return subprocess.run([str(script), *args], cwd=cwd, env=env, text=True, capture_output=True)

    run = ask("ping")
    assert run.returncode == 0, run.stderr
    reply = re.search(r"^REPLY_FILE: (.+)$", run.stdout, re.M).group(1)
    log = re.search(r"^LOG_FILE: (.+)$", run.stdout, re.M).group(1)
    assert os.path.isabs(reply) and os.path.isabs(log)
    assert reply.startswith(str(tmpdir) + os.sep)
    assert not list(cwd.iterdir())
    assert Path(reply).read_text().strip() == "pong"
    log_text = Path(log).read_text()
    assert "model=gpt-5.6-sol" in log_text
    assert "model_reasoning_effort=xhigh" in log_text
    assert "service_tier=fast" in log_text
    assert "STUB_KEY: UNSET" in log_text

    # a relative TMPDIR must sanitize to /tmp, never resolve against cwd
    rel_tmp = ask("ping", env={**env, "TMPDIR": "."})
    assert rel_tmp.returncode == 0, rel_tmp.stderr
    r2 = re.search(r"^REPLY_FILE: (.+)$", rel_tmp.stdout, re.M).group(1)
    assert os.path.isabs(r2)
    assert not r2.startswith(str(cwd))
    assert not list(cwd.iterdir())

    rel = ask("-s", ".scratch", "ping")
    assert rel.returncode == 2, rel.stdout + rel.stderr
    assert not list(cwd.iterdir())

    # absolute but inside the caller's repo is the original leak shape
    subprocess.run(["git", "init", "-q", str(cwd)], check=True, env=env, capture_output=True)
    inrepo = ask("-s", f"{cwd}/.scratch", "ping")
    assert inrepo.returncode == 2, inrepo.stdout + inrepo.stderr
    assert not (cwd / ".scratch").exists()

    sdir = tmp_path / "scratch"
    absr = ask("-s", str(sdir), "-m", "luna", "--image", "--skip-git-repo-check", "ping")
    assert absr.returncode == 0, absr.stderr
    assert re.search(r"^REPLY_FILE: (.+)$", absr.stdout, re.M).group(1).startswith(str(sdir) + os.sep)
    luna_log = Path(re.search(r"^LOG_FILE: (.+)$", absr.stdout, re.M).group(1)).read_text()
    assert "model=gpt-5.6-luna" in luna_log
    assert "model_reasoning_effort=xhigh" in luna_log
    assert "service_tier=fast" in luna_log
    assert "--disable shell_tool" in luna_log
    assert "--skip-git-repo-check" in luna_log

    stub.write_text('#!/bin/sh\ncat > /dev/null\necho "boom event"\nexit 7\n')
    fail = ask("ping")
    assert fail.returncode == 7, fail.stdout + fail.stderr
    assert "boom event" in fail.stdout
    assert "REPLY_FILE:" in fail.stdout
    assert "LOG_FILE:" in fail.stdout

    # gitignore backstop: a repo-relative scratch dir must never reach a commit
    gitignore = (templates_dir / "base" / "gitignore").read_text()
    assert ".claude-scratch/" in gitignore
    assert ".scratch/" in gitignore
    assert ".claude/worktrees/" in gitignore


def _codex_env(stub_bin, tmpdir, **extra):
    # Controlled env: stub codex on PATH, sandboxed TMPDIR, no leaked session id
    # (pytest runs in a Claude session; a real cc-transcript would else adopt it).
    env = {**os.environ, "PATH": f"{stub_bin}:{os.environ['PATH']}", "TMPDIR": str(tmpdir)}
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(extra)
    return env


def _read_lines(proc, count, deadline_s):
    # Raw-fd reads: robust regardless of pipe buffering.
    fd = proc.stdout.fileno()
    buf = b""
    end = time.monotonic() + deadline_s
    while buf.count(b"\n") < count and time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], max(0.0, end - time.monotonic()))
        if r:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
    return buf.decode().splitlines()


def _read_lines_buffered(proc, count, deadline_s):
    # Plain buffered readline in a thread (proc must be text=True); a regression
    # that re-holds the pipe blocks here until the join deadline trips.
    lines: list[str] = []

    def reader() -> None:
        for _ in range(count):
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(deadline_s)
    return lines


def _descendants(pid):
    # Every transitive child of pid via the ps pid/ppid tree.
    out = subprocess.run(["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True).stdout
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    seen: list[int] = []
    stack = [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.append(child)
                stack.append(child)
    return seen


def test_codex_ask_run_survives_pgid_kill_and_await_recovers(templates_dir, tmp_path):
    # Claude Code kills a timed-out Bash command across the whole process group;
    # codex-ask detaches codex (own pgroup, orphaned to PID 1) so it survives.
    script = templates_dir.parents[3] / "codex" / "bin" / "codex-ask"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        "sleep 3\n"
        '[ -n "$out" ] && printf "REAL REPLY 42\\n" > "$out"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = _codex_env(stub_bin, tmpdir)

    proc = subprocess.Popen(
        [str(script), "ping"],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        bufsize=0,
    )
    header = _read_lines(proc, 3, 5)
    reply = next((l.split(": ", 1)[1] for l in header if l.startswith("REPLY_FILE:")), None)
    assert reply, header
    scratch = os.path.dirname(reply)
    assert any(l.startswith("AWAIT:") and "--await" in l for l in header), header

    # Kill only once the run is established (pid file lands after the job forks
    # into its own pgroup); print-first emits before the detach, racing otherwise.
    pid_file = Path(scratch) / "pid"
    reg = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < reg:
        time.sleep(0.02)
    assert pid_file.exists(), "detached run never registered a pid"

    # Claude Code's full timeout kill: SIGTERM/SIGKILL the process GROUP and every
    # ps-walked descendant, 1500ms apart. A run left a launcher descendant dies.
    pgid = os.getpgid(proc.pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        victims = _descendants(proc.pid)
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        for d in victims:
            try:
                os.kill(d, sig)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(1.5)
    assert proc.wait(timeout=10) != 0  # the launcher itself was killed

    recovered = subprocess.run(
        [str(script), "--await", scratch], cwd=cwd, env=env, text=True, capture_output=True, timeout=30
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    r_reply = re.search(r"^REPLY_FILE: (.+)$", recovered.stdout, re.M).group(1)
    assert Path(r_reply).read_text().strip() == "REAL REPLY 42"

    # --await accepts a reply-file (resolved to its dir) and is idempotent
    again = subprocess.run(
        [str(script), "--await", reply], cwd=cwd, env=env, text=True, capture_output=True, timeout=30
    )
    assert again.returncode == 0
    assert re.search(r"^REPLY_FILE: (.+)$", again.stdout, re.M).group(1) == reply


def test_codex_ask_await_failure_status_and_clean_errors(templates_dir, tmp_path):
    # Exit codes flow through the status file; --await replays a failure's tail,
    # a reused -s dir never serves stale state, bad targets/no codex fail cleanly.
    script = templates_dir.parents[3] / "codex" / "bin" / "codex-ask"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "codex"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    cwd = tmp_path / "repo"
    cwd.mkdir()
    env = _codex_env(stub_bin, tmpdir)

    def ask(*args, e=env):
        return subprocess.run([str(script), *args], cwd=cwd, env=e, text=True, capture_output=True, timeout=30)

    # exit-7 stub: sync exits 7 with the log tail; --await replays it
    stub.write_text('#!/bin/sh\ncat > /dev/null\necho "boom detail"\nexit 7\n')
    stub.chmod(0o755)
    sdir = tmp_path / "s1"
    run = ask("-s", str(sdir), "ping")
    assert run.returncode == 7, run.stdout + run.stderr
    assert "boom detail" in run.stdout
    aw = ask("--await", str(sdir))
    assert aw.returncode == 7, aw.stdout + aw.stderr
    assert "boom detail" in aw.stdout
    assert "REPLY_FILE:" in aw.stdout

    # exit 0 but empty reply -> silent codex death: nonzero exit + log tail,
    # both sync and via --await
    stub.write_text('#!/bin/sh\ncat > /dev/null\necho "turn cut off"\nexit 0\n')
    edir = tmp_path / "s-empty"
    empty_run = ask("-s", str(edir), "ping")
    assert empty_run.returncode == 1, empty_run.stdout + empty_run.stderr
    assert "turn cut off" in empty_run.stdout
    assert "codex exited 0 but wrote no reply" in empty_run.stderr
    empty_await = ask("--await", str(edir))
    assert empty_await.returncode == 1, empty_await.stdout + empty_await.stderr
    assert "turn cut off" in empty_await.stdout
    assert "codex exited 0 but wrote no reply" in empty_await.stderr

    # stale-state guard: reusing the same -s dir with a success stub must not
    # serve the previous run's exit-7 status
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        '[ -n "$out" ] && echo fresh > "$out"\n'
        "exit 0\n"
    )
    reuse = ask("-s", str(sdir), "ping")
    assert reuse.returncode == 0, reuse.stdout + reuse.stderr

    # --await a dir with no recorded run -> rc 2, clean message
    empty = tmp_path / "empty"
    empty.mkdir()
    norun = ask("--await", str(empty))
    assert norun.returncode == 2
    assert "no recorded" in norun.stderr.lower()

    # --await a relative path -> rc 2, nothing written to cwd
    relaw = ask("--await", "relative/path")
    assert relaw.returncode == 2
    assert not list(cwd.iterdir())

    # no codex binary at all -> status 127. Assert the precondition so a stray
    # real /usr/bin/codex can't let this pass for the wrong reason.
    bare_path = "/usr/bin:/bin"
    assert shutil.which("codex", path=bare_path) is None, "test PATH must resolve no real codex"
    bare = _codex_env(stub_bin, tmpdir, PATH=bare_path)
    nocodex = subprocess.run([str(script), "ping"], cwd=cwd, env=bare, text=True, capture_output=True, timeout=30)
    assert nocodex.returncode == 127, nocodex.stdout + nocodex.stderr


def test_codex_ask_adopts_harness_scratchpad(templates_dir, tmp_path):
    # With a session id and no -s, codex-ask adopts the cc-transcript-resolved
    # scratchpad into a per-call subdir; any miss falls back to mktemp.
    script = templates_dir.parents[3] / "codex" / "bin" / "codex-ask"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    codex = stub_bin / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        '[ -n "$out" ] && echo ok > "$out"\n'
        "exit 0\n"
    )
    codex.chmod(0o755)
    fake_sp = tmp_path / "scratchpad"
    fake_sp.mkdir()
    sentinel = tmp_path / "cct-invoked"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    cwd = tmp_path / "repo"
    cwd.mkdir()

    def write_cct(body):
        cct = stub_bin / "cc-transcript"
        cct.write_text(body)
        cct.chmod(0o755)

    def ask(session):
        env = {**os.environ, "PATH": f"{stub_bin}:/usr/bin:/bin", "TMPDIR": str(tmpdir)}
        if session is None:
            env.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([str(script), "ping"], cwd=cwd, env=env, text=True, capture_output=True, timeout=30)

    def reply_of(run):
        assert run.returncode == 0, run.stdout + run.stderr
        return re.search(r"^REPLY_FILE: (.+)$", run.stdout, re.M).group(1)

    # happy path: cc-transcript prints the existing scratchpad -> per-call subdir
    write_cct(f'#!/bin/sh\ntouch "{sentinel}"\necho "{fake_sp}"\nexit 0\n')
    reply = reply_of(ask("eac1daa0-dead-beef-0000-000000000000"))
    assert reply.startswith(str(fake_sp) + os.sep), reply
    assert os.path.dirname(os.path.dirname(reply)) == str(fake_sp), reply
    assert sentinel.exists()

    # cc-transcript exits nonzero -> mktemp fallback under TMPDIR
    write_cct("#!/bin/sh\nexit 1\n")
    assert reply_of(ask("sid-nonzero")).startswith(str(tmpdir) + os.sep)

    # cc-transcript prints nothing -> fallback
    write_cct("#!/bin/sh\nexit 0\n")
    assert reply_of(ask("sid-empty")).startswith(str(tmpdir) + os.sep)

    # cc-transcript prints a non-existent path -> fallback
    write_cct(f'#!/bin/sh\necho "{tmp_path}/nope"\nexit 0\n')
    assert reply_of(ask("sid-missing")).startswith(str(tmpdir) + os.sep)

    # no session id -> the probe never runs (cc-transcript is never invoked)
    sentinel.unlink()
    write_cct(f'#!/bin/sh\ntouch "{sentinel}"\necho "{fake_sp}"\nexit 0\n')
    assert reply_of(ask(None)).startswith(str(tmpdir) + os.sep)
    assert not sentinel.exists()


def test_codex_ask_prints_headers_before_codex_produces_output(templates_dir, tmp_path):
    # Print-first is real: the stub blocks on a release file, so the 3 headers
    # must arrive (via a plain buffered reader) while codex has written nothing.
    script = templates_dir.parents[3] / "codex" / "bin" / "codex-ask"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    release = tmp_path / "release"
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        f'while [ ! -f "{release}" ]; do sleep 0.05; done\n'
        '[ -n "$out" ] && printf "RELEASED REPLY\\n" > "$out"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = _codex_env(stub_bin, tmpdir)

    proc = subprocess.Popen(
        [str(script), "ping"], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        header = _read_lines_buffered(proc, 3, 5)
        assert sum(l.startswith(("REPLY_FILE:", "LOG_FILE:", "AWAIT:")) for l in header) == 3, header
        reply = next(l.split(": ", 1)[1] for l in header if l.startswith("REPLY_FILE:"))
        # codex is provably still blocked: nothing released, reply file still empty
        assert not release.exists()
        assert Path(reply).read_text() == "", "codex produced output before the headers were read"
        release.write_text("go")
        assert proc.wait(timeout=15) == 0
        assert Path(reply).read_text().strip() == "RELEASED REPLY"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_codex_ask_concurrent_scratch_reuse_never_crashes(templates_dir, tmp_path):
    # Concurrent -s reuse is last-writer-wins, but a waiter whose status file is
    # rm'd mid-poll must fail cleanly, never `[: : integer expected`.
    script = templates_dir.parents[3] / "codex" / "bin" / "codex-ask"
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        '[ -n "$out" ] && echo ok > "$out"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = _codex_env(stub_bin, tmpdir)

    # (1) deterministic: an empty status file + a dead pid must clean-error (exit
    # 1), never crash the integer test in report_status.
    sdir = tmp_path / "empty-status"
    sdir.mkdir()
    (sdir / "meta").write_text(f"{sdir / 'r'}\n{sdir / 'l'}\n")
    (sdir / "l").write_text("log tail\n")
    (sdir / "pid").write_text("2147483646\n")  # a surely-dead pid
    (sdir / "status").write_text("")  # empty (mid-rewrite race)
    det = subprocess.run(
        [str(script), "--await", str(sdir)], cwd=cwd, env=env, text=True, capture_output=True, timeout=15
    )
    assert det.returncode == 1, det.stdout + det.stderr
    assert "integer expected" not in (det.stdout + det.stderr)

    # (2) racy smoke: complete a run, then race an --await against a second run
    # reusing the same -s dir (whose pre-launch rm can delete the status).
    shared = tmp_path / "shared"
    first = subprocess.run(
        [str(script), "-s", str(shared), "ping"], cwd=cwd, env=env, text=True, capture_output=True, timeout=30
    )
    assert first.returncode == 0, first.stdout + first.stderr
    for _ in range(6):
        waiter = subprocess.Popen(
            [str(script), "--await", str(shared)], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        subprocess.run([str(script), "-s", str(shared), "ping"], cwd=cwd, env=env, capture_output=True, timeout=30)
        try:
            _, werr = waiter.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            waiter.kill()
            _, werr = waiter.communicate()
        assert "integer expected" not in werr, werr
        assert waiter.returncode in (0, 1), (waiter.returncode, werr)


# --- codex-ask disk-truth protocol: --collect, staged reply, poll recovery ---

DEAD_PID = "2147483646"  # far above macOS PID_MAX: never a live process
STARTED = '{"type":"turn.started"}\n'
COMPLETED = STARTED + '{"type":"turn.completed","usage":{}}\n'


def _codex_ask(templates_dir):
    return templates_dir.parents[3] / "codex" / "bin" / "codex-ask"


def _craft_lane(d, *, reply=None, status=None, pid=None, log=None, question="ask\n", meta=True):
    # Hand-build one lane's on-disk state (meta/status/pid/reply/log), mirroring
    # what a real codex-ask run leaves behind, so --collect can be tested offline.
    d.mkdir(parents=True, exist_ok=True)
    r, q, lg = d / "codex-r-x", d / "codex-q-x", d / "codex-q-x.log"
    if meta:
        (d / "meta").write_text(f"{r}\n{lg}\n")
    q.write_text(question)
    if reply is not None:
        r.write_text(reply)
    if log is not None:
        lg.write_text(log)
    if status is not None:
        (d / "status").write_text(status)
    if pid is not None:
        (d / "pid").write_text(f"{pid}\n")
    return d


def _collect_lanes(script, target, *, timeout=30):
    run = subprocess.run(
        [str(script), "--collect", str(target)], text=True, capture_output=True, timeout=timeout
    )
    lanes = {}
    for line in run.stdout.splitlines():
        line = line.strip()
        if line:
            rec = json.loads(line)  # raises on invalid JSON -> a real escaper bug fails loudly
            lanes[rec["lane"]] = rec
    return run, lanes


def test_codex_ask_collect_classifies_lane_states(templates_dir, tmp_path):
    # One hand-crafted lane per reachable state; --collect classifies each from
    # disk alone and emits exactly one JSONL record per lane.
    script = _codex_ask(templates_dir)
    root = tmp_path / "root"
    root.mkdir()
    (root / "norun").mkdir()  # empty dir, no meta
    d = root / "qronly"  # q/r files but no meta -> still no-run
    d.mkdir()
    (d / "codex-q-x").write_text("q\n")
    (d / "codex-r-x").write_text("stale\n")
    _craft_lane(root / "pending")  # meta, no pid, no status
    _craft_lane(root / "died", reply="", pid=DEAD_PID)  # dead pid + empty reply
    _craft_lane(root / "completed", reply="the answer\n", status="0", question="What is 2+2?\n")
    _craft_lane(root / "failed", status="7")  # non-zero exit
    _craft_lane(root / "emptyreply", reply="", status="0")  # status 0 + empty reply -> silent death
    _craft_lane(root / "diedmarker", reply="partial\n", status="0", log=STARTED)  # turn began, never completed
    _craft_lane(root / "completedmarker", reply="done\n", status="0", log=COMPLETED)

    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        _craft_lane(root / "running", pid=sleeper.pid)  # meta + live pid + no status
        run, lanes = _collect_lanes(script, root)
    finally:
        sleeper.terminate()
        sleeper.wait()

    assert run.returncode == 0, run.stderr
    expected = {
        "norun": "no-run", "qronly": "no-run", "pending": "pending", "running": "running",
        "died": "died", "completed": "completed", "failed": "failed", "emptyreply": "failed",
        "diedmarker": "died", "completedmarker": "completed",
    }
    assert {k: lanes[k]["state"] for k in expected} == expected
    assert lanes["completed"]["reply_size"] == len("the answer\n")
    assert lanes["completed"]["reply_file"].endswith("codex-r-x")
    assert lanes["completed"]["question"] == "What is 2+2?"
    for name in ("pending", "running", "died"):
        assert "--await" in lanes[name].get("await", ""), name
    for name in ("completed", "failed", "norun"):
        assert "await" not in lanes[name], name  # nothing to await for a lane that never ran


def test_codex_ask_poll_recovery_synthesizes_status(templates_dir, tmp_path):
    # A run killed after staging its reply but before the status write: --await
    # reads the staged (complete) reply, synthesizes a durable status=0, recovers.
    script = _codex_ask(templates_dir)
    d = tmp_path / "lane"
    _craft_lane(d, reply="RECOVERED ANSWER\n", pid=DEAD_PID, log="tail\n")
    run = subprocess.run([str(script), "--await", str(d)], text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert (d / "status").read_text().strip() == "0"
    r = re.search(r"^REPLY_FILE: (.+)$", run.stdout, re.M).group(1)
    assert Path(r).read_text().strip() == "RECOVERED ANSWER"
    again = subprocess.run([str(script), "--await", str(d)], text=True, capture_output=True, timeout=30)
    assert again.returncode == 0, again.stdout + again.stderr  # idempotent


def test_codex_ask_poll_recovery_inverse(templates_dir, tmp_path):
    # Same shape, EMPTY staged reply -> a genuine death: rc 1, no durable status.
    script = _codex_ask(templates_dir)
    d = tmp_path / "lane"
    _craft_lane(d, reply="", pid=DEAD_PID, log="tail\n")
    run = subprocess.run([str(script), "--await", str(d)], text=True, capture_output=True, timeout=30)
    assert run.returncode == 1, run.stdout + run.stderr
    assert not (d / "status").exists()


def test_codex_ask_reply_published_only_on_zero_exit(templates_dir, tmp_path):
    # rc gate on the reply mv: a nonzero-exit codex that still wrote its -o file
    # must not publish that reply, so a failed run is never mistaken for complete.
    script = _codex_ask(templates_dir)
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        'echo "boom tail"\n'
        '[ -n "$out" ] && printf "PARTIAL AND FAILED\\n" > "$out"\n'
        "exit 5\n"
    )
    stub.chmod(0o755)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = _codex_env(stub_bin, tmpdir)
    lane = tmp_path / "lane"
    run = subprocess.run(
        [str(script), "-s", str(lane), "ping"], env=env, text=True, capture_output=True, timeout=30
    )
    assert run.returncode == 5, run.stdout + run.stderr
    reply = re.search(r"^REPLY_FILE: (.+)$", run.stdout, re.M).group(1)
    assert Path(reply).read_text() == ""  # partial output not published on nonzero exit
    _, lanes = _collect_lanes(script, lane)
    assert lanes["."]["state"] == "failed"


def test_codex_ask_reuse_wipes_meta(templates_dir, tmp_path):
    # Wiping meta on reuse closes the wrong-attribution state: a stale reply with
    # no meta is never 'completed' — it reads no-run, and --await refuses.
    script = _codex_ask(templates_dir)
    root = tmp_path / "root"
    d = root / "stale"
    d.mkdir(parents=True)
    (d / "codex-r-old").write_text("STALE PREVIOUS ANSWER\n")  # reply, but no meta
    run, lanes = _collect_lanes(script, root)
    assert lanes["stale"]["state"] == "no-run"
    assert "STALE PREVIOUS ANSWER" not in run.stdout
    aw = subprocess.run([str(script), "--await", str(d)], text=True, capture_output=True, timeout=15)
    assert aw.returncode == 2
    assert "no recorded" in aw.stderr.lower()
    assert 'rm -f "$S/status" "$S/pid" "$S/meta"' in script.read_text()


def test_codex_ask_reply_staging_is_atomic(templates_dir, tmp_path):
    # A blocked run reads 'running' with an empty reply (staged write not yet
    # moved); --collect never blocks on it; release -> 'completed', reply present.
    script = _codex_ask(templates_dir)
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    release = tmp_path / "release"
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        f'while [ ! -f "{release}" ]; do sleep 0.05; done\n'
        '[ -n "$out" ] && printf "STAGED REPLY\\n" > "$out"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = _codex_env(stub_bin, tmpdir)
    root = tmp_path / "root"
    root.mkdir()
    lane = root / "a"

    proc = subprocess.Popen(
        [str(script), "-s", str(lane), "ping"], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        pid_file = lane / "pid"
        end = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < end:
            time.sleep(0.02)
        assert pid_file.exists(), "run never registered a pid"
        t0 = time.monotonic()
        run, lanes = _collect_lanes(script, root)
        assert time.monotonic() - t0 < 5, "collect blocked on a running lane"
        assert lanes["a"]["state"] == "running", lanes["a"]
        assert lanes["a"]["reply_size"] == 0
        rf = lanes["a"]["reply_file"]
        assert Path(rf).read_text() == "", "final reply populated before completion"
        assert not release.exists()
        release.write_text("go")
        assert proc.wait(timeout=15) == 0
        _, lanes2 = _collect_lanes(script, root)
        assert lanes2["a"]["state"] == "completed"
        assert Path(rf).read_text().strip() == "STAGED REPLY"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_codex_ask_collect_mixed_root_snapshot(templates_dir, tmp_path):
    # A snapshot over a mixed root returns promptly and never blocks, even with a
    # live (running) lane present alongside terminal ones.
    script = _codex_ask(templates_dir)
    root = tmp_path / "root"
    root.mkdir()
    _craft_lane(root / "done", reply="x\n", status="0", log=COMPLETED)
    _craft_lane(root / "gone", reply="", pid=DEAD_PID)
    _craft_lane(root / "waiting")
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        _craft_lane(root / "live", pid=sleeper.pid)
        t0 = time.monotonic()
        run, lanes = _collect_lanes(script, root, timeout=10)
        dt = time.monotonic() - t0
    finally:
        sleeper.terminate()
        sleeper.wait()
    assert run.returncode == 0
    assert dt < 5, dt
    assert lanes["done"]["state"] == "completed"
    assert lanes["gone"]["state"] == "died"
    assert lanes["waiting"]["state"] == "pending"
    assert lanes["live"]["state"] == "running"


def test_codex_ask_collect_never_inlines_reply_and_escapes(templates_dir, tmp_path):
    # Large replies contribute size, never content; a hostile question first line
    # (quotes, backslashes, tab, control chars, trailing lines) yields valid JSONL.
    script = _codex_ask(templates_dir)
    root = tmp_path / "root"
    root.mkdir()
    sentinel = "REPLY_BODY_SENTINEL_" + "z" * 4000
    _craft_lane(root / "big", reply=sentinel + "\n", status="0", log=COMPLETED)
    hostile_q = 'say "hi" a\\b\tc\x01\x02 end\nSECOND LINE MUST BE IGNORED\n'
    _craft_lane(root / "hostile", reply="ok\n", status="0", log=COMPLETED, question=hostile_q)
    # a lane dir whose name carries a newline must still emit one valid JSONL line
    _craft_lane(root / "lane\nbreak", reply="ok\n", status="0", log=COMPLETED)
    run, lanes = _collect_lanes(script, root)  # json.loads inside proves validity
    assert sentinel not in run.stdout  # reply body never inlined
    assert lanes["big"]["reply_size"] == len(sentinel) + 1
    q = lanes["hostile"]["question"]
    assert "SECOND LINE" not in q  # only the first line survives
    assert q.startswith('say "hi" a\\b')  # quotes/backslash round-trip through JSON
    assert "\x01" not in q and "\x02" not in q  # C0 controls stripped
    assert "lane\nbreak" in lanes  # newline in the lane name survives escaping
    assert len(run.stdout.strip().splitlines()) == len(lanes)  # no record split across lines


def test_codex_ask_collect_single_lane_dir(templates_dir, tmp_path):
    # Pointed straight at a lane dir (meta present), --collect emits one '.' record.
    script = _codex_ask(templates_dir)
    d = tmp_path / "lane"
    _craft_lane(d, reply="one answer\n", status="0", log=COMPLETED, question="the q\n")
    run, lanes = _collect_lanes(script, d)
    assert run.returncode == 0
    assert list(lanes) == ["."]
    assert lanes["."]["state"] == "completed"
    assert lanes["."]["question"] == "the q"


def test_codex_ask_collect_is_readonly(templates_dir, tmp_path):
    # --collect never writes: the lane tree is byte-identical before and after.
    script = _codex_ask(templates_dir)
    root = tmp_path / "root"
    root.mkdir()
    _craft_lane(root / "a", reply="x\n", status="0", log=COMPLETED)
    _craft_lane(root / "b", reply="", pid=DEAD_PID)

    def snap():
        return {
            str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()
        }

    before = snap()
    run, _ = _collect_lanes(script, root)
    assert run.returncode == 0
    assert snap() == before


def test_codex_ask_schema_passthrough(templates_dir, tmp_path):
    # --schema FILE -> codex --output-schema (opt-in, verdict lanes); an
    # unreadable schema fails cleanly before codex is ever launched.
    script = _codex_ask(templates_dir)
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "codex"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done\n'
        "cat > /dev/null\n"
        'echo "STUB_ARGS: $*"\n'
        '[ -n "$out" ] && echo done > "$out"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = _codex_env(stub_bin, tmpdir)
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}\n')

    ok = subprocess.run(
        [str(script), "-s", str(tmp_path / "s1"), "--schema", str(schema), "ping"],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    log = Path(re.search(r"^LOG_FILE: (.+)$", ok.stdout, re.M).group(1)).read_text()
    assert f"--output-schema {schema}" in log

    bad = subprocess.run(
        [str(script), "-s", str(tmp_path / "s2"), "--schema", str(tmp_path / "nope.json"), "ping"],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert bad.returncode == 2
    assert "readable json schema" in bad.stderr.lower()


def test_claude_md_disk_truth_fragment(templates_dir):
    # The fleet fragment carries the disk-truth doctrine + the collect/schema
    # surface; base-conventions carries the delegated-results verify clause.
    claude = (templates_dir.parents[4] / "plugin" / "guides" / "md" / "claude-rules.md").read_text()
    assert "the disk is the record" in claude
    assert "codex-ask --collect" in claude
    assert "codex-ask --schema" in claude
    conventions = (templates_dir.parent / "reference" / "base-conventions.md").read_text()
    assert "codex-ask --collect" in conventions


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
    frag = ".claude/fragments/great-docs.yml/great-docs-repo.fragment.yml"
    assert "pypi: true" in _real_plan("python", py_var_pairs)[0][frag]
    assert "pypi: false" in _real_plan("python", py_var_pairs, features=["docs"])[0][frag]


def test_docs_layout_dirs_import_fleet_pack(py_var_pairs):
    # great-docs.yml + docs.yml now compose from cc-guides layout dirs: a repo-local
    # *.fragment.yml plus fleet-shared cc-skills: imports (`cc-guides render` joins them).
    plan, _ = _real_plan("python", py_var_pairs)
    gd_layout = plan[".claude/fragments/great-docs.yml/layout.toml"]
    assert '"great-docs-repo"' in gd_layout
    assert '"cc-skills:great-docs-fleet"' in gd_layout
    assert '"cc-skills:great-docs-prerender"' in gd_layout
    assert 'source = "github:yasyf/cc-skills@main"' in gd_layout
    docs_layout = plan[".claude/fragments/.github/workflows/docs.yml/layout.toml"]
    for imp in ('"docs-build-preamble"', '"cc-skills:docs-build-head"', '"cc-skills:docs-build-sync"',
                '"cc-skills:docs-build-tail"', '"cc-skills:docs-publish"'):
        assert imp in docs_layout
    # the workflow preamble's PR paths filter is substituted to the package dir
    preamble = plan[".claude/fragments/.github/workflows/docs.yml/docs-build-preamble.fragment.yml"]
    assert '"demo_proj/**"' in preamble
    assert "{{PACKAGE}}" not in preamble


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


def test_capt_hook_layout_imports_guard_packs(base_var_pairs, py_var_pairs, go_var_pairs, swift_var_pairs):
    # Every layer's capt-hook.toml layout imports the ccx and cc-present guard-pack fragments
    # (which carry the repo-scoped @latest pins), alongside the base fixes/general/steering
    # fragment, so every contributor gets the guards whether or not the plugins are attached.
    for layer, pairs in (
        ("base", base_var_pairs),
        ("python", py_var_pairs),
        ("go", go_var_pairs),
        ("swift", swift_var_pairs),
    ):
        layout = tomllib.loads(
            _real_plan(layer, pairs)[0][".claude/fragments/.claude/capt-hook.toml/layout.toml"]
        )
        assert "cc-skills:capt-hook-base" in layout["fragments"], f"{layer} base fragment"
        assert "cc-skills:capt-hook-ccx" in layout["fragments"], f"{layer} ccx guard fragment"
        assert "cc-skills:capt-hook-cc-present" in layout["fragments"], f"{layer} cc-present guard fragment"
        assert layout["sources"]["cc-skills"]["source"] == "github:yasyf/cc-skills@main", f"{layer} source pin"


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


def test_mcp_json_composes_from_pack_fragments(base_var_pairs):
    plan, _ = _real_plan("base", base_var_pairs)
    layout = tomllib.loads(plan[".claude/fragments/.mcp.json/layout.toml"])
    assert layout["fragments"] == ["cc-skills:mcp-base", "mcp-overrides"]
    assert layout["sources"]["cc-skills"]["source"] == "github:yasyf/cc-skills@main"
    assert json.loads(plan[".claude/fragments/.mcp.json/mcp-overrides.fragment.json"]) == {}
    assert ".mcp.json" not in plan


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


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "has space", "trailing/", ".", "a/./b", "plugin/hooks\n"])
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


def test_secondary_code_root_collision_rejected(go_var_pairs):
    r = scaffold.resolve("go", [], [], _secondary(go_var_pairs, ".claude/hooks"), DATE, "python")
    with pytest.raises(ScaffoldError):
        scaffold.select_files(r)


def test_secondary_code_root_case_folded_collision_rejected(go_var_pairs):
    r = scaffold.resolve("go", [], [], _secondary(go_var_pairs, ".CLAUDE/hooks"), DATE, "python")
    with pytest.raises(ScaffoldError):
        scaffold.select_files(r)


def test_plan_rejects_symlink_escape(tmp_path, cc_guides_stub, go_var_pairs):
    target = tmp_path / "target"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "linked").symlink_to(outside)
    args = _run_args(target, layer="go", secondary_layer="python", var_pairs=_secondary(go_var_pairs, "linked"))
    with pytest.raises(ScaffoldError):
        scaffold.run(args)
    assert list(outside.iterdir()) == []
    assert not (target / "LICENSE").exists()


def test_plan_rejects_destination_nested_in_planned_file(tmp_path, cc_guides_stub, go_var_pairs):
    args = _run_args(tmp_path, layer="go", secondary_layer="python", var_pairs=_secondary(go_var_pairs, "README.md"))
    with pytest.raises(ScaffoldError):
        scaffold.run(args)
    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "LICENSE").exists()


def test_plan_rejects_existing_file_ancestor(tmp_path, cc_guides_stub, go_var_pairs):
    (tmp_path / "plugin").write_text("a file, not a directory\n")
    args = _run_args(tmp_path, layer="go", secondary_layer="python", var_pairs=_secondary(go_var_pairs))
    with pytest.raises(ScaffoldError):
        scaffold.run(args)
    assert (tmp_path / "plugin").read_text() == "a file, not a directory\n"
    assert not (tmp_path / "LICENSE").exists()


@pytest.mark.parametrize("layer", ["base", "python", "go", "swift", "swift-app"])
def test_every_layout_references_hook_style_fragment(layer):
    layout = (scaffold.TEMPLATES / layer / "claude/fragments/AGENTS.md/layout.toml").read_text()
    assert '"{{PROJECT_NAME}}-hook-style"' in layout
