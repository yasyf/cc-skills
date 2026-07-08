"""The drift checker and the scaffold stamp-pinning it verifies.

The partials and their canonical shas are fixtures (never the real repo's shas):
``sha_for`` maps each partial's template path to a fixed sha, so a target stamp
either matches (ok/edited) or not (stale). The fixtures mirror the four real shapes
the checker must handle — a verbatim partial with a ``## `` heading (``conv``), a
heading-less verbatim partial rendered mid-section (``vc``, like version-control), a
seed partial with a heading (``readme-lead``), and a seed whose heading trails the
stamp by a couple of lines (``readme-use``, like readme-use-cases). The
render-pinning test injects fake per-partial shas through the same ``pinning_reader``
that production ``run`` uses.
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


def sh_stamp(sha: str) -> str:
    return f"# canonical: {stamp.CANONICAL}@{sha}"


@pytest.fixture
def partials(tmp_path):
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "conv.md").write_text(md_stamp("conv", stamp.PENDING) + "\n" + CONV_BODY)
    (d / "vc.md").write_text(md_stamp("vc", stamp.PENDING) + "\n" + VC_BODY)
    (d / "readme-lead.md").write_text(md_stamp("readme-lead", stamp.PENDING) + "\n" + SEED_BODY)
    (d / "readme-use.md").write_text(md_stamp("readme-use", stamp.PENDING) + "\n" + USE_BODY)
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
    # the line-1 stamp is stripped from the stored body
    assert "canonical:" not in partials["conv"].body


# --- verbatim partial with a heading ---


def test_ok(partials, sha_for):
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY
    assert drift.check_target("t.md", target, partials, sha_for) == [
        drift.Finding("ok", SHA_CONV, "t.md", "conv", False)
    ]


def test_stale(partials, sha_for):
    # a verbatim fragment whose stamp sha != the canonical sha
    target = "# Doc\n\n" + md_stamp("conv", SHA_SEED) + "\n" + CONV_BODY
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", SHA_SEED, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_edited(partials, sha_for):
    # sha matches, body differs -> edited (verbatim only)
    body = "## Conventions\n\nFirst rule.\nSecond rule CHANGED.\n"
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + body
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("edited", SHA_CONV, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


def test_ok_modulo_trailing_whitespace(partials, sha_for):
    # trailing whitespace (per line + a trailing blank line before the next heading)
    # is forgiven, so an otherwise-identical body is ok, not edited; the L-line window
    # stops before "## Next" so it is not swept in
    body = "## Conventions   \n\nFirst rule.\nSecond rule.\n\n"
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + body + "## Next\n"
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_CONV, "t.md", "conv", False)]


def test_pending_sha_is_stale_and_fails(partials, sha_for):
    # a deployed target should never carry @pending; if it does, the pin never ran,
    # so a verbatim fragment counts as stale and fails the exit
    target = "# Doc\n\n" + md_stamp("conv", stamp.PENDING) + "\n" + CONV_BODY
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", stamp.PENDING, "t.md", "conv", True)]
    assert drift.exit_code(findings) == 1


# --- heading-less verbatim fragment, rendered mid-section (like version-control) ---


def _embedded_vc(stamp_line: str, body: str) -> str:
    # a heading-less fragment inlined between bullets inside a '## General Rules'
    # section, exactly how version-control.md renders in a real AGENTS.md
    return (
        "# Doc\n\n"
        "## General Rules\n\n"
        "**Earlier rule.** Something before.\n\n"
        + stamp_line
        + "\n"
        + body
        + "\n"
        "**Later rule.** Something after.\n"
    )


def test_headingless_ok_no_bleed(partials, sha_for):
    # the L-line body window must stop at the fragment's own last line and never
    # bleed into the "**Later rule.**" bullet that follows it
    target = _embedded_vc(md_stamp("vc", SHA_VC), VC_BODY)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_VC, "t.md", "vc", False)]
    assert drift.exit_code(findings) == 0


def test_headingless_edited(partials, sha_for):
    mutated = "**Version control.** Use jj, not git.\n\n**Watch CI.** DIFFERENT NOW.\n"
    target = _embedded_vc(md_stamp("vc", SHA_VC), mutated)
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("edited", SHA_VC, "t.md", "vc", True)]
    assert drift.exit_code(findings) == 1


def test_headingless_stale(partials, sha_for):
    target = _embedded_vc(md_stamp("vc", SHA_SEED), VC_BODY)
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
    target = "# Doc\n\n" + md_stamp("ghost", SHA_CONV) + "\n## Ghost\n\nbody\n"
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [drift.Finding("unknown", SHA_CONV, "t.md", "ghost", False)]
    assert drift.exit_code(findings) == 0


def test_back_to_back_sections_do_not_bleed(partials, sha_for):
    # partials inline consecutively (stamp, body, blank, next stamp, …); the
    # following fragment's stamp must not be swept into this body -> both ok
    target = (
        "# Doc\n\n"
        + md_stamp("conv", SHA_CONV)
        + "\n"
        + CONV_BODY
        + "\n"
        + md_stamp("readme-lead", SHA_SEED)
        + "\n"
        + SEED_BODY
    )
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [
        drift.Finding("ok", SHA_CONV, "t.md", "conv", False),
        drift.Finding("ok", SHA_SEED, "t.md", "readme-lead", False),
    ]
    assert drift.exit_code(findings) == 0


def test_spliced_stamp_inside_fragment_is_edited(partials, sha_for):
    # a stamp spliced into an otherwise-exact fragment stays in the L-line window,
    # so the outer fragment surfaces as edited — a splice is drift, not decoration
    lines = CONV_BODY.splitlines()
    body = "\n".join([lines[0], md_stamp("readme-lead", SHA_SEED), *lines[1:]]) + "\n"
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + body
    findings = drift.check_target("t.md", target, partials, sha_for)
    assert findings == [
        drift.Finding("edited", SHA_CONV, "t.md", "conv", True),
        drift.Finding("ok", SHA_SEED, "t.md", "readme-lead", False),
    ]
    assert drift.exit_code(findings) == 1


# --- seed class: staleness prints but never fails the exit, body never checked ---


def test_seed_stale_does_not_fail_exit(partials, sha_for):
    target = "# R\n\n" + md_stamp("readme-lead", SHA_CONV) + "\n" + SEED_BODY  # SHA_CONV != canonical SHA_SEED
    findings = drift.check_target("r.md", target, partials, sha_for)
    assert findings == [drift.Finding("stale", SHA_CONV, "r.md", "readme-lead", False)]
    assert drift.exit_code(findings) == 0


def test_seed_skips_body_match(partials, sha_for):
    # correct sha, divergent body: seed is ok (customized), never 'edited'
    body = "## Readme Lead\n\nA totally different, customized opener.\n"
    target = "# R\n\n" + md_stamp("readme-lead", SHA_SEED) + "\n" + body
    findings = drift.check_target("r.md", target, partials, sha_for)
    assert findings == [drift.Finding("ok", SHA_SEED, "r.md", "readme-lead", False)]


def test_seed_heading_trailing_stamp_attributes_by_stamp(partials, sha_for):
    # readme-use's heading sits two lines below the stamp ('---', blank, '## Use
    # cases'); attribution is by the stamp, not by an adjacent heading -> ok
    target = "# R\n\n" + md_stamp("readme-use", SHA_SEED) + "\n" + USE_BODY
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
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY
    assert drift.require_findings("t.md", target, ["conv"]) == []


def test_require_headingless_present(partials):
    # --require of a heading-less partial is satisfied by its stamp alone
    target = "# Doc\n\n" + md_stamp("vc", SHA_VC) + "\n" + VC_BODY
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


def test_pinning_reader_no_sha_leaves_pending():
    read = scaffold.pinning_reader(lambda src: f"x {stamp.CANONICAL}@{stamp.PENDING} y", lambda src: None)
    assert read("whatever") == f"x {stamp.CANONICAL}@{stamp.PENDING} y"
