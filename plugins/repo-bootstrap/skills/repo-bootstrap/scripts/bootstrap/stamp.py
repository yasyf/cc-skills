"""Provenance stamps: pin a shipped file to the commit that last touched it.

Every ``templates/_partials/*.md`` (and ``templates/plugin/install-binary.sh``)
carries a line-1 canonical stamp naming this package and a sha — ``@pending`` in
the template, the last-touch commit sha in a rendered copy. Two comment syntaxes
share the canonical prefix: HTML ``<!-- canonical: … -->`` for markdown, ``#
canonical: …`` for shell. A markdown stamp is *self-identifying* — it names its
source partial (``…/_partials/<basename>.md@<sha>``) so a drift checker can
attribute an inlined fragment without depending on what follows it; the shell stamp
is file-level and names the package alone. Pinning is best-effort (git history may
be absent in an installed plugin cache); drift-checking against the pinned sha (see
``drift.py``) is not.

STDLIB ONLY — this package runs under the system ``python3`` before ``uv`` exists.
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

CANONICAL = "cc-skills/plugins/repo-bootstrap"
PENDING = "pending"

SHA = r"(?P<sha>[0-9a-f]{40}|" + PENDING + r")"
# A markdown stamp names its source partial by basename; a shell stamp is
# file-level and carries only the canonical package prefix.
STAMP_MD = re.compile(r"<!-- canonical: " + re.escape(CANONICAL) + r"/_partials/(?P<name>[^/@\s]+)\.md@" + SHA + r" -->")
STAMP_SH = re.compile(r"# canonical: " + re.escape(CANONICAL) + r"@" + SHA)
# Every ``@pending`` under the canonical prefix, path segment (if any) preserved.
STAMP_PENDING = re.compile(re.escape(CANONICAL) + r"(?P<seg>[^@\s]*)@" + PENDING)


def md_stamp(line: str) -> tuple[str, str] | None:
    """The (partial name, sha-or-``pending``) of a markdown stamp on ``line``, else None."""
    m = STAMP_MD.search(line)
    return (m.group("name"), m.group("sha")) if m else None


def sh_stamp_sha(line: str) -> str | None:
    """The sha (or ``pending``) of a shell stamp on ``line``, else None."""
    m = STAMP_SH.search(line)
    return m.group("sha") if m else None


@cache
def _repo_root() -> Path | None:
    """The cc-skills git checkout root, walking up from this file; None if none
    is found (an installed plugin cache has no ``.git``)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


@cache
def canonical_sha(template_path: Path) -> str | None:
    """The sha of the last commit touching ``template_path`` in the cc-skills
    checkout, or None when git history is unavailable (installed plugin cache,
    or a path with no recorded history)."""
    root = _repo_root()
    if root is None:
        return None
    proc = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%H", "--", str(template_path)],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() or None


def pin(text: str, sha: str) -> str:
    """Replace every ``@pending`` stamp in ``text`` with ``@<sha>``, preserving each
    stamp's path segment (so a self-identifying markdown stamp keeps its partial name
    while the file-level shell stamp keeps none)."""
    return STAMP_PENDING.sub(lambda m: f"{CANONICAL}{m.group('seg')}@{sha}", text)
