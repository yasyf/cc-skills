from __future__ import annotations

from datetime import UTC, datetime

import progress
import standing

FIRST = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
LATER = datetime(2026, 10, 5, 12, 5, tzinfo=UTC)
HISTORY = "`ccn doc history 91b9194 --json --full`"

REGISTER_825 = """## 8:25 PM PT SIMPLIFICATION DECISIONS REGISTER (owner-verified; keep this section byte-for-byte across compactions)

1. KEEP the preview gate. Plan 46148d5 is the executable copy.
2. DELETE the reseed tooling.
"""

RULINGS_921 = """### Owner rulings tonight, verbatim (8:47 PM - 9:2x PM PT Oct 4). These bind every lane.
- 8:47 PM: "fix the underlying issue then get rid of the check"
- 9:01 PM: "there are no holds on deploys right now so stop making that up"
"""

RULINGS_1017 = """### Owner rulings since 9:2x PM (verbatim, binding)
- 10:01 PM: "every merged PR releases at once"
"""

DUMP_4 = """## 1:13 AM PT Oct 5 pre-compact dump 4 (READ THIS SECTION FIRST after any compaction)

### Resume protocol (unchanged from dump 3, restated)
Read the register, then this section.

### Owner rulings since dump 3 (verbatim, in order)
- 12:40 AM: "roll HSBC back now"

### HSBC incident (hsbc-routing-0031): state of record
Routing reverted at 12:52 AM. Forward fix in #30612.

### 1:35 AM PT addendum to dump 4 (read with dump 4 above)
Queue drained. Census 251/293.
"""

TONIGHT = (
    "# release-v3: progress 2026-10-05T0058Z (generated)\n\n"
    "Generated from sources by the long-running compaction hook at 2026-10-05T00:58:00Z.\n\n"
    "## 2:38 PM PT (real clock) pre-compact delta — read this section first after compaction\n"
    "Owner state at 2:38 PM. Platy full-platform deploy owned by slack-release-sweep-6.\n\n"
    "## 6:00 PM PT (real clock) pre-compact delta\n\n"
    "### Owner asks since 3:08 PM PT, verbatim where it matters, with state\n"
    "- 3:08 PM: why is HSBC so much work. Answered.\n\n"
    "### Lanes live at 6:00 PM (owner → next)\n"
    "- release-simplify: card 19.\n\n"
    f"{REGISTER_825}\n"
    "## 9:2x PM PT pre-compact delta + STANDING RULINGS (verbatim; read this section FIRST)\n\n"
    f"{RULINGS_921}\n"
    "### Program state at 9:2x PM PT\n"
    "Census 224/293 at 09e61e6. Releases R41 and R42 walking.\n\n"
    "## 10:17 PM PT pre-compact dump 2 (READ THIS SECTION FIRST; the 30-rule register is doc 0cf17c9)\n\n"
    "### Resume protocol\n"
    "Plan file first, then this dump.\n\n"
    f"{RULINGS_1017}\n"
    "### Program state\n"
    "Census 231/293. Lanes: valkey-fold, tenant-one-pr.\n\n"
    f"{DUMP_4}"
)


def test_the_newest_dump_stays_whole_and_each_binding_section_is_carried_once_verbatim() -> None:
    folded = progress.fold(TONIGHT, FIRST, HISTORY)

    assert folded.endswith(DUMP_4.strip("\n"))
    for binding in (REGISTER_825.split("\n", 1)[1], RULINGS_921, RULINGS_1017):
        assert folded.count(binding.strip("\n")) == 1
    assert "### 8:25 PM PT SIMPLIFICATION DECISIONS REGISTER" in folded
    assert folded.index(progress.CARRIED) < folded.index(progress.FOLDED) < folded.index("## 1:13 AM PT Oct 5 pre-compact dump 4")
    assert "# release-v3: progress 2026-10-05T0058Z" not in folded
    for gone in ("Releases R41 and R42 walking", "Lanes: valkey-fold, tenant-one-pr", "Answered."):
        assert gone not in folded


def test_each_earlier_dump_folds_into_one_dated_line_naming_its_parts() -> None:
    folded = progress.fold(TONIGHT, FIRST, HISTORY)
    lines = folded.split(f"{progress.FOLDED}\n\n", 1)[1].split("\n\n", 2)

    assert lines[0] == f"Full text of each folded dump: {HISTORY}."
    digest = lines[1].splitlines()
    assert len(digest) == 5
    assert all(line.startswith("- 2026-10-05 09:30Z, ") and len(line) <= progress.DIGEST_CHARS for line in digest)
    assert digest[0] == "- 2026-10-05 09:30Z, untitled narrative: Generated from sources by the long-running compaction hook at 2026-10-05T00:58:00Z."
    assert digest[3] == (
        "- 2026-10-05 09:30Z, 9:2x PM PT pre-compact delta + STANDING RULINGS: "
        "Program state at 9:2x PM PT: Census 224/293 at 09e61e6."
    )


def test_folding_twice_changes_nothing() -> None:
    once = progress.fold(TONIGHT, FIRST, HISTORY)

    assert progress.fold(once, LATER, HISTORY) == once


def test_a_new_dump_replaces_the_last_instead_of_appending() -> None:
    once = progress.fold(TONIGHT, FIRST, HISTORY)
    dump_5 = (
        "## 4:40 AM PT pre-compact dump 5 (READ THIS SECTION FIRST)\n\n"
        "### Owner rulings since dump 4 (verbatim, in order)\n- 4:02 AM: \"fold the doc\"\n\n"
        "### Program state\nCensus 270/293.\n\n"
        f"{RULINGS_1017}"
    )

    twice = progress.fold(f"{once}\n\n{dump_5}", LATER, HISTORY)

    assert twice.endswith(dump_5.strip("\n"))
    assert twice.count(RULINGS_1017.strip("\n")) == 1
    assert twice.count('- 12:40 AM: "roll HSBC back now"') == 1
    assert "Census 251/293" not in twice
    assert "Forward fix in #30612" not in twice
    assert twice.count("\n- 2026-10-05 ") == 6
    assert "\n- 2026-10-05 12:05Z, 1:13 AM PT Oct 5 pre-compact dump 4: Resume protocol: Read the register, then this section.; HSBC incident" in twice


def test_a_narrative_without_sections_is_left_alone() -> None:
    assert progress.fold("land l11, then watch SoFi\n", FIRST, HISTORY) == "land l11, then watch SoFi"


def test_a_record_over_the_cap_names_its_largest_section_and_part() -> None:
    record = f"# r\n\n## Open tasks\n- #1 x\n\n## Root narrative\n\n## dump\n\n### small\n{'s' * 100}\n\n### huge\n{'h' * progress.CAP}\n"

    refusal = progress.oversized(record)

    assert refusal is not None
    assert refusal.startswith(f"progress record is {len(record.encode())} bytes, over the {progress.CAP}-byte cap: its largest section is `dump`")
    assert f"most of it `huge` at {progress.CAP + len('### huge\n')} bytes" in refusal
    assert progress.oversized(progress.fold(TONIGHT, FIRST, HISTORY)) is None


def test_headings_inside_a_fence_never_split_a_dump() -> None:
    dump = "## dump 5\n\n### Program state\n```md\n## Example heading\n### not a part\n```\nCensus 280/293.\n"

    assert progress.fold(f"## dump 4\n\n### Program state\nold one. old two.\n\n{dump}", FIRST, HISTORY).endswith(dump.strip("\n"))


def test_unheaded_text_stays_current_across_folds() -> None:
    once = progress.fold(f"{progress.FOLDED}\n\nFull text of each folded dump: x.\n\n- 2026-10-05 09:30Z, dump 2: a\n", FIRST, HISTORY)
    once = progress.fold(f"land l11 next\n\n{once}", FIRST, HISTORY)

    assert progress.fold(once, LATER, HISTORY) == once
    assert once.startswith("land l11 next")


def test_an_oversized_record_without_sections_still_names_its_text() -> None:
    refusal = progress.oversized("x" * (progress.CAP + 1))

    assert refusal is not None and "`text before the first section`" in refusal


def test_a_digest_line_is_never_an_uncited_owner_gate() -> None:
    line = "- 2026-10-05 09:30Z, dump 2: Program state: Owner approval is no longer needed."

    assert standing.lint(f"## Standing owner rules\n\n## Root narrative\n\n{progress.FOLDED}\n\n{line}\n", None, None, set()) == []
