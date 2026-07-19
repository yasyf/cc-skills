"""1Password helpers: user-session guard and SSH-key minting."""

import os
import sys

from fleetlib.proc import run, try_run


def require_user_session() -> None:
    """Drop a read-only service-account token so `op` uses the user session."""
    if os.environ.pop("OP_SERVICE_ACCOUNT_TOKEN", None):
        print("note: dropped read-only OP_SERVICE_ACCOUNT_TOKEN — using your 1Password user session")


def mint_ssh_key(vault: str, title: str, notes: str) -> tuple[str, str]:
    """Have 1Password generate an Ed25519 key, archiving any prior same-titled
    item, and return (private_openssh, public) read back from the vault."""
    prior = try_run("op", "item", "get", title, "--vault", vault)
    if prior.returncode == 0:
        run("op", "item", "delete", title, "--vault", vault, "--archive")
        print(f"  1password: archived prior {title}")
    elif "isn't an item" not in prior.stderr:
        sys.exit(f"FAIL probing {vault}/{title}: {prior.stderr.strip()}")
    run(
        "op", "item", "create", "--vault", vault, "--category", "SSH Key",
        "--title", title, "--ssh-generate-key", "Ed25519", f"notesPlain={notes}",
    )
    private = run("op", "read", f"op://{vault}/{title}/private key?ssh-format=openssh").stdout
    public = run("op", "read", f"op://{vault}/{title}/public key").stdout.strip()
    if "OPENSSH PRIVATE KEY" not in private or not public.startswith("ssh-ed25519"):
        sys.exit(f"FAIL {title}: unexpected key material read back from 1Password")
    print(f"  1password: minted op://{vault}/{title}")
    return private if private.endswith("\n") else private + "\n", public
