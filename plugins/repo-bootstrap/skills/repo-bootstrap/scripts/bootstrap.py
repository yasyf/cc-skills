#!/usr/bin/env python3
"""repo-bootstrap CLI — one entry point for the whole skill.

    bootstrap.py identity                       resolve author/git identity
    bootstrap.py check-name NAME                 check a PyPI distribution name
    bootstrap.py scaffold  [flags]               render templates into a repo
    bootstrap.py verify    [--layer] [--target] [--no-license]  verify a scaffolded repo
    bootstrap.py trust     [--target] [--home] [--config]  mark a repo trusted for Claude Code

STDLIB ONLY. identity / check-name / scaffold all run before ``uv`` exists, so
neither this file nor the ``bootstrap`` package may import third-party modules.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bootstrap import identity, pypi, scaffold, trust, verify
from bootstrap.manifest import EXTRAS, FEATURES, SECONDARY_LAYERS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bootstrap.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("identity", help="resolve author/git identity")

    check = sub.add_parser("check-name", help="check a PyPI distribution name")
    check.add_argument("name")

    sc = sub.add_parser("scaffold", help="render templates into a repo")
    sc.add_argument("--target", type=Path, default=Path("."))
    sc.add_argument("--layer", choices=("base", "python", "go", "swift", "swift-app", "bun"), default="base")
    sc.add_argument(
        "--secondary-layer",
        choices=SECONDARY_LAYERS,
        default=None,
        help="add a second language's styleguide beside its code (--var SECONDARY_CODE_ROOT=<dir>) plus an "
        "AGENTS.md style pointer, without its toolchain; must differ from --layer",
    )
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
    vf.add_argument("--layer", choices=("base", "python", "go", "swift", "swift-app", "bun"), default="base")
    vf.add_argument("--target", default=".")
    vf.add_argument("--no-license", action="store_true", help="license `none` was chosen: require LICENSE absent")

    tr = sub.add_parser("trust", help="mark a repo trusted for Claude Code")
    tr.add_argument("--target", default=".", help="repo to trust (resolved to an absolute path)")
    tr.add_argument("--home", default=os.path.expanduser("~"), help="home dir holding ~/.claude.json and any ~/.cc-pool/accounts/*")
    tr.add_argument("--config", default=None, help="base .claude.json path (default: <home>/.claude.json)")

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
    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
