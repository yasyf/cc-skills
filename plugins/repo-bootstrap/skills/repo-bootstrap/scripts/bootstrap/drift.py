"""The ``drift`` subcommand: check stamped partials in target files against canon.

Each shipped ``templates/_partials/*.md`` is an *envelope*: a line-1 self-identifying
begin stamp naming the partial and a sha — ``cc-skills/plugins/repo-bootstrap/
_partials/<basename>.md@<sha|pending>`` — and a matching end marker
``<!-- /canonical: …/_partials/<basename>.md -->`` closing it. A rendered copy carries
that same envelope inlined directly above the fragment it introduces, so attribution
never depends on what follows: the checker scans a target for begin stamps *anywhere*,
each naming its partial, and pairs each to its own end marker by name. Per finding:

    ok            sha == canonical AND (verbatim) the enveloped body matches the partial
    stale         sha != canonical  (a target sha of ``pending`` counts as stale)
    edited        sha == canonical but the enveloped body differs (verbatim only)
    unterminated  a verbatim begin stamp with no matching end marker (an open envelope)
    unstamped     a known partial's ``## `` anchor heading present with no stamp naming it
    unknown       a stamp naming no shipped partial (renamed/removed in cc-skills)
    missing       a --require'd partial's stamp absent from the target

A partial's *class* is ``seed`` when its basename starts with ``readme`` (rendered
once, then customized per-repo) else ``verbatim`` (a byte copy that must not drift).
Seed fragments are sha-only: never body-checked, their staleness never fails, and their
envelope is neither required nor examined (legacy begin-only rendered seeds stay legal).

The verbatim body check compares the lines strictly between the begin stamp and its
end marker against the partial body, tolerant of trailing whitespace. The end marker
bounds the fragment exactly, so an insertion or deletion inside it surfaces as
``edited`` while content after the marker belongs to the enclosing file; the tool never
infers a window from body-line counts. A begin stamp whose end marker is missing is
structural breakage — ``unterminated`` — and fails the exit.

``unstamped`` detection is anchor-based, so it fires only for partials that have a
``## `` heading; a heading-less partial (e.g. version-control) is recognized solely
by its stamp and is never reported ``unstamped``. One TSV line per finding:
``status<TAB>sha-or-'-'<TAB>path<TAB>name``.

Exit is non-zero when a stamped verbatim-class fragment is stale, edited, or
unterminated, a shell stamp is stale, or a --require'd stamp is missing. ``unstamped``,
``unknown``, and seed-class staleness are informational — the stamp is the opt-in
contract, and a seed partial is expected to diverge once a repo customizes it.

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
    canonical body lines (the template content with the line-1 begin stamp and the
    final end-marker line removed)."""

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
        # An envelope: strip the line-1 begin stamp and the final end-marker line
        # (tolerating a partial that is missing either — after v0.39.0 all carry both).
        if lines and stamp.md_stamp(lines[0]) is not None:
            lines = lines[1:]
        if lines and stamp.md_end(lines[-1]) is not None:
            lines = lines[:-1]
        body_lines = lines
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


def classify_stamped(
    path: str, name: str, found_sha: str, lines: list[str], stamp_idx: int, partial: Partial, sha_for: ShaResolver
) -> Finding:
    canonical = sha_for(partial.path)
    stale = found_sha == stamp.PENDING or (canonical is not None and found_sha != canonical)
    # Seeds are sha-only: never body-checked, staleness never fails, envelope ignored.
    if partial.kind == "seed":
        return Finding("stale" if stale else "ok", found_sha, path, name, fails=False)
    # Verbatim: the fragment is the lines strictly between the begin stamp and its end
    # marker. An open envelope is structural breakage before any sha/body question.
    end_idx = stamp.find_end(lines, stamp_idx, name)
    if end_idx is None:
        return Finding("unterminated", found_sha, path, name, fails=True)
    if stale:
        return Finding("stale", found_sha, path, name, fails=True)
    inner = "\n".join(lines[stamp_idx + 1 : end_idx])
    if normalize(inner) != normalize(partial.body):
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
