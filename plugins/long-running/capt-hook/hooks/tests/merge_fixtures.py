from __future__ import annotations

import json

URL = "https://github.com/acme/app/pull/5"
OWN_FILES = ("lr/internal/lanes/sources.go", "lr/internal/lanes/sources_test.go")
WORKFLOW_ANNOTATION = (".github", 140, "Process completed with exit code 1.")


def check_run(name: str, *annotations: tuple[str, int, str], status: str = "COMPLETED", conclusion: str | None = "FAILURE") -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "databaseId": 7,
        "annotations": {"nodes": [{"path": path, "location": {"start": {"line": line}}, "message": message} for path, line, message in annotations]},
    }


def checks_read(*runs: dict, log: str = "") -> dict[str, str]:
    suite = {"app": {"slug": "github-actions"}, "checkRuns": {"nodes": list(runs)}}
    pr = {"files": {"nodes": [{"path": path} for path in OWN_FILES]}, "commits": {"nodes": [{"commit": {"checkSuites": {"nodes": [suite]}}}]}}
    return {"gh pr view": f"{URL}\n", "gh api graphql": json.dumps({"data": {"resource": pr}}), "gh api --help": "--allow-escape-sequences\n", "gh api repos/acme/app/actions/jobs/7/logs": log}


OWN_LINT = checks_read(check_run("lr-go", ("lr/internal/lanes/sources.go", 336, "Consider preallocating messages with capacity 1 (prealloc)")))
INHERITED_LINT = checks_read(check_run("lr-go", ("lr/internal/asks/command.go", 12, "QF1008: could remove embedded field (staticcheck)")))
OWN_TEST = checks_read(
    check_run("lr-go", WORKFLOW_ANNOTATION),
    log="2026-11-03T04:10:55.1971800Z ok  \tlr/internal/asks\n2026-11-03T04:10:55.1980294Z \x1b[31m    sources_test.go:42: got 2 messages, want 1\x1b[0m\n",
)
INHERITED_TEST = checks_read(check_run("lr-go", WORKFLOW_ANNOTATION), log="2026-11-03T04:10:55.1980294Z     command_test.go:9: want ask line\n")
PENDING = checks_read(check_run("lr-go", ("lr/internal/lanes/sources.go", 336, "prealloc"), status="IN_PROGRESS", conclusion=None))
GREEN = checks_read(check_run("lr-go", conclusion="SUCCESS"))
