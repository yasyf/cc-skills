from __future__ import annotations

import progress


def test_a_record_over_the_cap_names_its_largest_section_and_part() -> None:
    record = f"# r\n\n## Open tasks\n- #1 x\n\n## Open owner asks\n\n### small\n{'s' * 100}\n\n### huge\n{'h' * progress.CAP}\n"

    refusal = progress.oversized(record)

    assert refusal is not None
    assert refusal.startswith(
        f"progress record is {len(record.encode())} bytes, over the {progress.CAP}-byte cap: its largest section is `Open owner asks`"
    )
    assert f"most of it `huge` at {progress.CAP + len('### huge\n')} bytes" in refusal
    assert progress.oversized(record[: progress.CAP]) is None


def test_headings_inside_a_fence_never_split_a_section() -> None:
    record = f"## Inboxes\n\n```md\n## Example heading\n```\n{'i' * progress.CAP}\n\n## Lint findings\n- none\n"

    refusal = progress.oversized(record)

    assert refusal is not None and "its largest section is `Inboxes`" in refusal


def test_an_oversized_record_without_sections_still_names_its_text() -> None:
    refusal = progress.oversized("x" * (progress.CAP + 1))

    assert refusal is not None and "`text before the first section`" in refusal
