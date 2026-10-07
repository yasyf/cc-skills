from __future__ import annotations

import subprocess
import threading
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from livedash import Col, Context, Entry, Feed, Line, Series, Table, Tile, Tiles, component, view
from livedash.components import cci
from livedash.components import ledger as ledgers
from livedash.components.release import overview, platy

BUILDS_FILE = "builds.json"
BUILDS_INTERVAL = timedelta(minutes=1)
INCIDENT_WINDOW = timedelta(hours=72)
CENSUS_WINDOW = timedelta(hours=48)
LANDED_WINDOW = timedelta(hours=26)
LANE_WINDOW = timedelta(hours=48)
CLOSING_KINDS = ["done", "lift", "go", "decision", "owner", "answer"]
WORK_KINDS = ["go", "opened", "updated", "claim", "ready", "landed", "released", "fix-live"]
CCI_OPEN = ("open_defects", "open_blockers", "open_holds")
DEPLOYABLE_TONE = {platy.PROVEN: "ok", platy.UNPROVEN: "warn", platy.BLOCKED: "bad"}
STATE_TONE = {"passed": "ok", "failed": "bad", "failing": "bad", "canceled": "muted", "cancelled": "muted"}
BUILDS: dict[tuple[str, str], dict] = {}
BUILDS_LOCK = threading.Lock()


def known_builds(ctx: Context, pipeline: str, checkout: str) -> list[dict]:
    with BUILDS_LOCK:
        entry = BUILDS.setdefault((str(ctx.dir), pipeline), {"builds": platy.Builds(ctx.dir / BUILDS_FILE, lambda query: []), "due": None, "lock": threading.Lock()})
    with entry["lock"]:
        builds = entry["builds"]
        builds.fetch = lambda query: ctx.json(["bk", "api", f"/pipelines/{pipeline}/builds?{urlencode({'per_page': platy.PER_PAGE} | query, doseq=True)}"], cwd=checkout)
        if entry["due"] is None or ctx.now >= entry["due"]:
            entry["due"] = ctx.now + BUILDS_INTERVAL
            builds.refresh(ctx.now)
        return builds.known()


def work_lines(ctx: Context, since: str) -> list[dict]:
    return [{"at": record["at"], "lane": record["lane"], "verb": record["kind"].upper(), "text": record["text"], "cite": f"cci:{record['seq']}", "url": (record.get("refs") or {}).get("url"), "to": ", ".join(record.get("to") or [])} for record in reversed(cci.records(ctx, since, kind=WORK_KINDS))]


def blockers(digest: dict, closed: set[str]) -> list[dict]:
    digest = overview.uncurated(digest, closed)
    found = [record for key in CCI_OPEN for record in digest.get(key) or [] if record["refs"].get("stacks") or record["refs"].get("targets")]
    return sorted(found, key=lambda record: (view.stamp(record["at"]), record["seq"]), reverse=True)


def pipeline_change(checkout: str, trunk: str, release_code: str) -> dict:
    out = subprocess.run(["git", "log", "-1", "--format=%H%x09%cI%x09%s", "--end-of-options", trunk, "--", release_code], cwd=checkout, capture_output=True, text=True, check=True, timeout=30).stdout
    sha, at, subject = out.strip().split("\t", 2)
    return {"sha": sha, "at": view.iso(view.stamp(at)), "subject": subject}


def contains(checkout: str):
    cache: dict[tuple[str, str], bool] = {}

    def check(ancestor: str, commit: str) -> bool:
        if (ancestor, commit) not in cache:
            verdict = subprocess.run(["git", "merge-base", "--is-ancestor", "--end-of-options", ancestor, commit], cwd=checkout, capture_output=True, text=True, timeout=30)
            if verdict.returncode not in (0, 1):
                raise subprocess.CalledProcessError(verdict.returncode, verdict.args, verdict.stdout, verdict.stderr)
            cache[(ancestor, commit)] = verdict.returncode == 0
        return cache[(ancestor, commit)]

    return check


def subjects(checkout: str, commits: set[str]) -> dict[str, str]:
    wanted = sorted(commit for commit in commits if commit)
    if not wanted:
        return {}
    data = subprocess.run(["git", "cat-file", "--batch"], input="\n".join(wanted).encode() + b"\n", cwd=checkout, capture_output=True, check=True, timeout=30).stdout
    found, offset = {}, 0
    for commit in wanted:
        end = data.index(b"\n", offset)
        header = data[offset:end].split()
        offset = end + 1
        if header[-1] in (b"missing", b"ambiguous"):
            continue
        size = int(header[2])
        body = data[offset : offset + size].decode(errors="replace")
        offset += size + 1
        if header[1] == b"commit":
            found[commit] = body.split("\n\n", 1)[1].split("\n", 1)[0]
    return found


def stack_rows(ctx: Context, state_dir: Path, checkout: str, census: str, targets: str, trunk: str, release_code: str, builds: list[dict]) -> list[dict]:
    report = view.newest(state_dir, census)
    if report is None:
        raise FileNotFoundError(f"no census report matches {census} under {state_dir}")
    since = ctx.facts.get("started_at")
    closed = overview.curated(cci.records(ctx, since, kind=CLOSING_KINDS))
    found = platy.stack_rows(
        platy.census_rows(report),
        platy.target_map(Path(checkout) / targets),
        builds,
        work_lines(ctx, since),
        blockers(ctx.cci("digest", since_time=since), closed),
        pipeline_change(checkout, trunk, release_code),
        contains(checkout),
    )
    return [row | {"cite": f"stack:{row['stack']}", "census_report": report.name} for row in found]


def release_rows(ctx: Context, builds: list[dict], checkout: str, repo: str, slack: str | None, pipeline: str) -> list[dict]:
    named = subjects(checkout, {build["commit"] for build in builds[: overview.RELEASE_LIMIT * 2]})
    return overview.release_rows(builds, named, repo, slack, overview.slack_links(work_lines(ctx, ctx.facts.get("started_at")), pipeline), ctx.now)


def incident_groups(ctx: Context, state_dir: Path) -> list[dict]:
    since = ctx.facts.get("started_at")
    closers = cci.records(ctx, since, kind=CLOSING_KINDS)
    incidents = cci.records(ctx, view.iso(ctx.now - INCIDENT_WINDOW), kind=list(overview.INCIDENT_KINDS))
    digest = ctx.cci("digest", since_time=since)
    folders = [{"slug": path.name, "path": str(path), "text": ", ".join(child.name for child in sorted(path.iterdir())[:8])} for path in (state_dir / "incidents").iterdir() if path.is_dir()] if (state_dir / "incidents").is_dir() else []
    resolved = {record["resolves"] for record in [*closers, *incidents] if record.get("resolves")}
    return overview.incident_groups(incidents, {record["seq"] for record in digest.get("open_incidents") or []}, folders, ctx.now, overview.curated(closers), resolved)


@component("builds", "Release builds", question="Which release builds ran, and what did each one ship?", reads=["bk api", "git log"], every="1m", timeout="2m")
def builds_card(ctx: Context, *, checkout: str, repo: str, pipeline: str = "release", slack: str | None = None) -> Table:
    """Release, hotfix, rollback, deploy, plan and check builds from the release pipeline, newest first, with the PR each ships."""
    rows = release_rows(ctx, known_builds(ctx, pipeline, checkout), checkout, repo, slack, pipeline)
    out = [row | {"key": str(row["number"]), "build": f"#{row['number']}", "build_url": row["url"], "pr_link": f"#{row['pr']}" if row["pr"] else None, "pr_link_url": row["pr_url"], "tone": STATE_TONE.get(row["state"], "warn" if row["live"] else None)} for row in rows]
    return Table([Col("build", "Build", "link"), Col("state", "State", "badge"), Col("title", "What"), Col("pr_link", "Ships", "link"), Col("at", "Started", "age"), Col("minutes", "Minutes", "num")], out)


@component("stacks", "Platy deployability", question="Which stacks can Platy deploy, and why not the rest?", reads=["bk api", "git", "cci digest"], every="2m", timeout="2m")
def stacks(ctx: Context, *, checkout: str, state_dir: Path, census: str, trunk: str = "origin/dev", release_code: str = "go/ci/internal/release/", targets: str = "release/targets.yaml", pipeline: str = "release") -> Table:
    """Each census stack, grouped by release target: proven, unproven or blocked through Platy, its drift against trunk,
    why it is not proven, and the lane working on it."""
    rows = stack_rows(ctx, state_dir, checkout, census, targets, trunk, release_code, known_builds(ctx, pipeline, checkout))
    out = [row | {"key": row["stack"], "platy_link": f"#{row['platy_build']}" if row["platy_build"] else None, "platy_link_url": row["platy_url"], "tone": DEPLOYABLE_TONE[row["deployable"]]} for row in rows]
    return Table([Col("stack", "Stack"), Col("deployable", "Platy", "badge"), Col("zero", "vs trunk", "badge"), Col("platy_link", "Last Platy release", "link"), Col("platy_at", "When", "age"), Col("reason", "Why not proven"), Col("doing_lane", "Lane on it")], out, group_by="target")


@component("tiles", "Release overview", question="How is the release pipeline doing today?", reads=["bk api", "git", "cci digest", "cci lanes"], every="1m", timeout="2m")
def tiles(ctx: Context, *, checkout: str, repo: str, state_dir: Path, census: str, ledger: str, trunk: str = "origin/dev", release_code: str = "go/ci/internal/release/", targets: str = "release/targets.yaml", pipeline: str = "release", slack: str | None = None) -> Tiles:
    """Stacks at 0/0, releases today, the median passed release, PRs landed today against open, open incidents and lanes working."""
    builds = known_builds(ctx, pipeline, checkout)
    rows = stack_rows(ctx, state_dir, checkout, census, targets, trunk, release_code, builds)
    opened = sum(1 for key, fields in ledgers.rows(ctx, ledger).items() if key.isdigit() and fields.get("state", "open") == "open")
    since = ctx.facts.get("started_at")
    landed = overview.landings(cci.records(ctx, view.iso(ctx.now - LANDED_WINDOW), kind="landed"), {}, repo)
    lanes = [lane for lane in ctx.cci("lanes") if ctx.now - view.stamp(lane["at"]) <= LANE_WINDOW]
    holds = overview.standing_holds([*(ctx.cci("digest", since_time=since).get("open_holds") or [])], cci.records(ctx, since, kind=CLOSING_KINDS))
    summary = overview.overview(release_rows(ctx, builds, checkout, repo, slack, pipeline), {"total": len(rows), "at_zero": sum(row["zero"] == "0/0" for row in rows)}, rows, opened, landed, lanes, incident_groups(ctx, state_dir), holds, [], ctx.now)["tiles"]
    today = summary["releases_today"]
    return Tiles(
        [
            Tile("Stacks at 0/0", summary["stacks"]["at_zero"], f"of {summary['stacks']['total']}", "ok" if summary["stacks"]["at_zero"] == summary["stacks"]["total"] else "warn"),
            Tile("Releases today", sum(today.values()), None, "bad" if today["failed"] else "ok", f"{today['passed']} passed, {today['failed']} failed, {today['running']} live"),
            Tile("Median release", summary["median_minutes"], "min", None, f"over {summary['median_of']} passed releases"),
            Tile("PRs landed today", summary["prs"]["landed_today"], None, None, f"{summary['prs']['open']} open in the ledger"),
            Tile("Open incidents", summary["incidents"], None, "bad" if summary["incidents"] else "ok", f"{summary['sightings']} without a lane"),
            Tile("Lanes working", summary["lanes"], None, None, "posted in the last 30 minutes"),
        ]
    )


@component("lines", "PR and release records", question="Which PR and release records arrived since the drive started?", reads=["cci records"], every="1m")
def lines(ctx: Context, *, limit: int = 150) -> Feed:
    """GO, OPENED, UPDATED, CLAIM, READY, LANDED, RELEASED and FIX-LIVE records since the drive started, newest first."""
    found = work_lines(ctx, ctx.facts.get("started_at"))[:limit]
    return Feed([Entry(line["at"], line["lane"], f"{line['verb']} {line['text']}", line["url"], "ok" if line["verb"] in ("LANDED", "RELEASED", "FIX-LIVE") else None, key=line["cite"], cite=line["cite"]) for line in found])


@component("census", "Stacks at 0/0 over time", question="Are more stacks reaching 0/0 over time?", reads=["cci records"], every="5m")
def census(ctx: Context) -> Series:
    """The census count of stacks at 0/0 and its denominator, from cci state records carrying census fields over 48 hours."""
    points = overview.census_trend(cci.records(ctx, view.iso(ctx.now - CENSUS_WINDOW), kind="state"), None, ctx.now)
    return Series([Line("at 0/0", [[point["at"], point["n"]] for point in points]), Line("stacks", [[point["at"], point["total"]] for point in points])], note=None if points else "No census record in 48h.")


@component("landed-per-hour", "PRs landed per hour", question="How many PRs landed in each of the last 24 hours?", reads=["cci records"], every="5m")
def landed_per_hour(ctx: Context, *, repo: str) -> Series:
    """PRs whose landed record arrived in each of the last 24 hours."""
    hours = overview.landed_hours(overview.landings(cci.records(ctx, view.iso(ctx.now - LANDED_WINDOW), kind="landed"), {}, repo), ctx.now)
    return Series([Line("landed", [[hour["hour"], hour["count"]] for hour in hours])])


@component("incidents", "Incidents", question="Which incidents are open, and which lanes work on them?", reads=["cci records", "cci digest"], every="1m", timeout="60s")
def incidents(ctx: Context, *, state_dir: Path) -> Table:
    """Incident threads from 72 hours of cci records, open first: their status, latest record and lanes."""
    groups = incident_groups(ctx, state_dir)
    rows = [{"key": group["key"], "cite": group["cite"], "title": group["title"], "status": group["status"], "latest": group["latest"], "lanes": ", ".join(group["lanes"]), "at": group["at"], "tone": "bad" if group["active"] else "muted"} for group in groups]
    return Table([Col("title", "Incident"), Col("status", "Status", "badge"), Col("latest", "Latest"), Col("lanes", "Lanes"), Col("at", "Updated", "age")], rows, note=None if rows else "No incident in 72h.")
