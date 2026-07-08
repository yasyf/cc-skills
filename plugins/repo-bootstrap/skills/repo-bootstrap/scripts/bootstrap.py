#!/usr/bin/env python3
"""repo-bootstrap CLI — one entry point for the whole skill.

    bootstrap.py identity                       resolve author/git identity
    bootstrap.py check-name NAME                 check a PyPI distribution name
    bootstrap.py scaffold  [flags]               render templates into a repo
    bootstrap.py verify    [--layer] [--target] [--no-license]  verify a scaffolded repo
    bootstrap.py trust     [--target] [--home] [--config]  mark a repo trusted for Claude Code
    bootstrap.py drift     TARGET… [--require PARTIAL]  check stamped partials against canon
    bootstrap.py sync      TARGET… [--write]         update stamped partials toward canon

STDLIB ONLY. identity / check-name / scaffold all run before ``uv`` exists, so
neither this file nor the ``bootstrap`` package may import third-party modules.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bootstrap import drift, identity, pypi, scaffold, sync, trust, verify
from bootstrap.manifest import EXTRAS, FEATURES


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bootstrap.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("identity", help="resolve author/git identity")

    check = sub.add_parser("check-name", help="check a PyPI distribution name")
    check.add_argument("name")

    sc = sub.add_parser("scaffold", help="render templates into a repo")
    sc.add_argument("--target", type=Path, default=Path("."))
    sc.add_argument("--layer", choices=("base", "python", "go", "swift", "swift-app"), default="base")
    sc.add_argument("--extras", required=True, help=f"comma-separated: {', '.join(EXTRAS)}; or 'none' for no extras")
    sc.add_argument(
        "--features",
        default=",".join(f.name for f in FEATURES if f.default),
        help=f"layer-scoped, comma-separated (default = on-by-default features for the layer). "
        f"Known: {', '.join(f.name for f in FEATURES)}. Features outside the chosen layer are "
        "ignored; opt-in features (maturin, release) must be named explicitly. Pass a subset "
        "(or empty) to drop the python docs site / PyPI release, or the go/swift release pipeline.",
    )
    sc.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    sc.add_argument("--force", action="store_true", help="overwrite conflicting files")
    sc.add_argument("--dry-run", action="store_true")

    vf = sub.add_parser("verify", help="verify a scaffolded repo")
    vf.add_argument("--layer", choices=("base", "python", "go", "swift", "swift-app"), default="base")
    vf.add_argument("--target", default=".")
    vf.add_argument("--no-license", action="store_true", help="license `none` was chosen: require LICENSE absent")

    tr = sub.add_parser("trust", help="mark a repo trusted for Claude Code")
    tr.add_argument("--target", default=".", help="repo to trust (resolved to an absolute path)")
    tr.add_argument("--home", default=os.path.expanduser("~"), help="home dir holding ~/.claude.json and any ~/.cc-pool/accounts/*")
    tr.add_argument("--config", default=None, help="base .claude.json path (default: <home>/.claude.json)")

    dr = sub.add_parser(
        "drift",
        help="check stamped partials in target files against their canonical source",
        description="Scan each TARGET for self-identifying canonical stamps (and known "
        "partial anchor headings), printing one TSV finding per line "
        "(status<TAB>sha<TAB>path<TAB>name). Exits non-zero when a stamped verbatim-class "
        "fragment is stale/edited, a shell stamp is stale, or a --require'd stamp is "
        "missing; unstamped/unknown findings and seed-class (readme*) staleness print but "
        "never fail the exit — the stamp is the opt-in contract, and seed partials are "
        "customized per-repo.",
    )
    dr.add_argument("targets", nargs="+", type=Path, help="files to check (AGENTS.md, README.md, install-binary.sh copies)")
    dr.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="PARTIAL",
        dest="require",
        help="partial name (basename without .md) whose stamp must be present; repeatable",
    )

    sy = sub.add_parser(
        "sync",
        help="update stamped partials in target files toward their canonical source",
        description="Scan each TARGET for self-identifying canonical stamps and mechanically "
        "update each stamped fragment toward its current canonical partial. Per fragment a "
        "three-way decides the move: one still matching the body it was stamped from is "
        "rewritten to the current body and re-pinned (synced); one already holding the current "
        "body only has its stamp re-pinned (repinned); one diverging from both is a decision, "
        "not drift, and is left untouched (skipped-edited). The replaced window is measured from "
        "the ORIGINAL body at the stamp sha, so a partial that grew or shrank still splices "
        "cleanly. Dry-run by default (prints a 5-column TSV: status<TAB>old-sha<TAB>new-sha<TAB>"
        "path<TAB>name); pass --write to apply. ALWAYS exits 0 — sync is the fixer, drift is the "
        "gate (compose as `sync --write && drift`). Caveat: scaffold renders install-binary.sh "
        "with {{BINARY_NAME}}/{{PLUGIN_NAME}}/{{RELEASE_REPO}}/{{BREW_PACKAGE}} substituted, so a "
        "stale RENDERED shell copy matches neither template side and always reports "
        "skipped-edited — sync maintains unrendered copies only.",
    )
    sy.add_argument(
        "targets", nargs="+", type=Path, help="files to update (AGENTS.md, README.md, install-binary.sh copies)"
    )
    sy.add_argument("--write", action="store_true", help="apply the updates (default: dry-run, print findings only)")

    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "identity":
        return identity.main()
    if args.command == "check-name":
        return pypi.main(args.name)
    if args.command == "scaffold":
        return scaffold.run(args)
    if args.command == "verify":
        return verify.main(args.layer, args.target, args.no_license)
    if args.command == "trust":
        return trust.trust_repo(args.target, args.home, args.config)
    if args.command == "drift":
        return drift.main(args.targets, args.require)
    if args.command == "sync":
        return sync.main(args.targets, args.write)
    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
