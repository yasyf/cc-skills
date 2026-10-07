from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from livedash import view, yamlish

STARTED = re.compile(r"^(?P<mode>release|hotfix|rollback|dry-run) (?P<picks>.+?), started by (?P<who>\S+)")
DEPLOY = re.compile(r"^(?P<verb>deploy|plan the deploy of|roll back) (?P<what>.+?)(?: to (?P<env>[\w-]+(?:,\s*[\w-]+)*))? at (?P<sha>[0-9a-f]{7,40})(?P<rest>.*)$")
DEPLOY_KINDS = {"deploy": "deploy", "plan the deploy of": "plan", "roll back": "rollback"}
WITHOUT = re.compile(r"\bwithout (?P<envs>[\w-]+(?:, [\w-]+)*)")
EMOJI = re.compile(r"^(?::[\w+-]+:\s*)+")
LIVE_JOB_STATES = frozenset({"running", "canceling", "failing"})
TERMINAL = frozenset({"passed", "failed", "canceled", "skipped", "not_run"})
LIVE = ("creating", "scheduled", "running", "blocked", "canceling", "failing")
OVERLAP = timedelta(minutes=2)
RESWEEP = timedelta(minutes=5)
WORK_VERBS = frozenset({"GO", "OPENED", "CLAIM", "READY", "LANDED", "RELEASE", "FIX-LIVE"})
PER_PAGE = 100
BACKFILL_PAGES = 5
UNTARGETED = "untargeted"
PROVEN = "proven"
UNPROVEN = "unproven"
BLOCKED = "blocked"
VERDICTS = (PROVEN, UNPROVEN, BLOCKED)
UNPLANNED_CAUSE = "not planned"


def build_row(build: dict) -> dict:
    message = (build.get("message") or "").splitlines()[0] if build.get("message") else ""
    start = (build.get("env") or {}).get("RELEASE_START") or ""
    jobs = [job for job in build.get("jobs") or [] if job.get("type") == "script"]
    row = {
        "number": build["number"],
        "state": build["state"],
        "branch": build.get("branch") or "",
        "commit": (build.get("commit") or "")[:12],
        "at": build.get("created_at"),
        "started_at": build.get("started_at"),
        "finished_at": build.get("finished_at"),
        "message": message,
        "url": build.get("web_url"),
        "kind": "other",
        "targets": [],
        "stacks": [],
        "platy": False,
        "thread": None,
        "steps": [sum(job.get("state") == "passed" for job in jobs), len(jobs)],
        "now": [EMOJI.sub("", job.get("name") or "") for job in jobs if job.get("state") in LIVE_JOB_STATES],
        "failed_steps": [EMOJI.sub("", job.get("name") or "") for job in jobs if job.get("state") in ("failed", "timed_out")],
    }
    if started := STARTED.match(message):
        recorded = json.loads(start) if start.startswith("{") else {}
        row |= {
            "kind": started["mode"],
            "targets": [pick.strip() for pick in started["picks"].split(",")],
            "starter": started["who"],
            "platy": bool(recorded.get("thread")),
            "thread": recorded.get("thread"),
            "applies": row["branch"].startswith("release"),
            "deselected": [f"{item['component']}/{item['env']}" for item in recorded.get("deselected") or []],
        }
    elif deploy := DEPLOY.match(message):
        picks = [pick.strip() for pick in deploy["what"].split(",") if pick.strip()]
        envs = [env.strip() for env in (deploy["env"] or "").split(",")]
        stacks = [pick if "/" in pick else f"{pick}/{env}" for pick in picks for env in ([None] if "/" in pick else envs)]
        without = WITHOUT.search(deploy["rest"])
        row |= {
            "kind": DEPLOY_KINDS[deploy["verb"]],
            "stacks": stacks,
            "applies": row["branch"].startswith("releases/deploy"),
            "without": without["envs"].split(", ") if without else [],
            "destructive": "allowing deletes" in deploy["rest"],
        }
    elif message.startswith("release check"):
        row["kind"] = "check"
    return row


class Builds:
    def __init__(self, cache: Path, fetch: Callable[[dict], list[dict]]):
        self.cache = cache
        self.fetch = fetch
        saved = json.loads(cache.read_text()) if cache.exists() else {"rows": {}, "backfill_page": 1, "fetched_at": None}
        self.rows: dict[int, dict] = {int(key): value for key, value in saved["rows"].items()}
        self.backfill_page: int = saved["backfill_page"]
        self.fetched_at = view.stamp(saved["fetched_at"])
        self.swept_at = self.fetched_at - RESWEEP if self.fetched_at else None

    def refresh(self, moment: datetime) -> list[dict]:
        if self.swept_at is None:
            self.swept_at = moment
        else:
            unfinished = {number for number, row in self.rows.items() if row["state"] not in TERMINAL}
            live = {build["number"] for build in self.pull({"state[]": list(LIVE)})}
            if unfinished - live or moment - self.swept_at >= RESWEEP:
                self.pull({"finished_from": view.iso_epoch(int((self.swept_at - OVERLAP).timestamp()))})
                self.swept_at = moment
        for _ in range(BACKFILL_PAGES):
            if not self.backfill_page:
                break
            batch = self.fetch({"page": self.backfill_page})
            self.store(batch)
            self.backfill_page = self.backfill_page + 1 if len(batch) == PER_PAGE else 0
        self.fetched_at = moment
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps({"rows": self.rows, "backfill_page": self.backfill_page, "fetched_at": view.iso(moment)}))
        return self.known()

    def pull(self, query: dict) -> list[dict]:
        found: list[dict] = []
        while True:
            batch = self.fetch(query | {"page": len(found) // PER_PAGE + 1})
            self.store(batch)
            found += batch
            if len(batch) < PER_PAGE:
                return found

    def store(self, batch: list[dict]) -> None:
        for build in batch:
            self.rows[build["number"]] = build_row(build)

    def known(self) -> list[dict]:
        return sorted(self.rows.values(), key=lambda row: row["number"], reverse=True)


def target_map(targets_file: Path) -> dict[str, str]:
    model = yamlish.loads(targets_file.read_text())
    return {component: target for target, spec in (model.get("targets") or {}).items() for component in spec.get("components") or []}


def census_rows(report: Path) -> list[dict]:
    text = report.read_text()
    rows = []
    for row in view.select_table(text, "Non-zero"):
        rows.append({"stack": row.get("stack", ""), "zero": "unplanned" if row.get("cause") == UNPLANNED_CAUSE else "drift", "cause": row.get("cause"), "cudr": row.get("c/u/d/r"), "applied": row.get("applied at"), "behind": row.get("commits behind"), "census_build": row.get("source")})
    for row in view.select_table(text, "at 0/0"):
        rows.append({"stack": row.get("stack", ""), "zero": "0/0", "cause": None, "cudr": "0/0/0/0", "applied": row.get("applied at"), "behind": row.get("commits behind"), "census_build": None})
    return [row for row in rows if "/" in row["stack"]]


def mentions(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.IGNORECASE)


def stack_of(component: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(component)}/[\w-]", re.IGNORECASE)


def naming(target: str, components: list[str]) -> list[re.Pattern]:
    return [mentions(target), *(stack_of(component) for component in components)]


def blocker_for(stack: str, target: str, records: list[dict]) -> dict | None:
    return next((record for record in records if target in (record["refs"].get("targets") or []) or stack in (record["refs"].get("stacks") or [])), None)


def work_for(blocker: dict, named: list[re.Pattern], lines: list[dict]) -> dict | None:
    lanes = [lane for lane in (*(blocker.get("to") or []), blocker.get("lane")) if lane]
    patterns = [*named, *(mentions(lane) for lane in lanes)]
    since = view.stamp(blocker["at"])
    for line in lines:
        moment = view.stamp(line.get("at"))
        if moment is None or moment < since:
            return None
        if (line.get("verb") or "") not in WORK_VERBS:
            continue
        if line.get("lane") in lanes or any(pattern.search(line["text"]) for pattern in patterns):
            return line
    return None


def stack_rows(census: list[dict], targets: dict[str, str], builds: list[dict], lines: list[dict], blockers: list[dict], pipeline: dict, contains: Callable[[str, str], bool]) -> list[dict]:
    releases = [build for build in builds if build["kind"] in ("release", "hotfix", "rollback") and build.get("applies")]
    deploys = [build for build in builds if build["kind"] == "deploy" and build.get("applies")]
    covering = {target: [build for build in releases if build["platy"] and target in build["targets"]] for target in set(targets.values())}
    named = {target: naming(target, [component for component, owner in targets.items() if owner == target]) for target in covering}
    works: dict[tuple[str, int | None], dict | None] = {}
    out = []
    for row in census:
        component, env = row["stack"].split("/", 1)
        target = targets.get(component) or UNTARGETED
        latest = covering[target][0] if covering.get(target) else None
        blocker = blocker_for(row["stack"], target, blockers)
        key = (target, component if target == UNTARGETED else None, blocker["seq"] if blocker else None)
        if key not in works:
            since = blocker or {"at": latest["at"] if latest else pipeline["at"]}
            works[key] = work_for(since, named.get(target) or naming(target, [component]), lines)
        work = works[key]
        releasing = [build for build in covering.get(target, []) if row["stack"] not in build["deselected"]]
        platy = releasing[0] if releasing else None
        passed = next((build for build in releasing if build["state"] == "passed"), None)
        deploy = next((build for build in deploys if row["stack"] in build["stacks"] and build["state"] == "passed"), None)
        proven = passed is not None and contains(pipeline["sha"], passed["commit"])
        work = None if proven and not blocker and latest["state"] == "passed" else work
        if blocker:
            deployable, reason = BLOCKED, blocker["text"]
        elif latest and latest["state"] != "passed":
            deployable, reason = UNPROVEN, f"the last Platy release of {target}, #{latest['number']}, {latest['state']}"
        elif proven:
            deployable, reason = PROVEN, None
        elif passed:
            deployable, reason = UNPROVEN, f"Platy last converged it at {passed['commit']} (#{passed['number']}), before the release pipeline changed at {pipeline['sha'][:10]}: {pipeline['subject']}"
        else:
            deployable, reason = UNPROVEN, f"no Platy release has converged {row['stack'] if target != UNTARGETED else component}"
        said = {"reason": reason, "reason_url": blocker["refs"].get("url") if blocker else None, "doing": work["text"] if work else None, "doing_url": work.get("url") if work else None, "doing_lane": work.get("lane") if work else None}
        out.append(
            row
            | {
                "component": component,
                "env": env,
                "target": target,
                "platy_at": platy["at"] if platy else None,
                "platy_build": platy["number"] if platy else None,
                "platy_state": platy["state"] if platy else None,
                "platy_commit": platy["commit"] if platy else None,
                "platy_url": platy["url"] if platy else None,
                "last_pass_at": passed["at"] if passed else None,
                "last_pass_commit": passed["commit"] if passed else None,
                "proven_at": passed["commit"] if deployable == PROVEN and passed else None,
                "unproven_since": pipeline["sha"][:12] if deployable == UNPROVEN and passed else None,
                "blocked_by": blocker["text"] if deployable == BLOCKED and blocker else None,
                "blocked_seq": blocker["seq"] if deployable == BLOCKED and blocker else None,
                "pipeline_change": pipeline["sha"][:12],
                "pipeline_change_at": pipeline["at"],
                "pipeline_change_subject": pipeline["subject"],
                "target_build": latest["number"] if latest else None,
                "target_state": latest["state"] if latest else None,
                "target_at": latest["at"] if latest else None,
                "target_url": latest["url"] if latest else None,
                "cli_at": deploy["at"] if deploy else None,
                "cli_build": deploy["number"] if deploy else None,
                "cli_url": deploy["url"] if deploy else None,
                "deployable": deployable,
                **said,
                "cell": deployable if row["zero"] == "0/0" else f"{deployable} · {row['zero']}",
                "text": f"{row['stack']}: {deployable}; {row['zero']}; {said['reason'] or 'deployable via Platy'}",
                "url": platy["url"] if platy else None,
            }
        )
    return out


def target_rows(stacks: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in stacks:
        grouped.setdefault(row["target"], []).append(row)
    out = []
    for target, rows in sorted(grouped.items()):
        first = rows[0]
        working = next((row for row in rows if row["deployable"] != PROVEN and row["doing"]), first)
        counts = {verdict: sum(row["deployable"] == verdict for row in rows) for verdict in VERDICTS}
        passes = [row["last_pass_at"] for row in rows if row["last_pass_at"]]
        out.append(
            {
                "target": target,
                "stacks": len(rows),
                "at_zero": sum(row["zero"] == "0/0" for row in rows),
                **counts,
                "deployable": BLOCKED if counts[BLOCKED] else PROVEN if counts[PROVEN] == len(rows) else UNPROVEN,
                "platy_build": first["target_build"],
                "platy_state": first["target_state"],
                "platy_at": first["target_at"],
                "platy_url": first["target_url"],
                "last_pass_at": max(passes) if passes else None,
                "reason": next((row["reason"] for row in rows if row["deployable"] != PROVEN and row["reason"]), None),
                "reason_url": next((row["reason_url"] for row in rows if row["reason_url"]), None),
                **{field: working[field] for field in ("doing", "doing_url", "doing_lane")},
                "url": first["target_url"],
                "cite": f"target:{target}",
                "text": f"{target}: {counts[PROVEN]} proven, {counts[UNPROVEN]} unproven, {counts[BLOCKED]} blocked of {len(rows)} stacks; last Platy release #{first['target_build']} {first['target_state']}",
            }
        )
    return out
