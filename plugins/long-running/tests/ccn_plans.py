from __future__ import annotations

import json

PROGRAM = "brook"
PLAN_ID = "fc8714e" + "0" * 33
OTHER_PLAN_ID = "01a07ac" + "0" * 33
PLAN_BODY = "# Migration\n\n## Approach\n### 1. Readers first\nbody\n### 2. Delete the old store\nbody\n#### 3. Not a step\n## Appendix\n### 1. Readers first, again\n"


def task(ident: str, title: str, *, status: str = "open", labels: tuple[str, ...] = (f"program:{PROGRAM}",), plan: str | None = None, **fields) -> dict:
    return {"id": ident * 40, "title": title, "type": "task", "status": status, "priority": 2, "labels": list(labels), "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z", **({"plan": plan} if plan else {}), **fields}


class CcnPlans:
    def __init__(self, tasks: list[dict], plans: list[dict] | None = None) -> None:
        self.tasks = {record["id"]: record for record in tasks}
        self.plans = {record["id"]: record for record in plans or []}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *argv: str) -> str:
        self.calls.append(argv)
        labels = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--label"]
        if argv[:2] == ("task", "list"):
            keys = ("id", "title", "type", "status", "priority", "branch", "assignee", "plan", "blocked_by", "updated_at")
            return json.dumps([{key: record[key] for key in keys if key in record} for record in self.tasks.values() if set(labels) <= set(record.get("labels") or [])])
        if argv[:2] == ("task", "show"):
            return json.dumps(self.tasks[argv[2]])
        if argv[:2] == ("plan", "list"):
            return json.dumps([{key: plan[key] for key in ("id", "title", "status", "updated_at")} for plan in self.plans.values() if set(labels) <= set(plan["labels"]) and plan["status"] in ("draft", "approved", "executing")])
        if argv[:2] == ("plan", "show"):
            return json.dumps(next(plan for ident, plan in self.plans.items() if ident.startswith(argv[2])))
        raise AssertionError(f"unexpected ccn call: {argv}")


def plan(ident: str = PLAN_ID, title: str = "Migration", body: str = PLAN_BODY, labels: tuple[str, ...] = (f"program:{PROGRAM}",), status: str = "draft") -> dict:
    return {"id": ident, "title": title, "body": body, "status": status, "labels": list(labels), "updated_at": "2026-10-01T00:00:00Z"}
