from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "SKILL.md"
CEILING = 425


def test_the_skill_stays_at_or_under_its_ceiling():
    lines = len(SKILL.read_text().splitlines())
    assert lines <= CEILING, (
        f"SKILL.md has {lines} lines, over its ceiling of {CEILING}: move an older line to a reference page "
        "for each line you add, and lower CEILING in this file whenever a cut shortens the skill"
    )
