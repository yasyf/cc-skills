from __future__ import annotations

from urllib.parse import urlencode

from livedash import Col, Context, Table, component

BUILDKITE_TONE = {"passed": "ok", "failed": "bad", "failing": "bad", "canceled": "muted", "running": "warn", "scheduled": "warn", "blocked": "warn"}
ACTIONS_TONE = {"success": "ok", "failure": "bad", "cancelled": "muted", "in_progress": "warn", "queued": "warn"}


@component("ci-builds", "CI builds", every="1m", timeout="60s")
def ci_builds(ctx: Context, *, source: str = "buildkite", pipeline: str | None = None, repo: str | None = None, branch: str | None = None, limit: int = 20) -> Table:
    """Recent builds from Buildkite (`bk api`, by `pipeline`) or GitHub Actions (`gh run list`, by `repo`), on `branch` when set."""
    if source == "buildkite":
        if not pipeline:
            raise ValueError("a Buildkite source needs `pipeline`")
        query = urlencode({"per_page": limit} | ({"branch": branch} if branch else {}))
        builds = ctx.json(["bk", "api", f"/pipelines/{pipeline}/builds?{query}"])
        rows = [
            {"key": str(build["number"]), "build": f"#{build['number']}", "build_url": build["web_url"], "state": build["state"], "branch": build.get("branch"), "message": (build.get("message") or "").splitlines()[0] if build.get("message") else "", "at": build.get("created_at"), "tone": BUILDKITE_TONE.get(build["state"])}
            for build in builds
        ]
    elif source == "github":
        if not repo:
            raise ValueError("a GitHub source needs `repo`")
        runs = ctx.json(["gh", "run", "list", "-R", repo, "--limit", str(limit), "--json", "databaseId,displayTitle,status,conclusion,headBranch,createdAt,url,workflowName", *(["--branch", branch] if branch else [])])
        rows = []
        for run in runs:
            state = run["conclusion"] or run["status"]
            rows.append({"key": str(run["databaseId"]), "build": run["workflowName"], "build_url": run["url"], "state": state, "branch": run["headBranch"], "message": run["displayTitle"], "at": run["createdAt"], "tone": ACTIONS_TONE.get(state)})
    else:
        raise ValueError(f"source must be buildkite or github, not {source!r}")
    return Table([Col("build", "Build", "link"), Col("state", "State", "badge"), Col("branch", "Branch"), Col("message", "Message"), Col("at", "Started", "age")], rows, note=None if rows else "No builds.")
