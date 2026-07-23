# /// script
# requires-python = ">=3.13"
# ///
"""Auto-bump a plugin's patch version when its content drifts from the last
version change. The installed-plugin cache only re-pulls on a version move, so
content shipped without a bump goes stale on every consumer. Guarded plugins
(binary-pinned) get a drift warning instead of a bump.

Env contract:
  AUTOBUMP_DRY_RUN   "true"/"false" — plan only, never write/commit/push
  AUTOBUMP_GUARD     space-separated plugin `name` values to warn (not bump)
  AUTOBUMP_EXCLUDES  newline-separated repo-relative pathspecs to ignore
  AUTOBUMP_STRICT    "true"/"false" — guarded drift becomes an error + exit 1
  AUTOBUMP_FORCE     "1" to allow mutation outside CI (deliberate local use)
  GITHUB_ACTIONS     "true" in CI; mutation needs this or AUTOBUMP_FORCE
  GITHUB_STEP_SUMMARY  optional path to append the summary table
  GITHUB_REF_NAME    push branch (default "main")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
ROOT_EXCLUDES = (".claude-plugin", ".github", ".claude", ".gitignore")


def _git(repo: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=text, encoding="utf-8" if text else None
    )


def _git_out(repo: Path, *args: str) -> str:
    proc = _git(repo, *args)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def discover_manifests(repo: Path) -> list[str]:
    proc = _git(repo, "ls-files", "-z", "--", "*/.claude-plugin/plugin.json", ".claude-plugin/plugin.json", text=False)
    return [p.decode() for p in proc.stdout.split(b"\0") if p]


def plugin_root(manifest: str) -> str:
    parent = str(Path(manifest).parent.parent)
    return "." if parent in ("", ".") else parent


def manifest_raw_at(repo: Path, ref: str, manifest: str) -> str | None:
    proc = _git(repo, "show", f"{ref}:{manifest}")
    return proc.stdout if proc.returncode == 0 else None


def manifest_json_at(repo: Path, ref: str, manifest: str) -> dict | None:
    raw = manifest_raw_at(repo, ref, manifest)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def version_at(repo: Path, ref: str, manifest: str) -> str | None:
    data = manifest_json_at(repo, ref, manifest)
    return data.get("version") if data else None


def first_parent(repo: Path, sha: str) -> str | None:
    parts = _git_out(repo, "rev-list", "--parents", "-n", "1", sha).split()
    return parts[1] if len(parts) > 1 else None


def find_baseline(repo: Path, manifest: str) -> str:
    """Newest first-parent commit whose top-level version value differs from its
    first parent's (file-absent counts as different). No version change ever ⇒
    the manifest's creation commit."""
    shas = _git_out(repo, "log", "--first-parent", "--format=%H", "HEAD", "--", manifest).split()
    for sha in shas:
        parent = first_parent(repo, sha)
        v_parent = version_at(repo, parent, manifest) if parent else None
        if version_at(repo, sha, manifest) != v_parent:
            return sha
    return shas[-1]


def _strip_version(data: dict) -> dict:
    out = dict(data)
    out.pop("version", None)
    return out


def tree_drift(repo: Path, baseline: str, root: str, excludes: list[str], nested_roots: list[str]) -> bool:
    prefix = "" if root == "." else f"{root}/"
    pathspecs = ["." if root == "." else root]
    pathspecs += [f":(exclude){prefix}{name}" for name in ROOT_EXCLUDES]
    pathspecs += [f":(exclude){e}" for e in excludes]
    pathspecs += [f":(exclude){nested}" for nested in nested_roots]  # a nested plugin's drift is its own
    rc = _git(repo, "diff", "--quiet", baseline, "HEAD", "--", *pathspecs).returncode
    if rc not in (0, 1):
        raise RuntimeError(f"git diff --quiet exited {rc} for {root}")
    return rc == 1


def structural_drift(repo: Path, baseline: str, manifest: str, head: dict) -> bool:
    base = manifest_json_at(repo, baseline, manifest)
    return base is None or _strip_version(base) != _strip_version(head)


def bump_patch(version: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        raise ValueError(f"non-semver version {version!r}")
    major, minor, patch = m.groups()
    return f"{major}.{minor}.{int(patch) + 1}"


def bump_manifest_text(raw: str, old: str, new: str) -> str:
    """Surgical: rewrite only the first (top-level) `"version"` key on the raw
    text — json.dumps would reflow inline objects, and a nested dependency
    `"version"` must stay untouched."""
    pattern = r'("version"\s*:\s*")' + re.escape(old) + r'(")'
    out, n = re.subn(pattern, lambda m: m.group(1) + new + m.group(2), raw, count=1)
    assert n == 1, f"expected one version match for {old!r}, got {n}"
    parsed = json.loads(out)
    assert parsed["version"] == new, "top-level version unchanged after edit (wrong key matched?)"
    assert _strip_version(json.loads(raw)) == _strip_version(parsed), "edit altered fields beyond the top-level version"
    return out


@dataclass
class Result:
    name: str
    root: str
    manifest: str
    version: str | None
    baseline: str | None
    verdict: str  # clean | exempt | bump | guarded | error
    new_version: str | None = None
    message: str | None = None
    new_text: str | None = None


def _nested_roots(root: str, roots: list[str]) -> list[str]:
    """Other plugin roots that lie inside `root` — their drift is theirs, not ours."""
    return [r for r in roots if r != root and (root == "." or r.startswith(f"{root}/"))]


def analyze(repo: Path, guard: set[str], excludes: list[str]) -> list[Result]:
    manifests = discover_manifests(repo)
    roots = [plugin_root(m) for m in manifests]
    results = []
    for manifest, root in zip(manifests, roots, strict=True):
        head_raw = manifest_raw_at(repo, "HEAD", manifest)
        if head_raw is None:
            results.append(Result(root, root, manifest, None, None, "exempt"))
            continue
        try:
            head = json.loads(head_raw)
        except json.JSONDecodeError:
            results.append(Result(root, root, manifest, None, None, "error", message="plugin.json at HEAD is not valid JSON"))
            continue
        name = head.get("name") or root
        version = head.get("version")
        if version is None:
            results.append(Result(name, root, manifest, None, None, "exempt"))
            continue
        baseline = find_baseline(repo, manifest)
        drifted = tree_drift(repo, baseline, root, excludes, _nested_roots(root, roots)) or structural_drift(
            repo, baseline, manifest, head
        )
        if not drifted:
            results.append(Result(name, root, manifest, version, baseline, "clean"))
        elif name in guard:
            results.append(Result(name, root, manifest, version, baseline, "guarded"))
        else:
            try:
                new = bump_patch(version)
                new_text = bump_manifest_text(head_raw, version, new)
            except (ValueError, AssertionError, json.JSONDecodeError) as exc:
                results.append(Result(name, root, manifest, version, baseline, "error", message=str(exc) or type(exc).__name__))
            else:
                results.append(Result(name, root, manifest, version, baseline, "bump", new_version=new, new_text=new_text))
    return results


def _verdict_cell(r: Result) -> str:
    return {
        "clean": "clean",
        "exempt": "exempt (no version)",
        "guarded": "guarded drift",
        "bump": f"bump → {r.new_version}",
        "error": f"ERROR: {r.message}",
    }[r.verdict]


def render_summary(results: list[Result]) -> str:
    lines = ["| Plugin | Version | Baseline | Verdict |", "| --- | --- | --- | --- |"]
    for r in results:
        baseline = r.baseline[:7] if r.baseline else "—"
        lines.append(f"| {r.name} | {r.version or '—'} | {baseline} | {_verdict_cell(r)} |")
    return "\n".join(lines)


def commit_and_push(repo: Path, bumps: list[Result], branch: str) -> bool:
    for r in bumps:
        (repo / r.manifest).write_text(r.new_text, encoding="utf-8")  # precomputed + validated in analyze
    _git_out(repo, "add", *(r.manifest for r in bumps))
    if len(bumps) == 1:
        r = bumps[0]
        message = [f"chore(plugins): autobump {r.name} {r.version}→{r.new_version} [skip ci]"]
    else:
        body = "\n".join(f"{r.name} {r.version}→{r.new_version}" for r in bumps)
        message = [f"chore(plugins): autobump {len(bumps)} plugins [skip ci]", body]
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": BOT_NAME,
        "GIT_AUTHOR_EMAIL": BOT_EMAIL,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
    }
    args = ["commit"]
    for m in message:
        args += ["-m", m]
    subprocess.run(["git", *args], cwd=repo, env=env, check=True)
    for _ in range(3):
        # env carries the bot identity: rebase creates commits too, and a bare
        # runner has none — without it every retry dies on "empty ident name".
        pull = subprocess.run(["git", "pull", "--rebase", "origin", branch], cwd=repo, env=env)
        if pull.returncode == 0:
            push = subprocess.run(["git", "push", "origin", f"HEAD:{branch}"], cwd=repo, env=env)
            if push.returncode == 0:
                return True
    return False


def main() -> int:
    repo = Path.cwd()
    dry_run = os.environ.get("AUTOBUMP_DRY_RUN", "false").strip().lower() == "true"
    strict = os.environ.get("AUTOBUMP_STRICT", "false").strip().lower() == "true"
    guard = set(os.environ.get("AUTOBUMP_GUARD", "").split())
    excludes = [line.strip() for line in os.environ.get("AUTOBUMP_EXCLUDES", "").splitlines() if line.strip()]
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    in_ci = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
    forced = os.environ.get("AUTOBUMP_FORCE", "").strip() == "1"

    results = analyze(repo, guard, excludes)

    for r in results:
        if r.verdict == "guarded":
            print(f"::warning::plugin {r.name} drifted but is guarded (binary-pinned); not bumping")
            if strict:
                print(f"::error::guarded plugin {r.name} drifted (strict)")
        elif r.verdict == "error":
            print(f"::error::plugin {r.name}: {r.message}")

    # A stray non-dry local run must never mutate a shared branch: mutation needs
    # CI, or a deliberate AUTOBUMP_FORCE=1. Otherwise a non-dry run reports only.
    report_only = not dry_run and not (in_ci or forced)
    bumps = [r for r in results if r.verdict == "bump"]
    push_failed = False
    if bumps and not dry_run and not report_only:
        push_failed = not commit_and_push(repo, bumps, branch)
        if push_failed:
            print("::error::autobump commit push failed after 3 retries")

    note = "local run: report-only (set AUTOBUMP_FORCE=1 to mutate)" if report_only else None
    summary = "\n".join(([note] if note else []) + [render_summary(results)])
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")

    guarded_fail = strict and any(r.verdict == "guarded" for r in results)
    nonsemver = any(r.verdict == "error" for r in results)
    return 1 if (guarded_fail or nonsemver or push_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
