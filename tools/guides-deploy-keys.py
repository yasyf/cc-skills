#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Rotate cc-guides deploy keys — 1Password mints each key, GitHub gets it.
Per repo: archive any prior item, generate an Ed25519 key (vault OpenClaw, item
cc-guides-deploy-key-<repo>), then replace the repo's cc-guides-render deploy
key and CC_GUIDES_DEPLOY_KEY actions secret. Repos default to the discovered
fleet: every non-archived source repo carrying .github/workflows/guides.yml."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fleetlib"))

from fleetlib import gh, op  # noqa: E402

VAULT = "OpenClaw"
GH_KEY_TITLE = "cc-guides-render"
SECRET_NAME = "CC_GUIDES_DEPLOY_KEY"
GUIDES_WORKFLOW = ".github/workflows/guides.yml"


def rotate(repo: str) -> None:
    """Each step failing leaves a working key/secret pair: old ids are fetched
    first (validating the repo before 1Password mutates), the new key and secret
    land before any old key is removed."""
    print(f"== {repo}")
    title = f"cc-guides-deploy-key-{repo}"
    notes = (
        f"Write deploy key for {gh.OWNER}/{repo} cc-guides re-render pushes "
        f"(minted {datetime.now(UTC):%Y-%m-%d}). Public half: repo deploy key "
        f"'{GH_KEY_TITLE}'. Private half: {SECRET_NAME} actions secret. "
        f"Rotate with tools/guides-deploy-keys.py in cc-skills."
    )
    old_ids = gh.deploy_key_ids(repo, GH_KEY_TITLE)
    private_key, public_key = op.mint_ssh_key(VAULT, title, notes)
    gh.add_deploy_key(repo, GH_KEY_TITLE, public_key)
    gh.set_secret(repo, SECRET_NAME, private_key)
    gh.delete_deploy_keys(repo, old_ids)
    print(f"  github: deploy key + {SECRET_NAME} secret installed")


def main() -> None:
    op.require_user_session()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repos", nargs="*", help=f"explicit repos (default: every repo carrying {GUIDES_WORKFLOW})"
    )
    parser.add_argument("--list", action="store_true", help="print the target repos and exit")
    args = parser.parse_args()
    repos = args.repos or gh.repos_with_file(GUIDES_WORKFLOW)
    if not args.repos:
        print(f"fleet: {len(repos)} repos carry {GUIDES_WORKFLOW}")
    if args.list:
        print("\n".join(repos))
        return
    for repo in repos:
        rotate(repo)
    print("done — all keys minted in 1Password, GitHub swapped")


if __name__ == "__main__":
    main()
