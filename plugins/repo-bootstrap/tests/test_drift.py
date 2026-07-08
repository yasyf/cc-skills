"""The drift checker and the scaffold stamp-pinning it verifies.

Fragments are envelopes: a line-1 begin stamp and a name-matched end marker
(``<!-- /canonical: …/_partials/<name>.md -->``). The checker locates a fragment by
its markers alone — the inner is the lines strictly between them — and never infers a
window from body-line counts. The partials and their canonical shas are fixtures (never
the real repo's shas): ``sha_for`` maps each partial's template path to a fixed sha, so a
target stamp either matches (ok/edited) or not (stale). The fixtures mirror the four real
shapes the checker must handle — a verbatim partial with a ``## `` heading (``conv``), a
heading-less verbatim partial rendered mid-section (``vc``, like version-control), a seed
partial with a heading (``readme-lead``), and a seed whose heading trails the stamp by a
couple of lines (``readme-use``, like readme-use-cases). The render-pinning test injects
fake per-partial shas through the same ``pinning_reader`` that production ``run`` uses.
"""

from __future__ import annotations

import datetime

import pytest
from bootstrap import drift, scaffold, stamp

DATE = datetime.date(2026, 6, 8)

SHA_CONV = "a" * 40
SHA_SEED = "b" * 40
SHA_SHELL = "c" * 40
SHA_VC = "d" * 40

CONV_BODY = "## Conventions\n\nFirst rule.\nSecond rule.\n"
VC_BODY = "**Version control.** Use jj, not git.\n\n**Watch CI.** Keep the run green.\n"
SEED_BODY = "## Readme Lead\n\nSeed prose to customize.\n"
USE_BODY = "---\n\n## Use cases\n\nTODO fill in.\n"


def md_stamp(name: str, sha: str) -> str:
    return f"<!-- canonical: {stamp.CANONICAL}/_partials/{name}.md@{sha} -->"


def md_end(name: str) -> str:
    return f"<!-- /canonical: {stamp.CANONICAL}/_partials/{name}.md -->"


def frag(name: str, sha: str, body: str) -> str:
    """A closed envelope: begin stamp, ``body`` (which ends in a newline), end marker."""
    return md_stamp(name, sha) + "\n" + body + md_end(name) + "\n"


def sh_stamp(sha: str) -> str:
    return f"# canonical: {stamp.CANONICAL}@{sha}"


@pytest.fixture
def partials(tmp_path):
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "conv.md").write_text(frag("conv", stamp.PENDING, CONV_BODY))
    (d / "vc.md").write_text(frag("vc", stamp.PENDING, VC_BODY))
    (d / "readme-lead.md").write_text(frag("readme-lead", stamp.PENDING, SEED_BODY))
    (d / "readme-use.md").write_text(frag("readme-use", stamp.PENDING, USE_BODY))
    return drift.discover_partials(d)


@pytest.fixture
def sha_for(partials):
    canon = {
        partials["conv"].path: SHA_CONV,
        partials["vc"].path: SHA_VC,
        partials["readme-lead"].path: SHA_SEED,
        partials["readme-use"].path: SHA_SEED,
        drift.SHELL_TEMPLATE: SHA_SHELL,
    }
    return canon.get


# --- discovery ---


def test_discover_classifies_and_anchors(partials):
    assert partials["conv"].kind == "verbatim"
    assert partials["conv"].anchor == "## Conventions"
    assert partials["vc"].kind == "verbatim"
    assert partials["vc"].anchor is None  # heading-less: recognized by stamp only
    assert partials["readme-lead"].kind == "seed"
    assert partials["readme-lead"].anchor == "## Readme Lead"
    assert partials["readme-use"].anchor == "## Use cases"  # trails the stamp
    # both envelope markers are stripped from the stored body
    assert "canonical:" not in partials["conv"].body
    assert "/canonical:" not in partials["conv"].body
    assert partials["conv"].body == CONV_BODY.rstrip("\n")


# --- verbatim partial with a heading ---


def test_ok(partials, sha_for):
    target = "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY)
    assert drift.check_target("t.md", target, partials, sha_for) == [
        drift.Finding("ok", SHA_CONV, "t.md", "conv", False)
    ]


def test_stale(partials, sha_for):
    # a verbatim fragment whose stamp sha != the canonical sha
    target = "# Doc\n\n" + frag("conv", SHA_SEED, CONV_BODY)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", SHA_SEED, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_edited(partials, sha_for):
    # sha matches, body differs -> edited (verbatim only)
    body = "## Conventions\n\nFirst rule.\nSecond rule CHANGED.\n"
    target = "# Doc\n\n" + frag("conv", SHA_CONV, body)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("edited", SHA_CONV, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_ok_modulo_trailing_whitespace(partials, sha_for):
    # trailing whitespace (per line + a trailing blank line before the end marker) is
    # forgiven, so an otherwise-identical body is ok, not edited; the end marker bounds
    # the fragment so "## Next" outside the envelope is never swept in
    body = "## Conventions   \n\nFirst rule.\nSecond rule.\n\n"
    target = "# Doc\n\n" + frag("conv", SHA_CONV, body) + "## Next\n"
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_CONV, "t.md", "conv", False)]


def test_pending_sha_is_stale_and_fails(partials, sha_for):
    # a deployed target should never carry @pending; if it does, the pin never ran,
    # so a verbatim fragment counts as stale and fails the exit
    target = "# Doc\n\n" + frag("conv", stamp.PENDING, CONV_BODY)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", stamp.PENDING, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_unterminated_verbatim_fails(partials, sha_for):
    # a begin stamp with no matching end marker: an open envelope is structural breakage,
    # reported unterminated and failing the exit — regardless of sha or body
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("unterminated", SHA_CONV, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_unterminated_precedes_stale(partials, sha_for):
    # an open envelope is diagnosed before the sha question: a stale AND unterminated
    # fragment surfaces as unterminated, not stale
    target = "# Doc\n\n" + md_stamp("conv", SHA_SEED) + "\n" + CONV_BODY
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("unterminated", SHA_SEED, "t.md", "conv", True)]


# --- heading-less verbatim fragment, rendered mid-section (like version-control) ---


def _embedded_vc(name: str, sha: str, body: str) -> str:
    # a heading-less fragment inlined between bullets inside a '## General Rules'
    # section, exactly how version-control.md renders in a real AGENTS.md
    return (
        "# Doc\n\n"
        "## General Rules\n\n"
        "**Earlier rule.** Something before.\n\n"
        + frag(name, sha, body)
        + "\n"
        "**Later rule.** Something after.\n"
    )


def test_headingless_ok_no_bleed(partials, sha_for):
    # the end marker bounds the fragment at its own last line, so the "**Later rule.**"
    # bullet that follows the envelope is never swept into the inner
    target = _embedded_vc("vc", SHA_VC, VC_BODY)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_VC, "t.md", "vc", False)]
    assert drift.exit_code(findings) == 0


def test_headingless_edited(partials, sha_for):
    mutated = "**Version control.** Use jj, not git.\n\n**Watch CI.** DIFFERENT NOW.\n"
    target = _embedded_vc("vc", SHA_VC, mutated)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("edited", SHA_VC, "t.md", "vc", True)]
    assert drift.exit_code(findings) == 1


def test_headingless_stale(partials, sha_for):
    target = _embedded_vc("vc", SHA_SEED, VC_BODY)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", SHA_SEED, "t.md", "vc", True)]
    assert drift.exit_code(findings) == 1


# --- unstamped / unknown ---


def test_unstamped_is_informational(partials, sha_for):
    # a known heading with no stamp above it: reported, but adoption is opt-in via
    # the stamp, so a bare matching heading never fails the exit
    target = "# Doc\n\n" + CONV_BODY
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("unstamped", None, "t.md", "conv", False)]
    assert drift.exit_code(findings) == 0


def test_unknown_bare_heading_ignored(partials, sha_for):
    # a bare heading that matches no known partial is not our concern
    target = "# Doc\n\n## Something Else\n\nbody\n"
    assert drift.check_target("t.md", target, partials, sha_for) == []


def test_unknown_stamp_reported(partials, sha_for):
    # a stamp naming no shipped partial likely means the partial was renamed or
    # removed in cc-skills -> report, don't fail
    target = "# Doc\n\n" + frag("ghost", SHA_CONV, "## Ghost\n\nbody\n")
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("unknown", SHA_CONV, "t.md", "ghost", False)]
    assert drift.exit_code(findings) == 0


def test_orphan_end_marker_ignored(partials, sha_for):
    # an end marker with no owning begin (nothing scans forward from it) is ignored by
    # the checker — no finding, no failure
    target = "# Doc\n\n" + md_end("conv") + "\n\nsome prose\n"
    assert drift.check_target("t.md", target, partials, sha_for) == []


def test_back_to_back_sections_do_not_bleed(partials, sha_for):
    # partials inline consecutively (envelope, blank, next envelope, …); each end marker
    # bounds its own fragment so the following stamp is never swept in -> both ok
    target = "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY) + "\n" + frag("readme-lead", SHA_SEED, SEED_BODY)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [
        drift.Finding("ok", SHA_CONV, "t.md", "conv", False),
        drift.Finding("ok", SHA_SEED, "t.md", "readme-lead", False),
    ]
    assert drift.exit_code(findings) == 0


def test_lines_gained_inside_envelope_is_edited(partials, sha_for):
    # a stamp spliced into an otherwise-exact fragment stays inside the envelope's inner,
    # so the outer fragment surfaces as edited — a splice is drift, not decoration. The
    # spliced inner is a seed begin-only stamp: sha-only, so ok on its own.
    lines = CONV_BODY.splitlines()
    body = "\n".join([lines[0], md_stamp("readme-lead", SHA_SEED), *lines[1:]]) + "\n"
    target = "# Doc\n\n" + frag("conv", SHA_CONV, body)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [
        drift.Finding("edited", SHA_CONV, "t.md", "conv", True),
        drift.Finding("ok", SHA_SEED, "t.md", "readme-lead", False),
    ]
    assert drift.exit_code(findings) == 1


def test_nested_different_name_envelope(partials, sha_for):
    # an inner envelope nested inside an outer one (partial-includes-partial): find_end
    # pairs each begin to its OWN end marker by name, so the outer inner spans the whole
    # nested fragment and each classifies independently. Here the outer body is exactly
    # the inner envelope, so the outer is 'edited' (its inner isn't conv's body) while the
    # inner vc matches its own body -> ok.
    inner = frag("vc", SHA_VC, VC_BODY)
    target = "# Doc\n\n" + frag("conv", SHA_CONV, inner)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [
        drift.Finding("edited", SHA_CONV, "t.md", "conv", True),
        drift.Finding("ok", SHA_VC, "t.md", "vc", False),
    ]


# --- seed class: staleness prints but never fails the exit, body never checked ---


def test_seed_stale_does_not_fail_exit(partials, sha_for):
    target = "# R\n\n" + frag("readme-lead", SHA_CONV, SEED_BODY)  # SHA_CONV != canonical SHA_SEED
    findings = drift.check_target("r.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", SHA_CONV, "r.md", "readme-lead", False)]
    assert drift.exit_code(findings) == 0


def test_seed_skips_body_match(partials, sha_for):
    # correct sha, divergent body: seed is ok (customized), never 'edited'
    body = "## Readme Lead\n\nA totally different, customized opener.\n"
    target = "# R\n\n" + frag("readme-lead", SHA_SEED, body)
    findings = drift.check_target("r.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_SEED, "r.md", "readme-lead", False)]


def test_seed_begin_only_stays_sha_only(partials, sha_for):
    # a legacy begin-only rendered seed (no end marker) is still sha-only: ok at the
    # canonical sha, stale otherwise, NEVER unterminated — seeds don't require an envelope
    ok_target = "# R\n\n" + md_stamp("readme-lead", SHA_SEED) + "\n" + SEED_BODY
    assert drift.check_target("r.md", ok_target, partials, sha_for) == [
        drift.Finding("ok", SHA_SEED, "r.md", "readme-lead", False)
    ]
    stale_target = "# R\n\n" + md_stamp("readme-lead", SHA_CONV) + "\n" + SEED_BODY
    stale = drift.check_target("r.md", stale_target, partials, sha_for)
    assert stale == [drift.Finding("stale", SHA_CONV, "r.md", "readme-lead", False)]
    assert drift.exit_code(stale) == 0


def test_seed_heading_trailing_stamp_attributes_by_stamp(partials, sha_for):
    # readme-use's heading sits two lines below the stamp ('---', blank, '## Use
    # cases'); attribution is by the stamp, not by an adjacent heading -> ok
    target = "# R\n\n" + frag("readme-use", SHA_SEED, USE_BODY)
    findings = drift.check_target("r.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_SEED, "r.md", "readme-use", False)]


def test_seed_heading_trailing_stamp_unstamped(partials, sha_for):
    # same shape, stamp removed: the '## Use cases' anchor still triggers unstamped
    target = "# R\n\n" + USE_BODY
    findings = drift.check_target("r.md", target, partials, sha_for)
    assert findings == [drift.Finding("unstamped", None, "r.md", "readme-use", False)]
    assert drift.exit_code(findings) == 0


# --- --require: presence is stamp-based (works for heading-less partials) ---


def test_missing_under_require(partials):
    target = "# Doc\n\nno stamps here\n"
    findings = drift.require_findings("t.md", target, ["conv"])
    assert findings == [drift.Finding("missing", None, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_require_satisfied_by_stamp(partials):
    target = "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY)
    assert drift.require_findings("t.md", target, ["conv"]) == []


def test_require_headingless_present(partials):
    # --require of a heading-less partial is satisfied by its stamp alone
    target = "# Doc\n\n" + frag("vc", SHA_VC, VC_BODY)
    assert drift.require_findings("t.md", target, ["vc"]) == []


def test_require_headingless_absent(partials):
    target = "# Doc\n\nnothing here\n"
    findings = drift.require_findings("t.md", target, ["vc"])
    assert findings == [drift.Finding("missing", None, "t.md", "vc", True)]
    assert drift.exit_code(findings) == 1


# --- shell-stamped targets (sha-only; scanned only for non-.md targets) ---


def test_shell_ok(partials, sha_for):
    target = "#!/bin/sh\n" + sh_stamp(SHA_SHELL) + "\nset -eu\n"
    findings = drift.check_target("install-binary.sh", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_SHELL, "install-binary.sh", "install-binary.sh", False)]


def test_shell_stale_fails_exit(partials, sha_for):
    target = "#!/bin/sh\n" + sh_stamp(SHA_SEED) + "\nset -eu\n"
    findings = drift.check_target("install-binary.sh", target, partials, sha_for)
    assert findings == [drift.Finding("stale", SHA_SEED, "install-binary.sh", "install-binary.sh", True)]
    assert drift.exit_code(findings) == 1


def test_shell_pending_is_stale_even_without_history(partials):
    # an unpinned deployed copy is stale even when git history is unavailable
    # (canonical=None), mirroring the markdown @pending rule
    target = "#!/bin/sh\n" + sh_stamp(stamp.PENDING) + "\nset -eu\n"
    findings = drift.check_target("install-binary.sh", target, partials, lambda p: None)
    assert findings == [drift.Finding("stale", stamp.PENDING, "install-binary.sh", "install-binary.sh", True)]
    assert drift.exit_code(findings) == 1


def test_shell_stamp_in_md_target_is_not_scanned(partials, sha_for):
    # a doc that quotes the shell-stamp syntax must not produce a false shell
    # finding; the same content in a shell file still does
    text = "# Doc\n\nRendered copies are stamped `" + sh_stamp(SHA_SEED) + "` on line 2.\n"
    assert drift.check_target("notes.md", text, partials, sha_for) == []
    assert drift.check_target("notes.sh", text, partials, sha_for) == [
        drift.Finding("stale", SHA_SEED, "notes.sh", "install-binary.sh", True)
    ]


# --- stamp pinning ---


def test_pin_preserves_named_segment():
    md = f"<!-- canonical: {stamp.CANONICAL}/_partials/ccx.md@{stamp.PENDING} -->"
    sh = f"# canonical: {stamp.CANONICAL}@{stamp.PENDING}"
    sha = "e" * 40
    # the markdown stamp keeps its partial-name segment; the shell stamp keeps none
    assert stamp.pin(md, sha) == f"<!-- canonical: {stamp.CANONICAL}/_partials/ccx.md@{sha} -->"
    assert stamp.pin(sh, sha) == f"# canonical: {stamp.CANONICAL}@{sha}"
    # both forms in one text are pinned together
    assert stamp.pin(md + "\n" + sh, sha) == (
        f"<!-- canonical: {stamp.CANONICAL}/_partials/ccx.md@{sha} -->\n# canonical: {stamp.CANONICAL}@{sha}"
    )


def test_pin_and_repin_leave_end_markers_untouched():
    # the end marker carries the name but no @sha, so neither pin (unpinned stamps) nor
    # repin (any stamp) rewrites it — repin touches only the begin line
    sha = "e" * 40
    envelope = frag("ccx", stamp.PENDING, "body line\n")
    assert stamp.pin(envelope, sha) == frag("ccx", sha, "body line\n")
    assert md_end("ccx") in stamp.pin(envelope, sha)
    pinned = frag("ccx", "9" * 40, "body line\n")
    assert stamp.repin(pinned, sha) == frag("ccx", sha, "body line\n")
    assert md_end("ccx") in stamp.repin(pinned, sha)


def test_scaffold_pins_each_partial_own_sha(base_var_pairs):
    r = scaffold.resolve("base", [], [], base_var_pairs, DATE)
    items = scaffold.select_files(r)
    fake = {
        "_partials/ccx.md": "1" * 40,
        "_partials/version-control.md": "2" * 40,
        "_partials/ask-before-assuming.md": "3" * 40,
        "_partials/readme-opener.md": "4" * 40,
    }
    read = scaffold.pinning_reader(scaffold.read_template, fake.get)
    plan, _ = scaffold.render_plan(items, r, read, scaffold.template_exists)
    agents, readme = plan["AGENTS.md"], plan["README.md"]
    # each partial pins to ITS OWN sha under its own self-identifying stamp
    assert f"{stamp.CANONICAL}/_partials/ccx.md@" + "1" * 40 in agents
    assert f"{stamp.CANONICAL}/_partials/version-control.md@" + "2" * 40 in agents
    assert f"{stamp.CANONICAL}/_partials/ask-before-assuming.md@" + "3" * 40 in agents
    assert f"{stamp.CANONICAL}/_partials/readme-opener.md@" + "4" * 40 in readme
    # partials whose sha we didn't fake are a best-effort miss -> left @pending
    assert f"@{stamp.PENDING} -->" in agents
    # every rendered begin stamp is closed by its own end marker (the pin never touched it)
    assert md_end("ccx") in agents
    assert md_end("readme-opener") in readme


def test_pinning_reader_no_sha_leaves_pending():
    read = scaffold.pinning_reader(lambda src: f"x {stamp.CANONICAL}@{stamp.PENDING} y", lambda src: None)
    assert read("whatever") == f"x {stamp.CANONICAL}@{stamp.PENDING} y"
