from datetime import UTC, datetime
from pathlib import Path

MIGRATED: dict[str, dict] = {}
MIGRATED_AT: dict[str, datetime] = {}


def rulings_prefix(command: str) -> bool:
    argv = command.split()
    return len(argv) > 1 and Path(argv[1]).name == "rulings.py"


def separate(name: str, cases: dict) -> dict:
    migrated = {inp: expected for inp, expected in cases.items() if any(rulings_prefix(command) for command in (getattr(inp, "commands", None) or {}))}
    if migrated:
        assert name not in MIGRATED
        MIGRATED[name] = migrated
        MIGRATED_AT[name] = datetime.now(UTC)
    return {inp: expected for inp, expected in cases.items() if inp not in migrated}
