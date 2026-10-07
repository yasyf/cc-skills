from __future__ import annotations

import statistics
import subprocess
import threading
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from livedash import Col, Context, Entry, Feed, Heat, Heatmap, Line, RateLimited, Series, Span, Table, Tile, Tiles, Timeline, Track, component, view
from livedash.context import RATE_LIMIT_BACKOFF
from livedash.components import bus, cci
from livedash.components import ledger as ledgers
from livedash.components.release import overview, platy

BUILDS_FILE = "buildkite-builds.json"
BUILDS_INTERVAL = timedelta(minutes=1)
INCIDENT_WINDOW = timedelta(hours=72)
CENSUS_WINDOW = timedelta(hours=48)
LANDED_WINDOW = timedelta(hours=26)
LANE_WINDOW = timedelta(hours=48)
CLOSING_KINDS = ["done", "lift", "go", "decision", "owner", "answer"]
WORK_KINDS = ["go", "opened", "claim", "ready", "landed", "release", "fix-live"]
CCI_OPEN = ("open_defects", "open_blockers", "open_holds")
DEPLOYABLE_TONE = {platy.PROVEN: "ok", platy.UNPROVEN: "warn", platy.BLOCKED: "bad"}
STACK_FIELDS = ("stack", "cite", "target", "deployable", "zero", "platy_at", "reason", "reason_url", "doing_lane", "doing_url")
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
            try:
                builds.refresh(ctx.now)
            except RateLimited:
                entry["due"] = ctx.now + timedelta(seconds=RATE_LIMIT_BACKOFF)
                raise
        return builds.known()


def backfilling(ctx: Context, pipeline: str) -> bool:
    with BUILDS_LOCK:
        return bool(BUILDS[(str(ctx.dir), pipeline)]["builds"].backfill_page)


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
def builds_card(ctx: Context, *, checkout: str, repo: str, pipeline: str = "release", slack: str | None = None, limit: int = 20) -> Table:
    """The newest `limit` release, hotfix, rollback, deploy, plan and check builds from the release pipeline, live ones
    first, with the PR each ships."""
    rows = release_rows(ctx, known_builds(ctx, pipeline, checkout), checkout, repo, slack, pipeline)[:limit]
    out = [
        {"key": str(row["number"]), "cite": row["cite"], "build": f"#{row['number']}", "build_url": row["url"], "state": row["state"], "title": row["title"], "by": row["by"], "pr_link": f"#{row['pr']}" if row["pr"] else None, "pr_link_url": row["pr_url"], "at": row["at"], "minutes": row["minutes"], "tone": STATE_TONE.get(row["state"], "warn" if row["live"] else None)}
        for row in sorted(rows, key=lambda row: not row["live"])
    ]
    return Table([Col("build", "Build", "link"), Col("state", "State", "badge"), Col("title", "What"), Col("by", "By", phone=False), Col("pr_link", "Ships", "link"), Col("at", "Started", "age"), Col("minutes", "Minutes", "num", phone=False)], out)


def summarized(rows: list[dict]) -> list[dict]:
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        buckets.setdefault((row["target"], row["deployable"], row["reason"], row["zero"]), []).append(row)
    out = []
    for (target, _, _, zero), found in buckets.items():
        if len(found) == 1:
            out += found
            continue
        named = "" if zero == "0/0" else ": " + ", ".join(row["stack"] for row in found)
        out.append(found[0] | {"stack": f"{len(found)} stacks at {zero}{named}" if zero == "0/0" else f"{len(found)} stacks with {zero}{named}", "cite": f"target:{target}:{zero}:{found[0]['stack']}"})
    return out


@component("stacks", "Platy deployability", question="Which stacks can Platy deploy, and why not the rest?", reads=["bk api", "git", "cci digest"], every="2m", timeout="2m")
def stacks(ctx: Context, *, checkout: str, state_dir: Path, census: str, trunk: str = "origin/dev", release_code: str = "go/ci/internal/release/", targets: str = "release/targets.yaml", pipeline: str = "release") -> Table:
    """Each census stack, grouped by release target: proven, unproven or blocked through Platy, its drift against trunk,
    why it is not proven, and the lane working on it. A target's stacks that share a verdict, a reason and a drift state
    fold into one row; stacks off 0/0 stay named in it."""
    rows = summarized(stack_rows(ctx, state_dir, checkout, census, targets, trunk, release_code, known_builds(ctx, pipeline, checkout)))
    out = [{field: row[field] for field in STACK_FIELDS} | {"key": row["cite"], "platy_link": f"#{row['platy_build']}" if row["platy_build"] else None, "platy_link_url": row["platy_url"], "tone": DEPLOYABLE_TONE[row["deployable"]]} for row in rows]
    return Table([Col("stack", "Stack"), Col("deployable", "Platy", "badge"), Col("zero", f"vs {trunk.removeprefix('origin/')}", "badge"), Col("platy_link", "Last Platy release", "link"), Col("platy_at", "When", "age"), Col("reason", "Why not proven"), Col("doing_lane", "Lane on it")], out, group_by="target", note="Backfilling the release pipeline's Buildkite history; a stack's last Platy release may be older than the builds read so far." if backfilling(ctx, pipeline) else None)


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
    """GO, OPENED, CLAIM, READY, LANDED, RELEASE and FIX-LIVE records since the drive started, newest first."""
    found = work_lines(ctx, ctx.facts.get("started_at"))[:limit]
    return Feed([Entry(line["at"], line["lane"], f"{line['verb']} {line['text']}", line["url"], "ok" if line["verb"] in ("LANDED", "RELEASE", "FIX-LIVE") else None, key=line["cite"], cite=line["cite"]) for line in found])


@component("census", "Stacks at 0/0 over time", question="Are more stacks reaching 0/0 over time?", reads=["cci records"], every="5m")
def census(ctx: Context) -> Series:
    """The census count of stacks at 0/0 and its denominator, from cci state records carrying census fields over 48 hours."""
    points = overview.census_trend(cci.records(ctx, view.iso(ctx.now - CENSUS_WINDOW), kind="state"), None, ctx.now)
    return Series([Line("at 0/0", [[point["at"], point["n"]] for point in points], "area"), Line("stacks", [[point["at"], point["total"]] for point in points])], note=None if points else "No census record in 48h.")


@component("landed-per-hour", "PRs landed per hour", question="How many PRs landed in each of the last 24 hours?", reads=["cci records"], every="5m")
def landed_per_hour(ctx: Context, *, repo: str) -> Series:
    """PRs whose landed record arrived in each of the last 24 hours."""
    hours = overview.landed_hours(overview.landings(cci.records(ctx, view.iso(ctx.now - LANDED_WINDOW), kind="landed"), {}, repo), ctx.now)
    return Series([Line("landed", [[hour["hour"], hour["count"], {"label": f"{hour['count']} landed: " + ", ".join(f"#{row['pr']}" for row in hour["prs"]) if hour["prs"] else "none landed"}] for hour in hours], "bar")])


@component("incidents", "Incidents", question="Which incidents are open, and which lanes work on them?", reads=["cci records", "cci digest"], every="1m", timeout="60s")
def incidents(ctx: Context, *, state_dir: Path, recent: str = "6h") -> Table:
    """Incident threads from 72 hours of cci records: every active one, and those settled or gone quiet within `recent`,
    open first, with their status, latest record and lanes. Only an incident record, or a record keyed to a known incident
    slug, opens a thread."""
    groups = [group for group in incident_groups(ctx, state_dir) if group["active"] or not bus.expired(group, ctx.now, recent)]
    rows = [{"key": group["key"], "cite": group["cite"], "title": group["title"], "status": group["status"], "latest": None if group["latest"] == group["title"] else group["latest"], "lanes": ", ".join(group["lanes"]), "at": group["at"], "tone": "bad" if group["active"] else "muted"} for group in groups]
    return Table([Col("title", "Incident"), Col("status", "Status", "badge"), Col("latest", "Latest"), Col("lanes", "Lanes"), Col("at", "Updated", "age")], rows, note=None if rows else f"No open incident, and none settled in {recent}.")


APPLYING_KINDS = frozenset({"release", "hotfix", "rollback", "deploy"})
PHASE_TONE = {"passed": "ok", "running": "warn", "failed": "bad"}
GOAL_MINUTES = 5.0
DURATION_BARS = 40
TIMELINE_TRACKS = 12
CELL_RANK = ("bad", "warn", "muted", "ok")
CENSUS_TONE = {"0/0": "ok", "drift": "warn", "unplanned": "muted"}


def census_tone(row: dict) -> str:
    return "bad" if row["deployable"] == platy.BLOCKED else CENSUS_TONE[row["zero"]]


def without_watch(row: dict) -> float:
    return round(max(row["minutes"] - row["watch_minutes"], 0.0), 1)


def finished_applies(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["kind"] in APPLYING_KINDS and row["applies"] and row["state"] in overview.FINISHED and row["minutes"] is not None]


def percentile(values: list[float], share: float) -> float | None:
    ordered = sorted(values)
    return ordered[max(round(share * len(ordered) + 0.5) - 1, 0)] if ordered else None


@component("durations", "Release time without Watch", question="How long did each release take, Watch excluded, against the goal?", reads=["bk api", "git log"], every="2m", timeout="2m")
def durations(ctx: Context, *, checkout: str, repo: str, pipeline: str = "release", slack: str | None = None, goal_minutes: float = GOAL_MINUTES) -> Series:
    """The last 40 finished release, hotfix, rollback and deploy builds that applied, oldest first: minutes from start to
    finish minus the Watch steps, as bars toned by the build's state and linked to Buildkite, under a `goal_minutes` rule."""
    rows = finished_applies(release_rows(ctx, known_builds(ctx, pipeline, checkout), checkout, repo, slack, pipeline))[:DURATION_BARS]
    points = [[row["finished_at"] or row["at"], without_watch(row), {"tone": STATE_TONE.get(row["state"], "muted"), "link": row["url"], "label": f"#{row['number']} {row['title']}: {without_watch(row)} min without Watch ({row['minutes']} min in all)"}] for row in reversed(rows)]
    return Series([Line("minutes without Watch", points, "bar")], unit="min", thresholds={"goal": goal_minutes}, note=None if points else "No finished release yet.")


def track_of(row: dict) -> Track:
    spans = [Span(phase["phase"], phase["start"], phase["end"], PHASE_TONE.get(phase["state"], "muted")) for phase in row["phases"]]
    if not spans:
        spans = [Span(row["state"], row["started_at"] or row["at"], row["finished_at"], STATE_TONE.get(row["state"], "warn" if row["live"] else "muted"))]
    passed, total = row["steps"] or [0, 0]
    doing = f" · {row['now'][0]}" if row["live"] and row["now"] else ""
    return Track(f"#{row['number']} {row['title']}", spans, row["url"], STATE_TONE.get(row["state"], "warn" if row["live"] else None), f"{passed} of {total} steps{doing}", str(row["number"]), row["cite"])


@component("timeline", "Releases now", question="Which releases are running, and which phase is each one in?", reads=["bk api", "git log"], every="1m", timeout="2m")
def timeline(ctx: Context, *, checkout: str, repo: str, pipeline: str = "release", slack: str | None = None, hours: int = 3) -> Timeline:
    """Every live release, hotfix, rollback, deploy or plan build, then those finished in the last `hours`, as a Gantt of
    their Build, Plan, Wait, Deploy, Watch and Finish phases read from the Buildkite job times."""
    floor = view.iso(ctx.now - timedelta(hours=hours))
    rows = [row for row in release_rows(ctx, known_builds(ctx, pipeline, checkout), checkout, repo, slack, pipeline) if row["kind"] != "check" and (row["live"] or (row["finished_at"] or "") >= floor)]
    rows.sort(key=lambda row: (not row["live"], row["started_at"] or row["at"]), reverse=False)
    tracks = [track_of(row) for row in rows[:TIMELINE_TRACKS]]
    return Timeline(tracks, note=None if tracks else f"No release ran in the last {hours} hours.")


@component("census-grid", "Census by target", question="Which targets are at 0/0 in which environments, and where is the drift?", reads=["census report", "bk api", "git", "cci digest"], every="2m", timeout="2m")
def census_grid(ctx: Context, *, checkout: str, state_dir: Path, census: str, trunk: str = "origin/dev", release_code: str = "go/ci/internal/release/", targets: str = "release/targets.yaml", pipeline: str = "release") -> Heatmap:
    """One square per release target and environment, toned by its worst stack: green when every stack is at 0/0, amber
    drifting, red blocked through Platy, grey not planned. A square counts the stacks still off 0/0, names each by
    component and verdict, and links the Platy release of the first one off 0/0."""
    rows = stack_rows(ctx, state_dir, checkout, census, targets, trunk, release_code, known_builds(ctx, pipeline, checkout))
    by_target = Counter(row["target"] for row in rows)
    names = [target for target, _ in by_target.most_common()]
    envs = sorted({row["env"] for row in rows})
    at: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        at.setdefault((row["target"], row["env"]), []).append(row)
    cells = []
    for target in names:
        line = []
        for env in envs:
            stacks = at.get((target, env))
            if not stacks:
                line.append(None)
                continue
            off = [row for row in stacks if census_tone(row) != "ok"]
            worst = min((census_tone(row) for row in stacks), key=CELL_RANK.index)
            said = ", ".join(f"{row['component']} {'blocked' if census_tone(row) == 'bad' else row['zero']}" for row in off)
            title = f"{target} in {env}: {len(stacks) - len(off)} of {len(stacks)} at 0/0" + (f"; {said}" if said else "")
            line.append(Heat(worst, title, (off or stacks)[0]["platy_url"], str(len(off)) if off else None))
        cells.append(line)
    tones = Counter(census_tone(row) for row in rows)
    legend = {tone: f"{label} {tones[tone]}" for tone, label in (("ok", "at 0/0"), ("warn", "drift"), ("bad", "blocked"), ("muted", "not planned"))}
    return Heatmap([f"{target} ({by_target[target]})" for target in names], envs, cells, None, legend, f"{len(rows)} stacks; a number counts the stacks off 0/0 in that square.")


def trend_of(delta: float, unit: str, window: str, better_up: bool, lead: str = "") -> tuple[str | None, str | None]:
    if not delta:
        return f"no change {window}", "muted"
    arrow = "▲" if delta > 0 else "▼"
    return f"{arrow} {lead}{abs(delta):,g}{unit} {window}", "ok" if (delta > 0) == better_up else "bad"


def census_then(records: list[dict], moment) -> dict | None:
    floor = moment - timedelta(hours=24)
    older = [record for record in records if (record.get("fields") or {}).get("census") and view.stamp(record["at"]) <= floor]
    return older[-1]["fields"]["census"] if older else None


@component("kpis", "Release program", question="Where do the numbers that matter stand right now, and which way are they moving?", reads=["bk api", "git", "cci records", "census report", "the spend, review, needs-owner and incidents cards"], every="1m", timeout="2m")
def kpis(ctx: Context, *, checkout: str, repo: str, state_dir: Path, census: str, trunk: str = "origin/dev", release_code: str = "go/ci/internal/release/", targets: str = "release/targets.yaml", pipeline: str = "release", slack: str | None = None, goal_minutes: float = GOAL_MINUTES, spend: str = "spend", review: str = "review", owner: str = "needs-owner", incidents: str = "incidents") -> Tiles:
    """The program's headline numbers as tiles: stacks at 0/0 with a ring and its change over 24 hours, releases today
    and their pass rate, median and p90 release time without Watch against `goal_minutes`, yesterday's cloud spend
    against target from the `spend` card, open PRs from the `review` card, open incidents from the `incidents` card, and
    items waiting on the owner from the `owner` card. Each tile jumps to the card behind it."""
    builds = known_builds(ctx, pipeline, checkout)
    stacks = stack_rows(ctx, state_dir, checkout, census, targets, trunk, release_code, builds)
    at_zero, total = sum(row["zero"] == "0/0" for row in stacks), len(stacks)
    then = census_then(cci.records(ctx, view.iso(ctx.now - CENSUS_WINDOW), kind="state"), ctx.now)
    trend, trend_tone = trend_of(at_zero - then["n"], "", "in 24h", True) if then else (None, None)
    drift = Counter(row["zero"] for row in stacks)
    tiles = [Tile("Stacks at 0/0", at_zero, f"/{total}", "ok" if at_zero == total else "warn", f"{total - at_zero} to go", "#card-stacks", trend, trend_tone, at_zero / total if total else None, f"{drift['drift']} drift · {drift['unplanned']} not planned")]
    rows = release_rows(ctx, builds, checkout, repo, slack, pipeline)
    today = ctx.now.astimezone(overview.PACIFIC).date().isoformat()
    yesterday = (ctx.now.astimezone(overview.PACIFIC) - timedelta(days=1)).date().isoformat()
    applying = [row for row in rows if row["kind"] in APPLYING_KINDS and row["applies"]]
    todays = [row for row in applying if overview.pacific_day(row["at"]) == today]
    finished = [row for row in todays if row["state"] in overview.FINISHED]
    passed = sum(row["state"] == "passed" for row in finished)
    rate = round(100 * passed / len(finished)) if finished else None
    before = [row for row in applying if overview.pacific_day(row["at"]) == yesterday and row["state"] in overview.FINISHED]
    before_rate = round(100 * sum(row["state"] == "passed" for row in before) / len(before)) if before else None
    trend, trend_tone = trend_of(rate - before_rate, " pts", "vs yesterday", True, "pass rate ") if rate is not None and before_rate is not None else (None, None)
    tiles.append(Tile("Releases today", len(todays), None, "bad" if rate is not None and rate < 50 else "ok", f"{rate}% passed ({passed} of {len(finished)} finished)" if rate is not None else "none finished yet", "#card-timeline", trend, trend_tone, None, f"{sum(row['live'] for row in todays)} running now"))
    recent = [without_watch(row) for row in finished_applies(rows) if row["state"] == "passed"][:20]
    prior = [without_watch(row) for row in finished_applies(rows) if row["state"] == "passed"][20:40]
    median, p90 = (round(statistics.median(recent), 1), percentile(recent, 0.9)) if recent else (None, None)
    trend, trend_tone = trend_of(round(median - statistics.median(prior), 1), " min", "vs the 20 before", False) if recent and prior else (None, None)
    tiles.append(Tile("Median release", median, "min", None if median is None else "ok" if median <= goal_minutes else "warn" if median <= 2 * goal_minutes else "bad", f"p90 {p90} min · goal {goal_minutes:g} min, Watch excluded" if recent else "no passed release yet", "#card-durations", trend, trend_tone, None, f"last {len(recent)} passed releases"))
    if (bars := ctx.latest(spend)) is not None and bars.lines and bars.lines[0].points:
        points = bars.lines[0].points
        last = points[-1][1]
        week = [point[1] for point in points[-8:-1]]
        target = next(iter(bars.thresholds.values()), None)
        trend, trend_tone = trend_of(round(last - statistics.mean(week)), "", "vs 7-day mean", False, "$") if week else (None, None)
        tiles.append(Tile("Cloud spend", f"${last:,}", "/day", None if target is None else "ok" if last <= target else "warn", f"{points[-1][0]}" + (f" · target ${target:,.0f}/day" if target is not None else ""), "#card-spend", trend, trend_tone, None, None))
    if (table := ctx.latest(review)) is not None:
        landable = sum(row.get("verdict") == "landable" for row in table.rows)
        red = sum(row.get("ci") == "red" for row in table.rows)
        tiles.append(Tile("Open PRs", len(table.rows), None, "bad" if red else None, f"{landable} landable · {red} red", "#card-review"))
    if (table := ctx.latest(incidents)) is not None:
        active = sum(row.get("tone") == "bad" for row in table.rows)
        tiles.append(Tile("Open incidents", active, None, "bad" if active else "ok", f"{len(table.rows) - active} settled recently", "#card-incidents"))
    if (table := ctx.latest(owner)) is not None:
        tiles.append(Tile("Waiting on you", len(table.rows), None, "warn" if table.rows else "ok", "owner tasks, asks and boards", "#card-needs-owner"))
    return Tiles(tiles)
