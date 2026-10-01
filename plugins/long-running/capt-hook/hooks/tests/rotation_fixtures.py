from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "rotation"
ROOT = FIXTURES / "projects" / "p" / "root.jsonl"
SLEEPY = {"id": "t-sleepy", "type": "teammate", "status": "running", "description": "Sleepy lane"}
POLLER = {"id": "t-poller", "type": "teammate", "status": "running", "description": "PR poller"}
REVIEWER = {"id": "areviewer-3c3c3c3c3c3c3c3c", "type": "subagent", "status": "running", "description": "Reviewer"}
