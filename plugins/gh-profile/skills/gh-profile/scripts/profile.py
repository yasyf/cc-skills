#!/usr/bin/env python3
"""gh-profile CLI — one entry point for the whole skill.

    profile.py preflight                                  gh/auth/scope/profile-repo checks, KEY=VALUE output
    profile.py harvest  [--login X] [--out F]             build the dossier (delegates to the committed updater)
    profile.py render   --target DIR [--with metrics,claude] [--force]

STDLIB ONLY. preflight runs before anything else exists, so neither this file
nor the committed updater it delegates to may import third-party modules.

``harvest`` puts templates/github/scripts on sys.path and calls the SAME
update_profile.py that ``render`` later commits into the profile repo — the
skill's first render and every cron refresh share one code path.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates" / "github"

WITH_FLAGS = ("metrics", "claude")

# {{UPPER_SNAKE}} placeholders only — GitHub's ${{ github.* }} syntax is
# lowercase with inner spaces, so it can never match.
LEFTOVER = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess, capturing text output. Never raises on nonzero."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# --- preflight ---


def _parse_scopes(status_output: str) -> list[str]:
    """Pull scope names out of ``gh auth status``. Empty for fine-grained
    tokens, whose scopes gh cannot report."""
    for line in status_output.splitlines():
        if "Token scopes:" in line:
            _, _, rest = line.partition("Token scopes:")
            return [s for s in (part.strip().strip("'\"") for part in rest.split(",")) if s]
    return []


def cmd_preflight() -> int:
    missing: list[str] = []

    if not shutil.which("gh"):
        print("GH_VERSION=missing")
        print("MISSING: gh — install it: https://cli.github.com", file=sys.stderr)
        return 1
    code, out, _ = _run(["gh", "--version"])
    words = out.split()
    print(f"GH_VERSION={words[2] if code == 0 and len(words) > 2 else 'unknown'}")

    code, auth_out, auth_err = _run(["gh", "auth", "status"])
    auth_ok = code == 0
    print(f"AUTH={'ok' if auth_ok else 'missing'}")
    if not auth_ok:
        missing.append("MISSING: AUTH — run: gh auth login")

    login = ""
    if auth_ok:
        code, out, _ = _run(["gh", "api", "user", "-q", ".login"])
        login = out.strip() if code == 0 else ""
        if not login:
            missing.append("MISSING: LOGIN — `gh api user` failed; re-run gh auth login")
    print(f"LOGIN={login}")

    scopes = _parse_scopes(auth_out + auth_err)
    print(f"SCOPES={','.join(scopes)}")
    if not auth_ok or not scopes:
        # Fine-grained tokens: gh cannot report scopes, so we cannot verify.
        workflow_scope = "UNKNOWN"
    elif "workflow" in scopes:
        workflow_scope = "ok"
    else:
        workflow_scope = "MISSING"
    print(f"SCOPE_WORKFLOW={workflow_scope}")
    if workflow_scope == "MISSING":
        missing.append("MISSING: workflow scope — run: gh auth refresh -h github.com -s repo,workflow")
    elif auth_ok and workflow_scope == "UNKNOWN":
        print("NOTE: token scopes not reported (fine-grained token?) — pushing workflow files may fail", file=sys.stderr)

    repo_exists = False
    visibility = default_branch = has_markers = "n/a"
    if login:
        code, out, _ = _run(["gh", "api", f"repos/{login}/{login}"])
        if code == 0:
            repo_exists = True
            repo = json.loads(out)
            visibility = repo.get("visibility") or ("private" if repo.get("private") else "public")
            default_branch = repo.get("default_branch") or "n/a"
            code, out, _ = _run(["gh", "api", f"repos/{login}/{login}/readme", "-q", ".content"])
            if code == 0:
                readme = base64.b64decode(out).decode("utf-8", errors="replace")
                has_markers = "true" if "<!-- gh-profile:start:" in readme else "false"
    print(f"PROFILE_REPO={'exists' if repo_exists else 'absent'}")
    print(f"VISIBILITY={visibility}")
    print(f"DEFAULT_BRANCH={default_branch}")
    print(f"HAS_MARKERS={has_markers}")

    rate = "n/a"
    if auth_ok:
        code, out, _ = _run(["gh", "api", "rate_limit", "-q", ".resources.core.remaining"])
        if code == 0:
            rate = out.strip()
    print(f"RATE_REMAINING={rate}")

    for line in missing:
        print(line, file=sys.stderr)
    return 1 if missing else 0


# --- harvest (delegates to the committed updater) ---


def _load_updater():
    scripts = TEMPLATES / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import update_profile

    return update_profile


def cmd_harvest(args: argparse.Namespace) -> int:
    argv = ["harvest"]
    if args.login:
        argv += ["--login", args.login]
    if args.out:
        argv += ["--out", args.out]
    return _load_updater().main(argv)


# --- render (copy templates/github into the profile repo clone) ---


@dataclass(frozen=True)
class TemplateFile:
    """One template -> destination mapping; ``requires`` gates on a --with flag."""

    src: str  # relative to templates/github
    dest: str  # relative to --target
    requires: str | None = None


TEMPLATE_FILES = (
    TemplateFile("scripts/update_profile.py", ".github/scripts/update_profile.py"),
    TemplateFile("workflows/profile-snake.yml", ".github/workflows/profile-snake.yml"),
    TemplateFile("workflows/profile-refresh.yml", ".github/workflows/profile-refresh.yml"),
    TemplateFile("workflows/profile-metrics.yml", ".github/workflows/profile-metrics.yml", requires="metrics"),
    TemplateFile("workflows/profile-claude-refresh.yml", ".github/workflows/profile-claude-refresh.yml", requires="claude"),
    # PROFILE_GUIDE.md sits at the TARGET ROOT (the Claude Action reads it there),
    # not under .github/ like everything else.
    TemplateFile("PROFILE_GUIDE.md", "PROFILE_GUIDE.md", requires="claude"),
)


def _random_minute() -> int:
    """Random cron minute per file: avoids the :00 thundering herd on GitHub's
    schedulers AND keeps this repo's own workflows from colliding."""
    return random.randint(0, 59)


def find_unrendered(text: str) -> list[str]:
    return sorted({m.group(0) for m in LEFTOVER.finditer(text)})


def render_plan(enabled: frozenset[str]) -> dict[str, str] | None:
    """Build dest -> content. None (after printing ERROR) on leftover tokens or
    template files missing from the manifest."""
    on_disk = {
        str(path.relative_to(TEMPLATES))
        for path in TEMPLATES.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    if stray := sorted(on_disk - {tf.src for tf in TEMPLATE_FILES}):
        print(f"ERROR: template files missing from the render manifest: {', '.join(stray)}", file=sys.stderr)
        return None

    plan: dict[str, str] = {}
    for tf in TEMPLATE_FILES:
        if tf.requires and tf.requires not in enabled:
            continue
        text = (TEMPLATES / tf.src).read_text()
        text = text.replace("{{CRON_MINUTE}}", str(_random_minute()))
        if leftover := find_unrendered(text):
            print(f"ERROR: unrendered placeholders in {tf.src}: {', '.join(leftover)}", file=sys.stderr)
            return None
        plan[tf.dest] = text
    return plan


def apply_plan(plan: dict[str, str], target: Path, force: bool) -> int:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"WROTE   {dest}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    flags = {f for f in args.with_.split(",") if f}
    if unknown := sorted(flags - set(WITH_FLAGS)):
        print(f"ERROR: unknown --with flags: {', '.join(unknown)}; known: {', '.join(WITH_FLAGS)}", file=sys.stderr)
        return 2
    plan = render_plan(frozenset(flags))
    if plan is None:
        return 1
    return apply_plan(plan, args.target, args.force)


# --- CLI entry ---


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="profile.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="check gh, auth, scopes, and the profile repo")

    harvest = sub.add_parser("harvest", help="build the dossier via the committed updater")
    harvest.add_argument("--login")
    harvest.add_argument("--out")

    render = sub.add_parser("render", help="render workflow templates into a profile repo clone")
    render.add_argument("--target", type=Path, required=True)
    render.add_argument("--with", dest="with_", default="", help=f"comma-separated: {', '.join(WITH_FLAGS)}")
    render.add_argument("--force", action="store_true", help="overwrite conflicting files")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "preflight":
        return cmd_preflight()
    if args.command == "harvest":
        return cmd_harvest(args)
    if args.command == "render":
        return cmd_render(args)
    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
