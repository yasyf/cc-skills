from __future__ import annotations

from ..compaction_handoff import CompactionState

LONG = "line\n" * 400
ACTIVE = [CompactionState(active=True, slug="brook")]
