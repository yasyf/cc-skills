#!/usr/bin/env python3
"""repo-bootstrap CLI — one entry point for the whole skill.

    bootstrap.py identity                       resolve author/git identity
    bootstrap.py check-name NAME                 check a PyPI distribution name
    bootstrap.py scaffold  [flags]               render templates into a repo
    bootstrap.py verify    [--layer] [--target]  verify a scaffolded repo

STDLIB ONLY. identity / check-name / scaffold all run before ``uv`` exists, so
neither this file nor the ``bootstrap`` package may import third-party modules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bootstrap import identity, pypi, scaffold, verify
from bootstrap.manifest import EXTRAS, FEATURES


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bootstrap.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("identity", help="resolve author/git identity")

    check = sub.add_parser("check-name", help="check a PyPI distribution name")
    check.add_argument("name")

    sc = sub.add_parser("scaffold", help="render templates into a repo")
    sc.add_argument("--target", type=Path, default=Path("."))
    sc.add_argument("--layer", choices=("base", "python"), default="base")
    sc.add_argument("--extras", required=True, help=f"comma-separated: {', '.join(EXTRAS)}; or 'none' for no extras")
    sc.add_argument(
        "--features",
        default=",".join(f.name for f in FEATURES),
        help=f"python-only, comma-separated (default all): {', '.join(f.name for f in FEATURES)}. "
        "Pass a subset (or empty) to drop docs site / PyPI release.",
    )
    sc.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    sc.add_argument("--force", action="store_true", help="overwrite conflicting files")
    sc.add_argument("--dry-run", action="store_true")

    vf = sub.add_parser("verify", help="verify a scaffolded repo")
    vf.add_argument("--layer", choices=("base", "python"), default="base")
    vf.add_argument("--target", default=".")

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
        return verify.main(args.layer, args.target)
    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
