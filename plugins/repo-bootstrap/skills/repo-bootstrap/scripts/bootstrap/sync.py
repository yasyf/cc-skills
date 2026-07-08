"""The ``sync`` subcommand: mechanically update stamped partials in target files.

Where ``drift`` reports how each stamped fragment stands against canon, ``sync`` moves
it there. For every self-identifying stamp it finds, it runs a three-way against the
partial's current body and the body the fragment was stamped from (recovered from git at
the stamp sha):

    synced          the fragment still matches the body it was stamped from — replace
                    that body with the current one and re-pin the stamp
    repinned        the fragment already holds the current body, only the stamp trails —
                    re-pin the stamp line alone
    skipped-edited  the fragment matches neither the stamped-from nor the current body —
                    a local decision, never overwritten
    ok              already at the canonical sha with a matching (verbatim) body, or any
                    seed at the canonical sha — nothing to do
    pending         an unpinned ``@pending`` stamp (the pin never ran) — reported, skipped
    unknown         a stamp naming no shipped partial — reported, skipped
    no-history      the canonical sha or the stamped-from blob is unavailable (installed
                    plugin cache, or a sha with no recorded blob) — reported, skipped

The replaced window's length is the *stamped-from* body's line count, never the current
partial's: a partial that grew or shrank between the stamp sha and canon would otherwise
excise the wrong span. When the stamped-from and current bodies are in a prefix relationship
(lines appended or trailing lines dropped), a target can match BOTH windows; the longer
matching window wins — equal lengths mean identical bodies, so a stamp-only re-pin — which
keeps an already-updated fragment from re-splicing the current body over its own prefix and
duplicating the appended lines. Seeds (basename ``readme*``, customized per repo) take the same
three-way when stale — an untouched seed syncs, a customized one is ``skipped-edited``
with its stamp left unpinned (provenance honesty) — but at the canonical sha a seed is
always ``ok`` (its body is never checked, mirroring ``drift``).

The shell template (``install-binary.sh``) is handled whole-file: an unrendered copy still
matching the stamped-from template (trailing-whitespace-tolerant, like the markdown
comparison) is replaced by the current one (pinned), a copy already matching the current
template is re-pinned, anything else is ``skipped-edited``. A whole-file replacement runs
only when the shell stamp is the file's sole canonical stamp. Caveat: scaffold renders
``install-binary.sh`` with ``{{BINARY_NAME}}`` / ``{{PLUGIN_NAME}}`` / ``{{RELEASE_REPO}}``
/ ``{{BREW_PACKAGE}}`` (and ``{{#PINNED}}`` / ``{{#LATEST}}`` sections) substituted, so a
stale *rendered* copy matches neither template side and always reports ``skipped-edited``
— ``sync`` maintains unrendered copies only; re-rendering a stale copy is out of scope.

Markdown edits are applied bottom-up (descending stamp index) so an earlier splice never
invalidates a later stamp's index; a synced window can never contain another stamp (a
stamp-free original body wouldn't have matched), so windows never overlap. A file's CRLF
or LF line endings and its trailing newline are preserved.

One TSV line per finding, five columns: ``status<TAB>old-sha-or-'-'<TAB>new-sha-or-'-'
<TAB>path<TAB>name``. ``new-sha`` is the sha the stamp carries after the sync — the re-pin
target for ``synced``/``repinned``, the confirmed current sha for ``ok``, and ``-`` for the
statuses that leave the stamp untouched and unconfirmed (``skipped-edited``, ``unknown``,
``pending``, ``no-history``). Dry-run by default; ``--write`` applies the edits. ``sync``
ALWAYS exits 0 — it is the fixer, ``drift`` is the gate (compose as ``sync --write &&
drift``); a ``skipped-edited`` fragment is informational, exactly like ``drift``'s
``unstamped``.

STDLIB ONLY — this package runs under the system ``python3`` before ``uv`` exists.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import stamp
from .drift import (
    SHELL_TEMPLATE,
    TEMPLATES,
    Partial,
    ShaResolver,
    discover_partials,
    fragment_body,
    normalize,
)

# ``(sha, template_path) -> that file's text at that commit, or None``. Injected like
# drift's ``sha_for``: production shells ``git show``, tests pass dict closures.
BodyResolver = Callable[[str, Path], "str | None"]


@dataclass(frozen=True)
class SyncFinding:
    """One sync result: ``old_sha`` is the stamp as found, ``new_sha`` the sha it was
    re-pinned to (None when the stamp is left untouched), plus the target path and the
    partial name."""

    status: str
    old_sha: str | None
    new_sha: str | None
    path: str
    name: str


@dataclass(frozen=True)
class MdEdit:
    """A markdown fragment rewrite: re-pin the stamp at ``stamp_idx`` to ``new_sha`` and
    replace the ``window`` lines after it with ``replacement`` (``window`` 0 / empty
    ``replacement`` for a stamp-line-only re-pin)."""

    stamp_idx: int
    new_sha: str
    window: int
    replacement: tuple[str, ...]


@dataclass(frozen=True)
class ShellEdit:
    """A whole-file shell rewrite: ``full_text`` wholly replaces the target — the current
    template pinned to ``new_sha`` for a sync, or the target with its stamp line re-pinned."""

    stamp_idx: int
    new_sha: str
    full_text: str


def git_body_at(sha: str, template_path: Path, root: Path | None = None) -> str | None:
    """The text of ``template_path`` as of commit ``sha`` in the cc-skills checkout, or
    None when unavailable (no git root, a sha with no such blob, or a path outside the
    root). ``git show`` addresses a blob by a *root-relative* posix path — unlike the
    ``git log --`` in ``stamp.canonical_sha``, which takes any path."""
    if root is None:
        root = stamp._repo_root()
    if root is None:
        return None
    try:
        rel = template_path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"{sha}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _blob_md_lines(blob: str) -> tuple[str, ...]:
    """The body lines of a committed markdown-partial blob: its lines minus a line-1
    canonical stamp (committed templates carry ``@pending`` there, so it is always the
    stamp). Returned as a tuple, never a joined string, so a body ending in a blank line
    keeps its trailing empty element — the excision window has to span it."""
    lines = blob.splitlines()
    stamped = bool(lines) and stamp.md_stamp(lines[0]) is not None
    return tuple(lines[1:] if stamped else lines)


def _blob_sh_body(blob: str) -> str:
    """The body of a shell target or template: every line except the single canonical
    shell stamp, wherever it sits (the real template stamps line 2, under the shebang)."""
    return "\n".join(line for line in blob.splitlines() if stamp.sh_stamp_sha(line) is None)


def classify_md(
    path: str,
    name: str,
    found_sha: str,
    lines: list[str],
    stamp_idx: int,
    partial: Partial,
    sha_for: ShaResolver,
    body_at: BodyResolver,
) -> tuple[SyncFinding, MdEdit | None]:
    """Classify one markdown stamp and, when it updates, the edit that realizes it."""
    canonical = sha_for(partial.path)
    if found_sha == stamp.PENDING:
        return SyncFinding("pending", found_sha, None, path, name), None
    if canonical is None:
        return SyncFinding("no-history", found_sha, None, path, name), None
    if found_sha == canonical:
        # At the canonical sha: a seed is ``ok`` regardless of body (never body-checked,
        # like drift); a verbatim is ``ok`` only if its body still matches, else a local
        # edit we must not overwrite — there is nothing newer to sync toward.
        if partial.kind == "seed" or normalize(fragment_body(lines, stamp_idx, len(partial.body_lines))) == normalize(
            partial.body
        ):
            return SyncFinding("ok", found_sha, found_sha, path, name), None
        return SyncFinding("skipped-edited", found_sha, None, path, name), None
    # Stale: recover the partial body as it stood at the stamp sha and run the three-way.
    # The window is the ORIGINAL body's line count — the current length would excise the
    # wrong span when the partial grew or shrank.
    original_blob = body_at(found_sha, partial.path)
    if original_blob is None:
        return SyncFinding("no-history", found_sha, None, path, name), None
    original_lines = _blob_md_lines(original_blob)
    old_window = len(original_lines)
    cur_window = len(partial.body_lines)
    matches_original = normalize(fragment_body(lines, stamp_idx, old_window)) == normalize("\n".join(original_lines))
    matches_current = normalize(fragment_body(lines, stamp_idx, cur_window)) == normalize(partial.body)
    # When the stamped-from body is a prefix of the current one (or vice versa) BOTH windows
    # can match the target — the longer window is the true fit; a shorter one that also
    # matches only caught the prefix. Prefer the longer; on equal lengths a double match
    # means identical bodies, so a stamp-only re-pin. Taking the shorter ``synced`` window
    # would splice the current body over a prefix and duplicate the appended lines.
    if matches_original and (not matches_current or old_window > cur_window):
        return (
            SyncFinding("synced", found_sha, canonical, path, name),
            MdEdit(stamp_idx, canonical, old_window, partial.body_lines),
        )
    if matches_current:
        return SyncFinding("repinned", found_sha, canonical, path, name), MdEdit(stamp_idx, canonical, 0, ())
    return SyncFinding("skipped-edited", found_sha, None, path, name), None


def classify_sh(
    path: str,
    found_sha: str,
    text: str,
    stamp_idx: int,
    stamp_count: int,
    sha_for: ShaResolver,
    body_at: BodyResolver,
    shell_template: Path,
) -> tuple[SyncFinding, ShellEdit | None]:
    """Classify the shell stamp and, when it updates, the whole-file rewrite. An unrendered
    copy still matching the stamped-from template (trailing-whitespace-tolerant, via
    ``normalize``) syncs to the current template; one already at the current template
    re-pins; a rendered (token-substituted) or otherwise-diverged copy is ``skipped-edited``.
    Both rewrites are refused (demoted to ``skipped-edited``) unless the shell stamp is the
    file's sole canonical stamp."""
    name = shell_template.name
    canonical = sha_for(shell_template)
    if found_sha == stamp.PENDING:
        return SyncFinding("pending", found_sha, None, path, name), None
    if canonical is None:
        return SyncFinding("no-history", found_sha, None, path, name), None
    if found_sha == canonical:
        # Sha-only ``ok`` at the current sha, mirroring drift — the rendered shell body is
        # substituted ({{BINARY_NAME}} …) and never byte-checked.
        return SyncFinding("ok", found_sha, found_sha, path, name), None
    original_blob = body_at(found_sha, shell_template)
    if original_blob is None:
        return SyncFinding("no-history", found_sha, None, path, name), None
    fragment = normalize(_blob_sh_body(text))
    if fragment == normalize(_blob_sh_body(original_blob)):
        if stamp_count != 1:
            return SyncFinding("skipped-edited", found_sha, None, path, name), None
        replacement = stamp.pin(shell_template.read_text(), canonical)
        return SyncFinding("synced", found_sha, canonical, path, name), ShellEdit(stamp_idx, canonical, replacement)
    if fragment == normalize(_blob_sh_body(shell_template.read_text())):
        if stamp_count != 1:
            return SyncFinding("skipped-edited", found_sha, None, path, name), None
        return SyncFinding("repinned", found_sha, canonical, path, name), ShellEdit(
            stamp_idx, canonical, stamp.repin(text, canonical)
        )
    return SyncFinding("skipped-edited", found_sha, None, path, name), None


def sync_target(
    path: str,
    text: str,
    partials: dict[str, Partial],
    sha_for: ShaResolver,
    body_at: BodyResolver,
    shell_template: Path = SHELL_TEMPLATE,
) -> tuple[list[SyncFinding], str]:
    """Classify every stamped fragment in one target and return the findings plus the
    rewritten text (identical when nothing synced/repinned). Markdown edits apply bottom-up
    so an earlier splice never invalidates a later stamp index; the shell stamp drives a
    whole-file replacement (mutually exclusive with markdown edits, as shell stamps are
    scanned only in non-``.md`` targets). The target's CRLF or LF line endings and its
    trailing newline are preserved."""
    lines = text.splitlines()
    is_md = path.endswith(".md")
    newline = "\r\n" if "\r\n" in text else "\n"
    # count stamp OCCURRENCES, not lines carrying one — two stamps on a single physical
    # line must not slip through the sole-canonical-stamp guard in classify_sh.
    stamp_count = len(stamp.STAMP_ANY.findall(text))

    findings: list[SyncFinding] = []
    md_edits: list[MdEdit] = []
    shell_edit: ShellEdit | None = None

    for i, line in enumerate(lines):
        parsed = stamp.md_stamp(line)
        if parsed is not None:
            name, found_sha = parsed
            partial = partials.get(name)
            if partial is None:
                findings.append(SyncFinding("unknown", found_sha, None, path, name))
                continue
            finding, edit = classify_md(path, name, found_sha, lines, i, partial, sha_for, body_at)
            findings.append(finding)
            if edit is not None:
                md_edits.append(edit)
            continue
        if not is_md and (sh_sha := stamp.sh_stamp_sha(line)) is not None:
            finding, sh_edit = classify_sh(path, sh_sha, text, i, stamp_count, sha_for, body_at, shell_template)
            findings.append(finding)
            if sh_edit is not None:
                shell_edit = sh_edit

    if shell_edit is not None:
        return findings, shell_edit.full_text
    if not md_edits:
        return findings, text

    new_lines = list(lines)
    for edit in sorted(md_edits, key=lambda e: e.stamp_idx, reverse=True):
        new_lines[edit.stamp_idx] = stamp.repin(new_lines[edit.stamp_idx], edit.new_sha)
        new_lines[edit.stamp_idx + 1 : edit.stamp_idx + 1 + edit.window] = edit.replacement
    new_text = newline.join(new_lines)
    if text.endswith("\n") and not new_text.endswith(newline):
        new_text += newline
    return findings, new_text


def main(
    targets: list[Path],
    write: bool,
    *,
    partials_dir: Path = TEMPLATES / "_partials",
    sha_for: ShaResolver = stamp.canonical_sha,
    body_at: BodyResolver = git_body_at,
) -> int:
    """Sync every target, writing changed files only under ``write``. Prints one TSV line
    per finding and ALWAYS returns 0 — sync fixes, drift gates."""
    partials = discover_partials(partials_dir)
    findings: list[SyncFinding] = []
    for target in targets:
        # newline="" keeps CRLF/LF intact across the round-trip (unlike read_text's
        # universal-newline translation); sync_target rebuilds in the file's own ending.
        with target.open(newline="") as fh:
            text = fh.read()
        target_findings, new_text = sync_target(str(target), text, partials, sha_for, body_at)
        if write and new_text != text:
            with target.open("w", newline="") as fh:
                fh.write(new_text)
        findings.extend(target_findings)
    for f in findings:
        print(f"{f.status}\t{f.old_sha or '-'}\t{f.new_sha or '-'}\t{f.path}\t{f.name}")
    return 0
