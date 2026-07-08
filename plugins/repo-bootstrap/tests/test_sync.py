"""The sync updater: mechanically move stamped fragments to their canonical body.

Fragments are envelopes: a begin stamp and a name-matched end marker delimit the inner,
so sync locates and replaces a fragment by its markers alone — no window is ever inferred
from body-line counts (the v0.38.1 prefix/longest-match machinery is gone). Fixtures
mirror test_drift.py — current partials written as closed envelopes fed through
``discover_partials``, fake 40-char shas, and dict-closure resolvers injected as callables
(never a mocked subprocess for the pure-logic tests). ``CONV_OLD_BODY`` (3 lines) vs
``CONV_BODY`` (5 lines) prove a fragment that grew still syncs cleanly now that the end
marker bounds the replacement. The blobs ``body_at`` returns lead with their own
``@pending`` begin stamp; the conv blob is a PRE-envelope commit (no end marker) to pin
``_blob_md_lines`` tolerance, the others are closed envelopes. Only
``test_git_body_at_real_repo`` shells out — to a throwaway ``git init`` repo.
"""

from __future__ import annotations

import subprocess

import pytest
from bootstrap import stamp, sync

SHA_OLD = "1" * 40  # a stale stamp sha (the body was stamped here)
SHA_CONV = "a" * 40  # current canonical sha for conv
SHA_SEED = "b" * 40  # current canonical sha for readme-lead
SHA_SHELL = "c" * 40  # current canonical sha for the shell template
SHA_VC = "d" * 40  # current canonical sha for vc (heading-less)

# conv: old 3 body lines, current 5 — a fragment that grew between shas.
CONV_OLD_BODY = "## Conventions\n\nOld first rule.\n"
CONV_BODY = "## Conventions\n\nNew first rule.\nNew second rule.\nNew third rule.\n"
# vc: heading-less, old 3 lines, current 5 — the second fragment for the ordering tests.
VC_OLD_BODY = "**Version control.** Old rule.\n\n**Watch CI.** Old CI rule.\n"
VC_BODY = "**Version control.** New rule.\n\n**Watch CI.** New CI rule.\n\n**Extra.** Added line.\n"
# readme-lead: a seed.
SEED_OLD_BODY = "## Readme Lead\n\nOld seed prose.\n"
SEED_BODY = "## Readme Lead\n\nNew seed prose to customize.\n"

SHELL_BODY = "#!/bin/sh\n" + "# canonical: " + stamp.CANONICAL + "@" + stamp.PENDING + "\nset -eu\necho new\n"
SHELL_OLD_BODY = "#!/bin/sh\n" + "# canonical: " + stamp.CANONICAL + "@" + stamp.PENDING + "\nset -eu\necho old\n"


def md_stamp(name: str, sha: str) -> str:
    return f"<!-- canonical: {stamp.CANONICAL}/_partials/{name}.md@{sha} -->"


def md_end(name: str) -> str:
    return f"<!-- /canonical: {stamp.CANONICAL}/_partials/{name}.md -->"


def frag(name: str, sha: str, body: str) -> str:
    """A closed envelope: begin stamp, ``body`` (which ends in a newline), end marker."""
    return md_stamp(name, sha) + "\n" + body + md_end(name) + "\n"


def sh_stamp(sha: str) -> str:
    return f"# canonical: {stamp.CANONICAL}@{sha}"


# --- fixtures ---


@pytest.fixture
def partials_dir(tmp_path):
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "conv.md").write_text(frag("conv", stamp.PENDING, CONV_BODY))
    (d / "vc.md").write_text(frag("vc", stamp.PENDING, VC_BODY))
    (d / "readme-lead.md").write_text(frag("readme-lead", stamp.PENDING, SEED_BODY))
    return d


@pytest.fixture
def partials(partials_dir):
    return sync.discover_partials(partials_dir)


@pytest.fixture
def shell_template(tmp_path):
    p = tmp_path / "install-binary.sh"
    p.write_text(SHELL_BODY)
    return p


@pytest.fixture
def sha_for(partials, shell_template):
    canon = {
        partials["conv"].path: SHA_CONV,
        partials["vc"].path: SHA_VC,
        partials["readme-lead"].path: SHA_SEED,
        shell_template: SHA_SHELL,
    }
    return canon.get


@pytest.fixture
def body_at(partials, shell_template):
    # committed blobs at the OLD sha, each leading with its own @pending begin stamp. The
    # conv blob is a PRE-envelope commit (no end marker); vc and readme-lead are closed
    # envelopes (post-envelope commits) — _blob_md_lines yields the same body either way.
    blobs = {
        (SHA_OLD, partials["conv"].path): md_stamp("conv", stamp.PENDING) + "\n" + CONV_OLD_BODY,
        (SHA_OLD, partials["vc"].path): frag("vc", stamp.PENDING, VC_OLD_BODY),
        (SHA_OLD, partials["readme-lead"].path): frag("readme-lead", stamp.PENDING, SEED_OLD_BODY),
        (SHA_OLD, shell_template): SHELL_OLD_BODY,
    }
    return lambda sha, path: blobs.get((sha, path))


# --- _blob_md_lines: tolerates a pre-envelope (no end marker) blob ---


def test_blob_md_lines_tolerates_missing_end_marker():
    pre = md_stamp("conv", stamp.PENDING) + "\n" + CONV_OLD_BODY  # pre-envelope: no end marker
    post = frag("conv", stamp.PENDING, CONV_OLD_BODY)  # post-envelope: closed
    assert sync._blob_md_lines(pre) == sync._blob_md_lines(post)
    assert sync._blob_md_lines(pre) == tuple(CONV_OLD_BODY.splitlines())


# --- markdown three-way: synced / repinned / skipped-edited / ok ---


def test_synced_replaces_inner_and_repins(partials, sha_for, body_at):
    # a stale fragment still holding the body it was stamped from -> inner swapped for the
    # current one and the stamp re-pinned to canonical
    target = "# Doc\n\n" + frag("conv", SHA_OLD, CONV_OLD_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert new_text == "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY)


def test_synced_preserves_content_after_envelope(partials, sha_for, body_at):
    # content after the end marker belongs to the enclosing file and must survive the sync
    # untouched — the marker bounds the replacement, no body-line counting required
    target = "# Doc\n\n" + frag("conv", SHA_OLD, CONV_OLD_BODY) + "## Keep\n\nkeep me.\n"
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert new_text == "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY) + "## Keep\n\nkeep me.\n"


def test_synced_excises_trailing_blank_inside_envelope(partials, sha_for):
    # an inner ENDING with a blank line before the end marker: the whole inner is excised,
    # so no stale blank lingers as residue. (The tuple-based _blob_md_lines keeps the
    # trailing empty element for a faithful stamped-from comparison.)
    old_body = "## Conventions\n\nOld rule.\n\n"
    blob = md_stamp("conv", stamp.PENDING) + "\n" + old_body
    body_at = lambda sha, path: blob if (sha, path) == (SHA_OLD, partials["conv"].path) else None
    target = "# Doc\n\n" + frag("conv", SHA_OLD, old_body) + "## Next\n"
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert "Old rule." not in new_text
    assert new_text == "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY) + "## Next\n"


def test_repinned_rewrites_stamp_line_only(partials, sha_for, body_at):
    # the inner already equals the current body, only the stamp trails -> re-pin the stamp
    # line alone, inner and end marker untouched
    target = "# Doc\n\n" + frag("conv", SHA_OLD, CONV_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("repinned", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert new_text == "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY)


def test_skipped_edited_never_overwrites(partials, sha_for, body_at):
    # diverged from BOTH the stamped-from and the current body -> a local decision, left
    # untouched, stamp NOT re-pinned
    body = "## Conventions\n\nA custom rule nobody shipped.\n"
    target = "# Doc\n\n" + frag("conv", SHA_OLD, body)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target


def test_lines_gained_inside_envelope_is_skipped(partials, sha_for, body_at):
    # a fragment that gained a line INSIDE its envelope matches neither the stamped-from
    # nor the current body -> skipped-edited, never clobbered or duplicated
    body = "## Conventions\n\nOld first rule.\nSNUCK IN.\n"
    target = "# Doc\n\n" + frag("conv", SHA_OLD, body)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target
    assert new_text.count("SNUCK IN.") == 1


def test_edited_at_current_sha_is_skipped(partials, sha_for, body_at):
    # a verbatim fragment at the CURRENT sha but edited inner -> nothing newer to sync to
    body = "## Conventions\n\nLocally edited.\n"
    target = "# Doc\n\n" + frag("conv", SHA_CONV, body)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_CONV, None, "t.md", "conv")]
    assert new_text == target


def test_ok_noop(partials, sha_for, body_at):
    # at the canonical sha with a matching inner -> ok, nothing rewritten
    target = "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("ok", SHA_CONV, SHA_CONV, "t.md", "conv")]
    assert new_text == target


def test_unterminated_no_edit_both_classes(partials, sha_for, body_at):
    # an open envelope (begin stamp, no matching end marker) -> unterminated, never edited,
    # for a verbatim AND a seed fragment alike
    conv_open = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY
    f1, t1 = sync.sync_target("t.md", conv_open, partials, sha_for, body_at)
    assert f1 == [sync.SyncFinding("unterminated", SHA_OLD, None, "t.md", "conv")]
    assert t1 == conv_open
    seed_open = "# R\n\n" + md_stamp("readme-lead", SHA_OLD) + "\n" + SEED_OLD_BODY
    f2, t2 = sync.sync_target("r.md", seed_open, partials, sha_for, body_at)
    assert f2 == [sync.SyncFinding("unterminated", SHA_OLD, None, "r.md", "readme-lead")]
    assert t2 == seed_open


def test_orphan_end_marker_ignored(partials, sha_for, body_at):
    # an end marker with no owning begin stamp is ignored -> no finding, no edit
    target = "# Doc\n\n" + md_end("conv") + "\n\nsome prose\n"
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == []
    assert new_text == target


def test_unknown_stamp_skipped(partials, sha_for, body_at):
    # a stamp naming no shipped partial -> unknown, never rewritten
    target = "# Doc\n\n" + frag("ghost", SHA_CONV, "## Ghost\n\nbody\n")
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("unknown", SHA_CONV, None, "t.md", "ghost")]
    assert new_text == target


def test_pending_stamp_skipped(partials, sha_for, body_at):
    # an unpinned @pending stamp (the pin never ran) -> pending, never rewritten
    target = "# Doc\n\n" + frag("conv", stamp.PENDING, CONV_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("pending", stamp.PENDING, None, "t.md", "conv")]
    assert new_text == target


def test_no_history_when_blob_unresolvable(partials, sha_for):
    # stale sha, but the blob at that sha can't be recovered -> no-history
    target = "# Doc\n\n" + frag("conv", SHA_OLD, CONV_OLD_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, lambda sha, path: None)
    assert findings == [sync.SyncFinding("no-history", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target


def test_no_history_when_canonical_unavailable(partials, body_at):
    # canonical sha unavailable (installed plugin cache) -> no-history before any envelope work
    target = "# Doc\n\n" + frag("conv", SHA_OLD, CONV_OLD_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, lambda path: None, body_at)
    assert findings == [sync.SyncFinding("no-history", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target


# --- extension / shrink now classify trivially (the prefix ambiguity is structurally gone) ---


def test_extension_classifies_trivially(tmp_path):
    # OLD body is a strict PREFIX of NEW (two lines appended). The end marker bounds the
    # inner, so a target holding NEW at a stale stamp is unambiguously repinned (never
    # re-spliced and duplicated), and an untouched@old sibling is unambiguously synced.
    old_body = "## Ext\n\nBase rule.\n"
    new_body = "## Ext\n\nBase rule.\nAppended one.\nAppended two.\n"  # OLD == NEW[0:3]
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "ext.md").write_text(frag("ext", stamp.PENDING, new_body))
    partials = sync.discover_partials(d)
    sha_for = {partials["ext"].path: SHA_CONV}.get
    old_blob = frag("ext", stamp.PENDING, old_body)
    body_at = lambda sha, path: old_blob if (sha, path) == (SHA_OLD, partials["ext"].path) else None

    updated = "# Doc\n\n" + frag("ext", SHA_OLD, new_body)
    findings, new_text = sync.sync_target("t.md", updated, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("repinned", SHA_OLD, SHA_CONV, "t.md", "ext")]
    assert new_text == "# Doc\n\n" + frag("ext", SHA_CONV, new_body)
    assert new_text.count("Appended one.") == 1  # not duplicated

    untouched = "# Doc\n\n" + frag("ext", SHA_OLD, old_body)
    findings2, new_text2 = sync.sync_target("t.md", untouched, partials, sha_for, body_at)
    assert findings2 == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "ext")]
    assert new_text2 == "# Doc\n\n" + frag("ext", SHA_CONV, new_body)
    # idempotent: a re-run of the synced result is a no-op
    again, again_text = sync.sync_target("t.md", new_text2, partials, sha_for, body_at)
    assert again == [sync.SyncFinding("ok", SHA_CONV, SHA_CONV, "t.md", "ext")]
    assert again_text == new_text2


def test_shrink_classifies_trivially(tmp_path):
    # NEW body is a strict prefix of OLD (a shrink). The end marker bounds the inner, so an
    # untouched@old target syncs (its whole inner, stale tail included, swapped for NEW) and
    # a target already holding NEW repins — no shorter/longer window to disambiguate.
    new_body = "## Shr\n\nKept rule.\n"
    old_body = "## Shr\n\nKept rule.\nRemoved one.\nRemoved two.\n"  # NEW == OLD[0:3]
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "shr.md").write_text(frag("shr", stamp.PENDING, new_body))
    partials = sync.discover_partials(d)
    sha_for = {partials["shr"].path: SHA_CONV}.get
    old_blob = frag("shr", stamp.PENDING, old_body)
    body_at = lambda sha, path: old_blob if (sha, path) == (SHA_OLD, partials["shr"].path) else None

    untouched = "# Doc\n\n" + frag("shr", SHA_OLD, old_body) + "## After\n"
    findings, new_text = sync.sync_target("t.md", untouched, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "shr")]
    assert "Removed one." not in new_text and "Removed two." not in new_text
    assert new_text == "# Doc\n\n" + frag("shr", SHA_CONV, new_body) + "## After\n"

    updated = "# Doc\n\n" + frag("shr", SHA_OLD, new_body) + "## After\n"
    findings2, new_text2 = sync.sync_target("t.md", updated, partials, sha_for, body_at)
    assert findings2 == [sync.SyncFinding("repinned", SHA_OLD, SHA_CONV, "t.md", "shr")]
    assert new_text2 == "# Doc\n\n" + frag("shr", SHA_CONV, new_body) + "## After\n"


# --- seed class: the same stale three-way, but body-blind at the canonical sha ---


def test_seed_untouched_stale_is_synced(partials, sha_for, body_at):
    # an untouched seed (still the old rendered body) at a stale sha syncs like a verbatim
    target = "# R\n\n" + frag("readme-lead", SHA_OLD, SEED_OLD_BODY)
    findings, new_text = sync.sync_target("r.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_SEED, "r.md", "readme-lead")]
    assert new_text == "# R\n\n" + frag("readme-lead", SHA_SEED, SEED_BODY)


def test_seed_customized_stale_is_skipped(partials, sha_for, body_at):
    # a customized seed at a stale sha -> skipped-edited, stamp deliberately NOT re-pinned
    # (re-pinning would falsely claim the custom body descends from the new sha)
    body = "## Readme Lead\n\nMy own custom intro.\n"
    target = "# R\n\n" + frag("readme-lead", SHA_OLD, body)
    findings, new_text = sync.sync_target("r.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_OLD, None, "r.md", "readme-lead")]
    assert new_text == target
    assert md_stamp("readme-lead", SHA_OLD) in new_text  # stamp still names the OLD sha


def test_seed_ok_at_current_sha_despite_custom_body(partials, sha_for, body_at):
    # at the canonical sha a seed is ok regardless of body (never body-checked, like drift)
    body = "## Readme Lead\n\nDivergent customized opener.\n"
    target = "# R\n\n" + frag("readme-lead", SHA_SEED, body)
    findings, new_text = sync.sync_target("r.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("ok", SHA_SEED, SHA_SEED, "r.md", "readme-lead")]
    assert new_text == target


# --- ordering: bottom-up application, and a nested inner envelope ---


def test_multiple_fragments_bottom_up(partials, sha_for, body_at):
    # two stale fragments that both grow (3 -> 5 lines): applying top-down would shift the
    # lower stamp's index out from under it, so edits must apply bottom-up
    target = "# Doc\n\n" + frag("conv", SHA_OLD, CONV_OLD_BODY) + "\n" + frag("vc", SHA_OLD, VC_OLD_BODY)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [
        sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv"),
        sync.SyncFinding("synced", SHA_OLD, SHA_VC, "t.md", "vc"),
    ]
    expected = "# Doc\n\n" + frag("conv", SHA_CONV, CONV_BODY) + "\n" + frag("vc", SHA_VC, VC_BODY)
    assert new_text == expected
    # a second pass is a total no-op: everything now classifies ok
    again, again_text = sync.sync_target("t.md", new_text, partials, sha_for, body_at)
    assert again_text == new_text
    assert all(f.status == "ok" for f in again)


def test_nested_envelope_inner_synced_outer_skipped(partials, sha_for, body_at):
    # a different-name inner envelope nested inside an outer one: find_end pairs the outer
    # begin to its OWN end marker (skipping the inner's), so the outer inner now differs
    # from both its stamped-from and current body -> skipped-edited (no edit). The inner
    # stale seed matches its own stamped-from -> synced, classified independently. The
    # outer's skip is why the nested inner is never clobbered.
    inner = frag("readme-lead", SHA_OLD, SEED_OLD_BODY)
    target = "# Doc\n\n" + frag("conv", SHA_OLD, "## Conventions\n" + inner)
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [
        sync.SyncFinding("skipped-edited", SHA_OLD, None, "t.md", "conv"),
        sync.SyncFinding("synced", SHA_OLD, SHA_SEED, "t.md", "readme-lead"),
    ]
    expected = "# Doc\n\n" + frag("conv", SHA_OLD, "## Conventions\n" + frag("readme-lead", SHA_SEED, SEED_BODY))
    assert new_text == expected


# --- dry-run / write / idempotence via main() + capsys + real tmp files ---


def _agents_doc(conv_sha: str, seed_sha: str) -> str:
    return "# Doc\n\n" + frag("conv", conv_sha, CONV_OLD_BODY) + "\n" + frag("readme-lead", seed_sha, SEED_OLD_BODY)


def test_dry_run_leaves_file_untouched_and_prints_tsv(tmp_path, partials_dir, sha_for, body_at, capsys):
    doc = tmp_path / "AGENTS.md"
    original = _agents_doc(SHA_OLD, SHA_OLD)  # both stale, both untouched -> would sync
    doc.write_text(original)
    rc = sync.main([doc], write=False, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    assert rc == 0
    assert doc.read_text() == original  # dry-run never writes
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(lines) == 2
    assert all(line.count("\t") == 4 for line in lines)  # five columns
    assert all(line.startswith("synced\t") for line in lines)


def test_write_applies_edits(tmp_path, partials_dir, sha_for, body_at):
    doc = tmp_path / "AGENTS.md"
    doc.write_text(_agents_doc(SHA_OLD, SHA_OLD))
    rc = sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    assert rc == 0
    text = doc.read_text()
    assert md_stamp("conv", SHA_CONV) in text
    assert md_stamp("readme-lead", SHA_SEED) in text
    assert "New first rule." in text and "Old first rule." not in text
    assert "New seed prose to customize." in text and "Old seed prose." not in text
    # both envelopes stay closed after the write
    assert md_end("conv") in text and md_end("readme-lead") in text


def test_second_write_is_all_noops(tmp_path, partials_dir, sha_for, body_at, capsys):
    doc = tmp_path / "AGENTS.md"
    doc.write_text(_agents_doc(SHA_OLD, SHA_OLD))
    sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    after_first = doc.read_text()
    capsys.readouterr()
    sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    assert doc.read_text() == after_first  # idempotent
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert lines and all(line.startswith("ok\t") for line in lines)


def test_crlf_file_preserves_line_endings(tmp_path, partials_dir, sha_for, body_at):
    # a CRLF target with one synced fragment stays CRLF throughout (main reads/writes with
    # newline="", sync_target rebuilds in the file's own ending) and is idempotent
    doc = tmp_path / "AGENTS.md"
    original = ("# Doc\n\n" + frag("conv", SHA_OLD, CONV_OLD_BODY)).replace("\n", "\r\n")
    doc.write_bytes(original.encode())
    sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    updated = doc.read_bytes().decode()
    assert "\r\n" in updated
    assert "\n" not in updated.replace("\r\n", "")  # no lone LF slipped in
    assert md_stamp("conv", SHA_CONV) in updated and "New first rule." in updated
    assert md_end("conv") in updated  # the envelope stays closed
    # a second write is a no-op and leaves the CRLF file byte-identical
    sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    assert doc.read_bytes().decode() == updated


def test_exit_zero_even_when_skipped(tmp_path, partials_dir, sha_for, body_at, capsys):
    doc = tmp_path / "AGENTS.md"
    # a customized verbatim fragment at the current sha -> skipped-edited, but exit stays 0
    doc.write_text("# Doc\n\n" + frag("conv", SHA_CONV, "## Conventions\n\nHeavily customized.\n"))
    rc = sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    assert rc == 0
    assert "skipped-edited\t" in capsys.readouterr().out
    assert "Heavily customized." in doc.read_text()  # the edited fragment is never overwritten


# --- shell target (whole-file; scanned only in non-.md targets) ---


def test_shell_ok_noop(partials, sha_for, body_at, shell_template):
    target = SHELL_BODY.replace(sh_stamp(stamp.PENDING), sh_stamp(SHA_SHELL))
    findings, new_text = sync.sync_target(
        "install-binary.sh", target, partials, sha_for, body_at, shell_template=shell_template
    )
    assert findings == [sync.SyncFinding("ok", SHA_SHELL, SHA_SHELL, "install-binary.sh", "install-binary.sh")]
    assert new_text == target


def test_shell_synced_replaces_whole_file(partials, sha_for, body_at, shell_template):
    # an unrendered byte copy at the OLD sha whose body matches the stamped-from template:
    # the whole file swaps to the current template with its stamp pinned to canonical
    target = SHELL_OLD_BODY.replace(sh_stamp(stamp.PENDING), sh_stamp(SHA_OLD))
    findings, new_text = sync.sync_target(
        "install-binary.sh", target, partials, sha_for, body_at, shell_template=shell_template
    )
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_SHELL, "install-binary.sh", "install-binary.sh")]
    assert new_text == SHELL_BODY.replace(sh_stamp(stamp.PENDING), sh_stamp(SHA_SHELL))


def test_shell_repinned_when_body_current(partials, sha_for, body_at, shell_template):
    # body already equals the current template, only the stamp trails -> re-pin only
    target = SHELL_BODY.replace(sh_stamp(stamp.PENDING), sh_stamp(SHA_OLD))
    findings, new_text = sync.sync_target(
        "install-binary.sh", target, partials, sha_for, body_at, shell_template=shell_template
    )
    assert findings == [sync.SyncFinding("repinned", SHA_OLD, SHA_SHELL, "install-binary.sh", "install-binary.sh")]
    assert new_text == SHELL_BODY.replace(sh_stamp(stamp.PENDING), sh_stamp(SHA_SHELL))


def test_shell_rendered_copy_is_skipped_edited(partials, sha_for, body_at, shell_template):
    # the honesty test: a rendered copy (tokens substituted away) matches neither the
    # current template nor the stamped-from blob -> skipped-edited, never overwritten
    rendered = "#!/bin/sh\n" + sh_stamp(SHA_OLD) + '\nset -eu\nNAME="demo-proj"\n'
    findings, new_text = sync.sync_target(
        "install-binary.sh", rendered, partials, sha_for, body_at, shell_template=shell_template
    )
    assert findings == [sync.SyncFinding("skipped-edited", SHA_OLD, None, "install-binary.sh", "install-binary.sh")]
    assert new_text == rendered


def test_shell_pending_skipped(partials, sha_for, body_at, shell_template):
    findings, new_text = sync.sync_target(
        "install-binary.sh", SHELL_BODY, partials, sha_for, body_at, shell_template=shell_template
    )
    assert findings == [
        sync.SyncFinding("pending", stamp.PENDING, None, "install-binary.sh", "install-binary.sh")
    ]
    assert new_text == SHELL_BODY


def test_shell_stamp_in_md_target_not_scanned(partials, sha_for, body_at, shell_template):
    # a doc quoting the shell-stamp syntax must not produce a shell finding; the same
    # content in a .sh file still is scanned (here it matches neither side -> skipped-edited)
    text = "# Doc\n\nRendered copies are stamped `" + sh_stamp(SHA_OLD) + "` on line 2.\n"
    md_findings, md_text = sync.sync_target(
        "notes.md", text, partials, sha_for, body_at, shell_template=shell_template
    )
    assert md_findings == []
    assert md_text == text
    sh_findings, _ = sync.sync_target("notes.sh", text, partials, sha_for, body_at, shell_template=shell_template)
    assert sh_findings == [
        sync.SyncFinding("skipped-edited", SHA_OLD, None, "notes.sh", "install-binary.sh")
    ]


# --- production body resolver against a real git repo ---


def test_git_body_at_real_repo(tmp_path):
    root = tmp_path / "repo"
    sub = root / "templates" / "_partials"
    sub.mkdir(parents=True)
    partial = sub / "conv.md"

    def git(*args):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            check=True,
            capture_output=True,
        )

    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    partial.write_text("v1 body\n")
    git("add", "-A")
    git("commit", "-m", "v1")
    sha_v1 = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    partial.write_text("v2 body\n")
    git("add", "-A")
    git("commit", "-m", "v2")

    # the blob as of v1 is retrieved verbatim, even though the working tree now holds v2
    assert sync.git_body_at(sha_v1, partial, root=root) == "v1 body\n"
    # an unknown sha -> None (git show fails)
    assert sync.git_body_at("0" * 40, partial, root=root) is None
    # a path outside the root is refused before any git call
    assert sync.git_body_at(sha_v1, tmp_path / "outside.md", root=root) is None
