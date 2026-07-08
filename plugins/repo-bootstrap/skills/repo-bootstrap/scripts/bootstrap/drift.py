"""The ``drift`` subcommand: check stamped partials in target files against canon.

Each shipped ``templates/_partials/*.md`` carries a line-1 self-identifying stamp
naming the partial and a sha — ``cc-skills/plugins/repo-bootstrap/_partials/
<basename>.md@<sha|pending>``. A rendered copy carries that same stamp inlined
directly above the fragment it introduces, so attribution never depends on what
follows the stamp: the checker scans a target for stamp lines *anywhere* and each
one names the partial it belongs to. Per finding:

    ok         sha == canonical AND (verbatim) the fragment body matches the partial
    stale      sha != canonical  (a target sha of ``pending`` counts as stale)
    edited     sha == canonical but the fragment body differs (verbatim only)
    unstamped  a known partial's ``## `` anchor heading present with no stamp naming it
    unknown    a stamp naming no shipped partial (renamed/removed in cc-skills)
    missing    a --require'd partial's stamp absent from the target

A partial's *class* is ``seed`` when its basename starts with ``readme`` (rendered
once, then customized per-repo) else ``verbatim`` (a byte copy that must not drift).
Seed fragments are sha-only: never body-checked, and their staleness never fails.

The verbatim body check compares the ``L`` lines following the stamp (``L`` = the
partial's own body line count) against the partial body, tolerant of trailing
whitespace. An insertion or deletion inside a fragment shifts the window and
surfaces as ``edited``; content appended after the fragment's last line is outside
the window and belongs to the enclosing file, not the fragment.

``unstamped`` detection is anchor-based, so it fires only for partials that have a
``## `` heading; a heading-less partial (e.g. version-control) is recognized solely
by its stamp and is never reported ``unstamped``. One TSV line per finding:
``status<TAB>sha-or-'-'<TAB>path<TAB>name``.

Exit is non-zero when a stamped verbatim-class fragment is stale or edited, a shell
stamp is stale, or a --require'd stamp is missing. ``unstamped``, ``unknown``, and
seed-class staleness are informational — the stamp is the opt-in contract, and a
seed partial is expected to diverge once a repo customizes it.

STDLIB ONLY — this package runs under the system ``python3`` before ``uv`` exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import stamp

TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"
SHELL_TEMPLATE = TEMPLATES / "plugin" / "install-binary.sh"

ShaResolver = Callable[[Path], "str | None"]


@dataclass(frozen=True)
class Partial:
    """One shipped partial: its template path, drift class, section anchor, and the
    canonical body lines (the template content with the line-1 stamp removed)."""

    name: str
    path: Path
    kind: str  # "seed" | "verbatim"
    anchor: str | None
    body_lines: tuple[str, ...]

    @property
    def body(self) -> str:
        return "\n".join(self.body_lines)


@dataclass(frozen=True)
class Finding:
    """One drift result. ``fails`` marks it as contributing to a non-zero exit."""

    status: str
    sha: str | None
    path: str
    name: str
    fails: bool


def discover_partials(partials_dir: Path) -> dict[str, Partial]:
    """Load ``*.md`` partials under ``partials_dir``, keyed by basename stem."""
    out: dict[str, Partial] = {}
    for path in sorted(partials_dir.glob("*.md")):
        lines = path.read_text().splitlines()
        stamped = bool(lines) and stamp.md_stamp(lines[0]) is not None
        body_lines = lines[1:] if stamped else lines
        anchor = next((line for line in body_lines if line.startswith("## ")), None)
        out[path.stem] = Partial(
            name=path.stem,
            path=path,
            kind="seed" if path.stem.startswith("readme") else "verbatim",
            anchor=anchor,
            body_lines=tuple(body_lines),
        )
    return out


def normalize(block: str) -> str:
    """Collapse trailing whitespace (per line and overall) for body comparison."""
    return "\n".join(line.rstrip() for line in block.splitlines()).rstrip("\n")


def fragment_body(lines: list[str], stamp_idx: int, length: int) -> str:
    """The ``length`` lines following ``stamp_idx`` — the target's copy of the
    stamped fragment. A stamp line inside the window (a splice, or a following
    fragment's stamp reached because this fragment ran short) stays in the body
    and surfaces as ``edited`` — the correct drift signal either way."""
    return "\n".join(lines[stamp_idx + 1 : stamp_idx + 1 + length])


def classify_stamped(
    path: str, name: str, found_sha: str, lines: list[str], stamp_idx: int, partial: Partial, sha_for: ShaResolver
) -> Finding:
    canonical = sha_for(partial.path)
    stale = found_sha == stamp.PENDING or (canonical is not None and found_sha != canonical)
    if partial.kind == "seed":
        return Finding("stale" if stale else "ok", found_sha, path, name, fails=False)
    if stale:
        return Finding("stale", found_sha, path, name, fails=True)
    if normalize(fragment_body(lines, stamp_idx, len(partial.body_lines))) != normalize(partial.body):
        return Finding("edited", found_sha, path, name, fails=True)
    return Finding("ok", found_sha, path, name, fails=False)


def classify_shell(path: str, found_sha: str, canonical: str | None) -> Finding:
    if found_sha == stamp.PENDING or (canonical is not None and found_sha != canonical):
        return Finding("stale", found_sha, path, SHELL_TEMPLATE.name, fails=True)
    return Finding("ok", found_sha, path, SHELL_TEMPLATE.name, fails=False)


def check_target(
    path: str, text: str, partials: dict[str, Partial], sha_for: ShaResolver, shell_template: Path = SHELL_TEMPLATE
) -> list[Finding]:
    """Findings for one target: stamped fragments (attributed by name), shell stamps
    (non-``.md`` targets only), and bare known anchor headings without a stamp."""
    findings: list[Finding] = []
    lines = text.splitlines()
    is_md = path.endswith(".md")
    matched: set[str] = set()

    for i, line in enumerate(lines):
        parsed = stamp.md_stamp(line)
        if parsed is not None:
            name, found_sha = parsed
            partial = partials.get(name)
            if partial is None:
                findings.append(Finding("unknown", found_sha, path, name, fails=False))
            else:
                findings.append(classify_stamped(path, name, found_sha, lines, i, partial, sha_for))
                matched.add(name)
            continue
        if not is_md and (sh_sha := stamp.sh_stamp_sha(line)) is not None:
            findings.append(classify_shell(path, sh_sha, sha_for(shell_template)))

    anchors = {p.anchor.rstrip(): p for p in partials.values() if p.anchor}
    for line in lines:
        if not line.startswith("## "):
            continue
        partial = anchors.get(line.rstrip())
        if partial is None or partial.name in matched:
            continue
        findings.append(Finding("unstamped", None, path, partial.name, fails=False))
    return findings


def require_findings(path: str, text: str, required: list[str]) -> list[Finding]:
    """A ``missing`` finding per --require'd partial whose stamp is absent — presence
    is stamp-based, so it works for heading-less partials too."""
    stamped = {parsed[0] for line in text.splitlines() if (parsed := stamp.md_stamp(line)) is not None}
    return [Finding("missing", None, path, name, fails=True) for name in required if name not in stamped]


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.fails for f in findings) else 0


def main(targets: list[Path], required: list[str]) -> int:
    partials = discover_partials(TEMPLATES / "_partials")
    findings: list[Finding] = []
    for target in targets:
        text = target.read_text()
        findings.extend(check_target(str(target), text, partials, stamp.canonical_sha))
        findings.extend(require_findings(str(target), text, required))
    for f in findings:
        print(f"{f.status}\t{f.sha or '-'}\t{f.path}\t{f.name}")
    return exit_code(findings)
