#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13"
# ///
"""Rotate cc-guides deploy keys — 1Password mints each key, GitHub gets it.
Per repo: archive any prior item, have 1Password generate an Ed25519 key (vault
OpenClaw, item cc-guides-deploy-key-<repo>), read it back, then replace the
repo's cc-guides-render deploy key and CC_GUIDES_DEPLOY_KEY actions secret.
1Password is the source of truth; repos default to the Great Docs fleet."""

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

VAULT = "OpenClaw"
GH_KEY_TITLE = "cc-guides-render"
SECRET_NAME = "CC_GUIDES_DEPLOY_KEY"
OWNER = "yasyf"
FLEET = [
    "captain-hook",
    "cc-transcript",
    "cc-steer",
    "docker-dsl",
    "experiment-at-home",
    "spawnllm",
    "cc-squash",
    "cc-orchestrate",
    "cc-review",
]


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, input=stdin, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"FAIL ({' '.join(args[:3])}…): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def item_title(repo: str) -> str:
    return f"cc-guides-deploy-key-{repo}"


def mint_in_1password(repo: str) -> tuple[str, str]:
    title = item_title(repo)
    exists = subprocess.run(
        ["op", "item", "get", title, "--vault", VAULT], capture_output=True, text=True
    )
    if exists.returncode == 0:
        run("op", "item", "delete", title, "--vault", VAULT, "--archive")
        print(f"  1password: archived prior {title}")
    notes = (
        f"Write deploy key for {OWNER}/{repo} cc-guides re-render pushes "
        f"(minted {datetime.now(UTC):%Y-%m-%d}). Public half: repo deploy key "
        f"'{GH_KEY_TITLE}'. Private half: {SECRET_NAME} actions secret. "
        f"Rotate with tools/guides-deploy-keys.py in cc-skills."
    )
    run(
        "op", "item", "create", "--vault", VAULT, "--category", "SSH Key",
        "--title", title, "--ssh-generate-key", "Ed25519", f"notesPlain={notes}",
    )
    private = run("op", "read", f"op://{VAULT}/{title}/private key?ssh-format=openssh").stdout
    public = run("op", "read", f"op://{VAULT}/{title}/public key").stdout.strip()
    if "OPENSSH PRIVATE KEY" not in private or not public.startswith("ssh-ed25519"):
        sys.exit(f"FAIL {repo}: unexpected key material read back from 1Password")
    print(f"  1password: minted op://{VAULT}/{title}")
    return private if private.endswith("\n") else private + "\n", public


def install_on_github(repo: str, private_key: str, public_key: str) -> None:
    slug = f"{OWNER}/{repo}"
    old_ids = run(
        "gh", "api", f"repos/{slug}/keys",
        "--jq", f'.[] | select(.title == "{GH_KEY_TITLE}") | .id',
    ).stdout.split()
    with tempfile.TemporaryDirectory() as tmp:
        pub_path = Path(tmp) / "key.pub"
        pub_path.write_text(public_key + "\n")
        for key_id in old_ids:
            run("gh", "api", "-X", "DELETE", f"repos/{slug}/keys/{key_id}")
            print(f"  github: removed old deploy key {key_id}")
        run(
            "gh", "repo", "deploy-key", "add", str(pub_path),
            "-R", slug, "--allow-write", "--title", GH_KEY_TITLE,
        )
    run("gh", "secret", "set", SECRET_NAME, "-R", slug, stdin=private_key)
    print(f"  github: deploy key + {SECRET_NAME} secret installed")


def rotate(repo: str) -> None:
    print(f"== {repo}")
    private_key, public_key = mint_in_1password(repo)
    install_on_github(repo, private_key, public_key)


def main() -> None:
    if os.environ.pop("OP_SERVICE_ACCOUNT_TOKEN", None):
        print("note: dropped read-only OP_SERVICE_ACCOUNT_TOKEN — using your 1Password user session")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repos", nargs="*", default=FLEET)
    for repo in parser.parse_args().repos:
        rotate(repo)
    print("done — all keys minted in 1Password, GitHub swapped")


if __name__ == "__main__":
    main()
