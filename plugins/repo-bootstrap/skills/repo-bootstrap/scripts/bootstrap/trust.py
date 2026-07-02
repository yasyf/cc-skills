"""trust subcommand: mark a bootstrapped repo trusted for Claude Code.

STDLIB ONLY (this runs under the system ``python3`` alongside the rest of the
engine, before ``uv`` exists).

Claude Code stores per-project trust as a boolean at
``projects["<abspath>"].hasTrustDialogAccepted`` in ``~/.claude.json`` — NOT in
``.claude/settings.json``. That file is large, mode 0600, and actively rewritten
by any running Claude Code process, so we mutate it with an atomic
read-modify-write (temp file in the same dir + ``os.replace``, mode 0600) that
preserves every other key. See cc-pool's ``WriteAtomic0600`` for the reference
behavior this mirrors.

On cc-pool machines each pooled account keeps its own
``~/.cc-pool/accounts/*/.claude.json``, and an overlay shares
``hasTrustDialogAccepted`` bidirectionally with the base file — so writing the
base is enough. We still best-effort apply the same write to any account files
so trust is immediate, skipping silently when the glob matches nothing.
"""

from __future__ import annotations

import glob
import json
import os
import tempfile

_TRUST_KEY = "hasTrustDialogAccepted"


def _load(config_path: str) -> dict:
    """Parse ``config_path`` as a JSON object. A missing or empty file (and a
    literal ``null``) is treated as ``{}``; any other non-object is an error
    rather than something we'd silently clobber."""
    try:
        with open(config_path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} is not a JSON object")
    return data


def _write_atomic_0600(config_path: str, data: dict) -> None:
    """Write ``data`` as JSON to ``config_path`` via a temp file in the same
    directory plus ``os.replace``, at mode 0600 — so a concurrent reader never
    sees a torn file. Mirrors cc-pool's ``WriteAtomic0600``."""
    directory = os.path.dirname(config_path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(config_path) + ".tmp.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, config_path)
        tmp = ""  # renamed away — nothing left to clean up
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def set_trusted(config_path: str, repo: str) -> bool:
    """Idempotently set ``projects[repo].hasTrustDialogAccepted = True`` in
    ``config_path``, creating the ``projects`` map and the ``projects[repo]``
    entry if absent and preserving all other keys. Returns ``True`` if the file
    was written, ``False`` if it was already trusted (no write needed)."""
    data = _load(config_path)
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(repo, {})
    if entry.get(_TRUST_KEY) is True:
        return False
    entry[_TRUST_KEY] = True
    _write_atomic_0600(config_path, data)
    return True


def _account_configs(home: str) -> list[str]:
    return sorted(glob.glob(os.path.join(home, ".cc-pool", "accounts", "*", ".claude.json")))


def trust_repo(target: str, home: str, config: str | None = None) -> int:
    """Mark ``target`` (resolved to an absolute path) trusted in the base
    ``~/.claude.json`` and, best-effort, in every cc-pool account config under
    ``home``. ``config`` overrides the base file (defaults to
    ``<home>/.claude.json``)."""
    repo = os.path.abspath(target)
    base = config or os.path.join(home, ".claude.json")

    set_trusted(base, repo)
    print(f"TRUSTED  {repo}")

    updated = 0
    for account_config in _account_configs(home):
        try:
            set_trusted(account_config, repo)
            updated += 1
        except (OSError, ValueError):
            continue  # best-effort: the overlay already shares trust from the base file
    if updated:
        print(f"         cc-pool: applied to {updated} account config(s)")
    return 0
