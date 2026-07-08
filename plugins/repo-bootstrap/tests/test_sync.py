"""The sync updater: mechanically move stamped fragments to their canonical body.

Fixtures mirror test_drift.py — current partials written ``@pending`` fed through
``discover_partials``, fake 40-char shas, and dict-closure resolvers injected as
callables (never a mocked subprocess for the pure-logic tests). ``CONV_OLD_BODY`` (3
lines) vs ``CONV_BODY`` (5 lines) and the matching VC pair prove the window is measured
from the ORIGINAL body: were the current length used, the wrong span would be excised.
Each blob returned by ``body_at`` leads with its own ``@pending`` stamp, exactly as a
committed template does, so ``_blob_*_body`` has a stamp to strip. Only
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

# conv: old 3 body lines, current 5 — different lengths, and line 3 differs so the old
# body is never a prefix of the current one (which would make the window ambiguous).
CONV_OLD_BODY = "## Conventions\n\nOld first rule.\n"
CONV_BODY = "## Conventions\n\nNew first rule.\nNew second rule.\nNew third rule.\n"
# vc: heading-less, old 3 lines, current 5 — the second fragment for the ordering tests.
VC_OLD_BODY = "**Version control.** Old rule.\n\n**Watch CI.** Old CI rule.\n"
VC_BODY = "**Version control.** New rule.\n\n**Watch CI.** New CI rule.\n\n**Extra.** Added line.\n"
# readme-lead: a seed, old and current bodies of equal length (the seed distinction lives
# at the canonical sha, not in the window arithmetic).
SEED_OLD_BODY = "## Readme Lead\n\nOld seed prose.\n"
SEED_BODY = "## Readme Lead\n\nNew seed prose to customize.\n"

SHELL_BODY = "#!/bin/sh\n" + "# canonical: " + stamp.CANONICAL + "@" + stamp.PENDING + "\nset -eu\necho new\n"
SHELL_OLD_BODY = "#!/bin/sh\n" + "# canonical: " + stamp.CANONICAL + "@" + stamp.PENDING + "\nset -eu\necho old\n"


def md_stamp(name: str, sha: str) -> str:
    return f"<!-- canonical: {stamp.CANONICAL}/_partials/{name}.md@{sha} -->"


def sh_stamp(sha: str) -> str:
    return f"# canonical: {stamp.CANONICAL}@{sha}"


# --- fixtures ---


@pytest.fixture
def partials_dir(tmp_path):
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "conv.md").write_text(md_stamp("conv", stamp.PENDING) + "\n" + CONV_BODY)
    (d / "vc.md").write_text(md_stamp("vc", stamp.PENDING) + "\n" + VC_BODY)
    (d / "readme-lead.md").write_text(md_stamp("readme-lead", stamp.PENDING) + "\n" + SEED_BODY)
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
    # committed blobs at the OLD sha — each leads with its own @pending stamp
    blobs = {
        (SHA_OLD, partials["conv"].path): md_stamp("conv", stamp.PENDING) + "\n" + CONV_OLD_BODY,
        (SHA_OLD, partials["vc"].path): md_stamp("vc", stamp.PENDING) + "\n" + VC_OLD_BODY,
        (SHA_OLD, partials["readme-lead"].path): md_stamp("readme-lead", stamp.PENDING) + "\n" + SEED_OLD_BODY,
        (SHA_OLD, shell_template): SHELL_OLD_BODY,
    }
    return lambda sha, path: blobs.get((sha, path))


# --- markdown three-way: synced / repinned / skipped-edited / ok ---


def test_synced_replaces_window_and_repins(partials, sha_for, body_at):
    # a stale fragment still holding the body it was stamped from -> body swapped for the
    # current one and the stamp re-pinned to canonical
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert new_text == "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY


def test_synced_window_length_comes_from_original(partials, sha_for, body_at):
    # a trailing section after the OLD 3-line body must survive the sync: were the window
    # measured from the 5-line CURRENT body, two trailing lines would be swept in and the
    # fragment would (wrongly) mismatch
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY + "## Keep\n\nkeep me.\n"
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert new_text == "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY + "## Keep\n\nkeep me.\n"


def test_synced_window_includes_trailing_blank_line(partials, sha_for):
    # an original body ENDING with a blank line: the excision window must span that
    # trailing empty line, or the stale blank lingers as residue after the replacement.
    # (A string round-trip of the body would drop the trailing element and undercount.)
    old_body = "## Conventions\n\nOld rule.\n\n"
    blob = md_stamp("conv", stamp.PENDING) + "\n" + old_body
    body_at = lambda sha, path: blob if (sha, path) == (SHA_OLD, partials["conv"].path) else None
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + old_body + "## Next\n"
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert "Old rule." not in new_text
    # no leftover blank line between the new body and "## Next"
    assert new_text == "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY + "## Next\n"


def test_repinned_rewrites_stamp_line_only(partials, sha_for, body_at):
    # the body already equals the current one, only the stamp trails -> re-pin the stamp
    # line alone, body untouched
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_BODY
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("repinned", SHA_OLD, SHA_CONV, "t.md", "conv")]
    assert new_text == "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY


def test_skipped_edited_never_overwrites(partials, sha_for, body_at):
    # diverged from BOTH the stamped-from and the current body -> a local decision, left
    # untouched, stamp NOT re-pinned
    body = "## Conventions\n\nA custom rule nobody shipped.\n"
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + body
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target


def test_edited_at_current_sha_is_skipped(partials, sha_for, body_at):
    # a verbatim fragment at the CURRENT sha but edited body -> nothing newer to sync to
    body = "## Conventions\n\nLocally edited.\n"
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + body
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_CONV, None, "t.md", "conv")]
    assert new_text == target


def test_ok_noop(partials, sha_for, body_at):
    # at the canonical sha with a matching body -> ok, nothing rewritten
    target = "# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("ok", SHA_CONV, SHA_CONV, "t.md", "conv")]
    assert new_text == target


def test_unknown_stamp_skipped(partials, sha_for, body_at):
    # a stamp naming no shipped partial -> unknown, never rewritten
    target = "# Doc\n\n" + md_stamp("ghost", SHA_CONV) + "\n## Ghost\n\nbody\n"
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("unknown", SHA_CONV, None, "t.md", "ghost")]
    assert new_text == target


def test_pending_stamp_skipped(partials, sha_for, body_at):
    # an unpinned @pending stamp (the pin never ran) -> pending, never rewritten
    target = "# Doc\n\n" + md_stamp("conv", stamp.PENDING) + "\n" + CONV_BODY
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("pending", stamp.PENDING, None, "t.md", "conv")]
    assert new_text == target


def test_no_history_when_blob_unresolvable(partials, sha_for):
    # stale sha, but the blob at that sha can't be recovered -> no-history
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, lambda sha, path: None)
    assert findings == [sync.SyncFinding("no-history", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target


def test_no_history_when_canonical_unavailable(partials, body_at):
    # canonical sha unavailable (installed plugin cache) -> no-history before any blob lookup
    target = "# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY
    findings, new_text = sync.sync_target("t.md", target, partials, lambda path: None, body_at)
    assert findings == [sync.SyncFinding("no-history", SHA_OLD, None, "t.md", "conv")]
    assert new_text == target


# --- prefix ambiguity between synced and repinned (longest-match disambiguation) ---


def test_synced_vs_repinned_prefix_extension(tmp_path):
    # OLD body is a STRICT PREFIX of NEW (two lines appended). A target already holding NEW
    # at a stale stamp must classify repinned (bump the stamp only) — classifying synced
    # would re-splice the current body over the prefix and DUPLICATE the appended lines. An
    # untouched@old sibling still classifies synced.
    old_body = "## Ext\n\nBase rule.\n"
    new_body = "## Ext\n\nBase rule.\nAppended one.\nAppended two.\n"  # OLD == NEW[0:3]
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "ext.md").write_text(md_stamp("ext", stamp.PENDING) + "\n" + new_body)
    partials = sync.discover_partials(d)
    sha_for = {partials["ext"].path: SHA_CONV}.get
    old_blob = md_stamp("ext", stamp.PENDING) + "\n" + old_body
    body_at = lambda sha, path: old_blob if (sha, path) == (SHA_OLD, partials["ext"].path) else None

    updated = "# Doc\n\n" + md_stamp("ext", SHA_OLD) + "\n" + new_body
    findings, new_text = sync.sync_target("t.md", updated, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("repinned", SHA_OLD, SHA_CONV, "t.md", "ext")]
    assert new_text == "# Doc\n\n" + md_stamp("ext", SHA_CONV) + "\n" + new_body
    assert new_text.count("Appended one.") == 1  # not duplicated

    untouched = "# Doc\n\n" + md_stamp("ext", SHA_OLD) + "\n" + old_body
    findings2, new_text2 = sync.sync_target("t.md", untouched, partials, sha_for, body_at)
    assert findings2 == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "ext")]
    assert new_text2 == "# Doc\n\n" + md_stamp("ext", SHA_CONV) + "\n" + new_body


def test_synced_vs_repinned_suffix_shrink(tmp_path):
    # NEW body is a STRICT PREFIX of OLD (trailing lines removed). An untouched@old target
    # must classify synced and excise the stale tail; a naive repinned-first order would
    # misfire here because the shorter current window matches the OLD body's prefix. A
    # target already holding NEW classifies repinned.
    new_body = "## Shr\n\nKept rule.\n"
    old_body = "## Shr\n\nKept rule.\nRemoved one.\nRemoved two.\n"  # NEW == OLD[0:3]
    d = tmp_path / "_partials"
    d.mkdir()
    (d / "shr.md").write_text(md_stamp("shr", stamp.PENDING) + "\n" + new_body)
    partials = sync.discover_partials(d)
    sha_for = {partials["shr"].path: SHA_CONV}.get
    old_blob = md_stamp("shr", stamp.PENDING) + "\n" + old_body
    body_at = lambda sha, path: old_blob if (sha, path) == (SHA_OLD, partials["shr"].path) else None

    untouched = "# Doc\n\n" + md_stamp("shr", SHA_OLD) + "\n" + old_body + "## After\n"
    findings, new_text = sync.sync_target("t.md", untouched, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "shr")]
    assert "Removed one." not in new_text and "Removed two." not in new_text
    assert new_text == "# Doc\n\n" + md_stamp("shr", SHA_CONV) + "\n" + new_body + "## After\n"

    updated = "# Doc\n\n" + md_stamp("shr", SHA_OLD) + "\n" + new_body + "## After\n"
    findings2, new_text2 = sync.sync_target("t.md", updated, partials, sha_for, body_at)
    assert findings2 == [sync.SyncFinding("repinned", SHA_OLD, SHA_CONV, "t.md", "shr")]
    assert new_text2 == "# Doc\n\n" + md_stamp("shr", SHA_CONV) + "\n" + new_body + "## After\n"


# --- seed class: the same stale three-way, but body-blind at the canonical sha ---


def test_seed_untouched_stale_is_synced(partials, sha_for, body_at):
    # an untouched seed (still the old rendered body) at a stale sha syncs like a verbatim
    target = "# R\n\n" + md_stamp("readme-lead", SHA_OLD) + "\n" + SEED_OLD_BODY
    findings, new_text = sync.sync_target("r.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("synced", SHA_OLD, SHA_SEED, "r.md", "readme-lead")]
    assert new_text == "# R\n\n" + md_stamp("readme-lead", SHA_SEED) + "\n" + SEED_BODY


def test_seed_customized_stale_is_skipped(partials, sha_for, body_at):
    # a customized seed at a stale sha -> skipped-edited, stamp deliberately NOT re-pinned
    # (re-pinning would falsely claim the custom body descends from the new sha)
    body = "## Readme Lead\n\nMy own custom intro.\n"
    target = "# R\n\n" + md_stamp("readme-lead", SHA_OLD) + "\n" + body
    findings, new_text = sync.sync_target("r.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("skipped-edited", SHA_OLD, None, "r.md", "readme-lead")]
    assert new_text == target
    assert md_stamp("readme-lead", SHA_OLD) in new_text  # stamp still names the OLD sha


def test_seed_ok_at_current_sha_despite_custom_body(partials, sha_for, body_at):
    # at the canonical sha a seed is ok regardless of body (never body-checked, like drift)
    body = "## Readme Lead\n\nDivergent customized opener.\n"
    target = "# R\n\n" + md_stamp("readme-lead", SHA_SEED) + "\n" + body
    findings, new_text = sync.sync_target("r.md", target, partials, sha_for, body_at)
    assert findings == [sync.SyncFinding("ok", SHA_SEED, SHA_SEED, "r.md", "readme-lead")]
    assert new_text == target


# --- ordering: bottom-up application, and a spliced inner stamp ---


def test_multiple_fragments_window_shift_bottom_up(partials, sha_for, body_at):
    # two stale fragments that both grow (3 -> 5 lines): applying top-down would shift the
    # lower stamp's index out from under it, so edits must apply bottom-up
    target = (
        "# Doc\n\n"
        + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY + "\n"
        + md_stamp("vc", SHA_OLD) + "\n" + VC_OLD_BODY
    )
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [
        sync.SyncFinding("synced", SHA_OLD, SHA_CONV, "t.md", "conv"),
        sync.SyncFinding("synced", SHA_OLD, SHA_VC, "t.md", "vc"),
    ]
    expected = (
        "# Doc\n\n"
        + md_stamp("conv", SHA_CONV) + "\n" + CONV_BODY + "\n"
        + md_stamp("vc", SHA_VC) + "\n" + VC_BODY
    )
    assert new_text == expected
    # a second pass is a total no-op: everything now classifies ok
    again, again_text = sync.sync_target("t.md", new_text, partials, sha_for, body_at)
    assert again_text == new_text
    assert all(f.status == "ok" for f in again)


def test_spliced_stamp_inner_synced_outer_skipped(partials, sha_for, body_at):
    # an inner stamp spliced into an outer fragment: the outer body now differs from both
    # its stamped-from and current body -> skipped-edited (no edit); the inner stale seed
    # still matches its own stamped-from body -> synced. The outer's skip is why the
    # spliced inner never gets clobbered.
    target = (
        "# Doc\n\n"
        + md_stamp("conv", SHA_OLD) + "\n"
        + "## Conventions\n"
        + md_stamp("readme-lead", SHA_OLD) + "\n"
        + SEED_OLD_BODY
    )
    findings, new_text = sync.sync_target("t.md", target, partials, sha_for, body_at)
    assert findings == [
        sync.SyncFinding("skipped-edited", SHA_OLD, None, "t.md", "conv"),
        sync.SyncFinding("synced", SHA_OLD, SHA_SEED, "t.md", "readme-lead"),
    ]
    expected = (
        "# Doc\n\n"
        + md_stamp("conv", SHA_OLD) + "\n"  # outer stamp untouched
        + "## Conventions\n"
        + md_stamp("readme-lead", SHA_SEED) + "\n"  # inner re-pinned
        + SEED_BODY
    )
    assert new_text == expected


# --- dry-run / write / idempotence via main() + capsys + real tmp files ---


def _agents_doc(conv_sha: str, seed_sha: str) -> str:
    return (
        "# Doc\n\n"
        + md_stamp("conv", conv_sha) + "\n" + CONV_OLD_BODY + "\n"
        + md_stamp("readme-lead", seed_sha) + "\n" + SEED_OLD_BODY
    )


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
    original = ("# Doc\n\n" + md_stamp("conv", SHA_OLD) + "\n" + CONV_OLD_BODY).replace("\n", "\r\n")
    doc.write_bytes(original.encode())
    sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    updated = doc.read_bytes().decode()
    assert "\r\n" in updated
    assert "\n" not in updated.replace("\r\n", "")  # no lone LF slipped in
    assert md_stamp("conv", SHA_CONV) in updated and "New first rule." in updated
    # a second write is a no-op and leaves the CRLF file byte-identical
    sync.main([doc], write=True, partials_dir=partials_dir, sha_for=sha_for, body_at=body_at)
    assert doc.read_bytes().decode() == updated


def test_exit_zero_even_when_skipped(tmp_path, partials_dir, sha_for, body_at, capsys):
    doc = tmp_path / "AGENTS.md"
    # a customized verbatim fragment at the current sha -> skipped-edited, but exit stays 0
    doc.write_text("# Doc\n\n" + md_stamp("conv", SHA_CONV) + "\n## Conventions\n\nHeavily customized.\n")
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
