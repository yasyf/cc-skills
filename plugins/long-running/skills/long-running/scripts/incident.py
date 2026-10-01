#!/usr/bin/env python3
"""The incident executor: one durable owner for an active incident, from intake to the final reply.

    incident.py open    --kind pr-review|alert --thread URL --onset ISO --bus ID --comms-lane NAME --root-lane NAME
                        --checkout DIR [--incident ID] [--pipeline SLUG] [--target NAME] [--repo OWNER/NAME] [--trunk BRANCH]
                        [--alert URL] [--code-path TEXT] [--runbook TEXT] [--adopt ROLE=LANE]...
                        [--grant NAME=REF]... [--expect-config TEXT] [--repair-pr N] [--orca-run ID --orca-repo ID] [--common PATH]
    incident.py note    --incident ID [--pr N] [--mechanism TEXT] [--live TEXT] [--not-ours TEXT]
    incident.py grant   --incident ID --name thread|channel|sync|rebuild --ref REF
    incident.py run     --incident ID [--owner NAME] [--interval S] [--once]
    incident.py status  --incident ID [--json]

STDLIB ONLY. ``open`` records the incident in the :mod:`actions` store; ``run`` owns it.
Each pass of ``run`` advances every step whose inputs are ready and records each side
effect as an action before it runs: the comms events, the sol fix and evidence launches
through ``orca-launch.sh`` (an Opus 5.5 backup at 15 minutes with no mechanism), the human
review request a reviewer fix needs, the landing check, the single-pipeline ``ci sync``
activation with its read-back, the canary, one rebuild per failed head in the outage window,
the accounting of every rebuild, and the final reply. A merge never closes the incident:
it stays ``activation_pending`` until the stored configuration matches the landed tree, and
no rebuild fires before that read-back and a passing canary. The inventory is re-derived
on every pass, so a head that failed after the first pass is still owned. An ``alert``
incident has no pipeline to activate: it is live when a lane notes ``--live`` evidence. Any
incident closes without a fix when a lane notes ``--not-ours`` evidence. ``--adopt`` records a
lane the desk already launched, so the executor never starts a second worker for the same role.

The incident's record lives in cc-notes on the checkout: the first pass opens an investigation
whose premise is the alert and an ``incident <id>`` log, both labelled ``incident:<id>``. Every
milestone becomes a log entry, each lane brief is attached to the log, and the mechanism,
landing, and not-ours verdicts land on the investigation. Briefs carry both ids, so lanes
append evidence with ``ccn log append`` instead of writing scratch files. A refused cc-notes
write reaches the root once and never blocks a launch.

A side effect needs its grant: ``thread`` and ``channel`` for comms posts, which the comms
lane makes with ``cc-slack ... --grant <ref>``; ``sync`` for the apply; ``rebuild`` for the
re-kick. A missing grant, a silent comms lane, an overdue action, and a failed canary are
the only things that reach the root, each once, as a bus ask and an open decision.
``grant`` adds the missing authority and the next pass proceeds.

Every subprocess goes through :class:`Shell`; the adapters it feeds are the seam the replay
tests replace.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from actions import Action, Incident, StaleGeneration, Store, parse_stamp, stamp

SCRIPTS = Path(__file__).resolve().parent
TEMPLATE = SCRIPTS.parent / "reference" / "active-alert-brief.md"
PACIFIC = ZoneInfo("America/Los_Angeles")
SOL = "sol"
INCIDENT_ROLES = ("fix", "evidence")
FORBIDDEN_MODELS = ("fable", "codex", "claude-fable-5-1", "gpt-6-astra")
ROUTES = {"fix": (SOL, "xhigh"), "evidence": (SOL, "xhigh"), "backup": ("opus", "xhigh")}
ADOPTABLE = ("fix", "evidence")
GRANTS = ("thread", "channel", "sync", "rebuild")
BACKUP_AFTER = timedelta(minutes=15)
COMMS_DEADLINE = timedelta(minutes=2)
SYNC_DEADLINE = timedelta(minutes=10)
REBUILD_DEADLINE = timedelta(minutes=45)
INTERVAL = 30
TERMINAL_BUILDS = ("passed", "failed", "canceled", "skipped", "not_run")
TERMINAL_JOBS = (*TERMINAL_BUILDS, "broken", "timed_out", "expired")
RECONCILED = "reconciled:"
MAX_ATTEMPTS = 3
UNFINISHED_BUILDS = ("creating", "scheduled", "running", "failing", "canceling", "blocked")
GREEN = "passed"
PROFILES = {
    "pr-review": {"pipeline": "pr-review", "canary_job": "Prepare PR evidence", "human_review": True, "review_surface": "channel"},
    "alert": {"pipeline": None, "canary_job": None, "human_review": False, "review_surface": "thread"},
}
LAUNCH = re.compile(r"^(?P<lane>\S+) (?P<state>ready|unsupervised) task=(?P<task>\S+) dispatch=(?P<dispatch>\S+) terminal=(?P<terminal>\S*) worktree=(?P<worktree>\S+)$")
POSTED = re.compile(r"\bts=(?P<ts>\d+\.\d+)")
PLACEHOLDER = re.compile(r"<([a-z][a-z -]*)>")


class RouteRefused(ValueError):
    """An incident role was routed to a model its rule forbids."""


class EffectFailed(RuntimeError):
    """The side effect definitely did not happen; the text says why."""


class ResponseLost(RuntimeError):
    """The side effect may have happened and its response did not arrive."""


LOST = (ResponseLost, subprocess.CalledProcessError, subprocess.TimeoutExpired, ConnectionError)
CALL_TIMEOUT = 900


class Shell:
    """The single subprocess boundary, plus the wall clock and the sleep the run loop uses."""

    def run(self, argv: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=True, timeout=CALL_TIMEOUT).stdout

    def call(self, argv: list[str], cwd: Path | None = None) -> tuple[int, str]:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=CALL_TIMEOUT)
        return proc.returncode, proc.stdout + proc.stderr

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def pacific(value: str | datetime) -> str:
    at = parse_stamp(value) if isinstance(value, str) else value
    return at.astimezone(PACIFIC).strftime("%-I:%M%p").lower()


def assert_route(role: str, model: str) -> None:
    if role in INCIDENT_ROLES and model != SOL:
        raise RouteRefused(f"the {role} lane runs on sol (gpt-6.1-sol, fast tier), never {model}")
    if model in FORBIDDEN_MODELS:
        raise RouteRefused(f"no incident lane runs on {model}")


def render_brief(section: str, values: dict[str, str], template: Path = TEMPLATE) -> str:
    text = template.read_text()
    start = text.index(f"## {section}")
    body = text[start:].split("```text\n", 1)[1].split("\n```", 1)[0]
    return PLACEHOLDER.sub(lambda match: values.get(match[1], "n/a"), body) + "\n"


@dataclass
class Orca:
    shell: Shell
    run_id: str | None
    repo_id: str | None
    launcher: Path = SCRIPTS / "orca-launch.sh"

    def launch(self, lane: str, model: str, effort: str, brief: Path) -> dict:
        if not (self.run_id and self.repo_id):
            raise EffectFailed("no Orca run: open the incident with --orca-run and --orca-repo")
        env = os.environ | {"ORCA_LAUNCH_RUN": self.run_id, "ORCA_LAUNCH_REPO": self.repo_id}
        line = self.shell.run([str(self.launcher), lane, model, effort, str(brief)], env=env).strip().splitlines()[-1]
        if not (match := LAUNCH.match(line)):
            raise EffectFailed(line)
        return match.groupdict()

    def receipt(self, lane: str) -> dict | None:
        state = Path(os.environ.get("ORCA_LAUNCH_STATE") or Path.home() / ".claude" / "scratch" / "orca-launch" / str(self.run_id))
        for path in (state / f"{lane}.json", state / f"{lane}.json.new"):
            result = json.loads(path.read_text()).get("result", {}) if path.exists() and path.stat().st_size else {}
            if result.get("taskId") and result.get("dispatchId"):
                return {"lane": lane, "state": result.get("state"), "task": result["taskId"], "dispatch": result["dispatchId"]}
        return None


@dataclass
class GitHub:
    shell: Shell
    repo: str
    trunk: str

    def prs(self, numbers: list[int]) -> dict[int, dict]:
        if not numbers:
            return {}
        owner, name = self.repo.split("/")
        fields = "number state headRefOid url"
        aliases = " ".join(f"p{n}: pullRequest(number: {n}) {{ {fields} }}" for n in numbers)
        query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {aliases} }} }}'
        data = json.loads(self.shell.run(["gh", "api", "graphql", "-f", f"query={query}"]))["data"]["repository"]
        return {pr["number"]: {"state": pr["state"], "head": pr["headRefOid"], "url": pr["url"]} for pr in data.values() if pr}

    def landed(self, pr: int) -> str | None:
        view = json.loads(self.shell.run(["gh", "pr", "view", str(pr), "--repo", self.repo, "--json", "state,mergedAt,mergeCommit,closedAt"]))
        if view["state"] == "MERGED":
            return view["mergeCommit"]["oid"]
        if view["state"] != "CLOSED":
            return None
        since = stamp(parse_stamp(view["closedAt"]) - timedelta(minutes=10))
        commits = json.loads(self.shell.run(["gh", "api", f"repos/{self.repo}/commits?sha={self.trunk}&since={since}&per_page=100"]))
        suffix = f"(#{pr})"
        return next((commit["sha"] for commit in commits if commit["commit"]["message"].splitlines()[0].endswith(suffix)), None)


@dataclass
class Buildkite:
    shell: Shell
    pipeline: str

    def api(self, path: str) -> object:
        return json.loads(self.shell.run(["bk", "api", path]))

    def configuration(self) -> str:
        return self.api(f"/pipelines/{self.pipeline}")["configuration"]

    def builds(self, query: str) -> list[dict]:
        found, page = [], 1
        while True:
            batch = self.api(f"/pipelines/{self.pipeline}/builds?{query}&per_page=100&page={page}")
            found += batch
            if len(batch) < 100:
                return found
            page += 1

    def failed(self, since: str, until: str) -> list[dict]:
        return self.builds(f"state[]=failed&state[]=canceled&created_from={since}&created_to={until}")

    def unfinished(self, since: str, until: str) -> list[dict]:
        states = "&".join(f"state[]={state}" for state in UNFINISHED_BUILDS)
        return self.builds(f"{states}&created_from={since}&created_to={until}")

    def since(self, since: str) -> list[dict]:
        return self.builds(f"created_from={since}")

    def rebuild(self, number: int) -> dict:
        return json.loads(self.shell.run(["bk", "api", "-X", "PUT", f"/pipelines/{self.pipeline}/builds/{number}/rebuild"]))


@dataclass
class Activation:
    shell: Shell
    checkout: Path
    pipeline: str
    worktree: Path

    def prepare(self, sha: str) -> Path:
        if not self.worktree.exists():
            self.shell.run(["git", "-C", str(self.checkout), "fetch", "origin", sha])
            self.shell.run(["git", "-C", str(self.checkout), "worktree", "add", "--detach", str(self.worktree), sha])
            self.shell.run(["bun", "install", "--cwd", str(self.worktree / "infra" / "ci"), "--frozen-lockfile"])
        head = self.shell.run(["git", "-C", str(self.worktree), "rev-parse", "HEAD"]).strip()
        if head != sha:
            raise EffectFailed(f"activation worktree {self.worktree} is at {head}, not the landed {sha}")
        return self.worktree

    def drifted(self, tree: Path) -> bool:
        code, out = self.shell.call(["bun", "infra/ci/src/cli.ts", "sync", self.pipeline], cwd=tree)
        if code == 0 and "match Buildkite" in out:
            return False
        if code != 0 and "drifted" in out:
            return True
        raise EffectFailed(f"ci sync {self.pipeline} dry run exited {code}: {out.strip()[-300:]}")

    def apply(self, tree: Path) -> list[str]:
        code, out = self.shell.call(["bun", "infra/ci/src/cli.ts", "sync", self.pipeline, "--apply"], cwd=tree)
        if code != 0:
            raise EffectFailed(f"ci sync {self.pipeline} --apply exited {code}: {out.strip()[-300:]}")
        return [line.strip() for line in out.splitlines() if "applied" in line]


@dataclass
class Comms:
    shell: Shell
    bus: str
    repo: Path
    sender: str
    comms_lane: str
    root_lane: str
    topic: str

    def bus_cli(self, *args: str) -> str:
        return self.shell.run([sys.executable, str(SCRIPTS / "bus.py"), *args, "--bus", self.bus, "--repo", str(self.repo)])

    def post(self, kind: str, text: str, to: str | None, topic: str | None = None) -> int:
        out = self.bus_cli("post", "--from", self.sender, "--kind", kind, "--topic", topic or self.topic, "--text", text, *(["--to", to] if to else []))
        return int(out.split()[0].lstrip("#"))

    def event(self, event: str, facts: dict, grant: str) -> int:
        return self.post("ask", json.dumps({"event": event, "grant": grant, **facts}), self.comms_lane)

    def find(self, event: str) -> int | None:
        entries = json.loads(self.bus_cli("read", "--lane", self.comms_lane, "--all", "--peek", "--json"))
        mine = (entry for entry in entries if entry["from"] == self.sender and entry["kind"] == "ask" and self.comms_lane in entry["to"])
        return next((entry["seq"] for entry in mine if json.loads(entry["text"]).get("event") == event), None)

    def posted(self, seq: int) -> str | None:
        entries = json.loads(self.bus_cli("read", "--lane", self.sender, "--kind", "answer", "--all", "--peek", "--json"))
        return next((match["ts"] for entry in entries if entry["re"] == seq and (match := POSTED.search(entry["text"]))), None)

    def ask_root(self, text: str) -> int:
        return self.post("ask", text, self.root_lane)

    def report(self, text: str) -> int:
        return self.post("decision", text, self.root_lane)

    def fence(self, text: str) -> int:
        return self.post("decision", text, None, topic="fence")


@dataclass
class Records:
    shell: Shell
    repo: Path

    def ccn(self, *args: str) -> object:
        return json.loads(self.shell.run(["ccn", "-R", str(self.repo), *args, "--json"]))

    def open(self, incident_id: str, title: str, premise: str) -> dict:
        label = f"incident:{incident_id}"
        found = self.ccn("investigation", "list", "--all", "--label", label)
        investigation = found[0]["id"][:7] if found else self.ccn("investigation", "open", title, "--body", premise, "--label", "incident", "--label", label)["id"][:7]
        logs = self.ccn("log", "list", "--label", label)
        log = logs[0]["id"][:7] if logs else self.ccn("log", "add", f"incident {incident_id}", "--label", "incident", "--label", label, "--entry", f"investigation {investigation}: {title}")["id"][:7]
        return {"investigation": investigation, "log": log}

    def entry(self, log: str, text: str, attach: Path | None = None) -> None:
        self.ccn("log", "append", log, "--entry", text, *(["--attach", str(attach), "--replace"] if attach else []))

    def verdict(self, investigation: str, verb: str, text: str, commit: str | None = None) -> None:
        self.ccn("investigation", verb, investigation, text, *(["--commit", commit] if commit else []))


@dataclass
class World:
    orca: Orca
    github: GitHub
    buildkite: Buildkite
    activation: Activation
    comms: Comms
    records: Records

    @classmethod
    def live(cls, incident: Incident, shell: Shell) -> World:
        facts = incident.facts
        checkout = Path(facts["checkout"])
        worktree = Path.home() / ".claude" / "worktrees" / checkout.name / f"incident-{incident.incident_id}-activation"
        return cls(
            Orca(shell, facts.get("orca_run"), facts.get("orca_repo")),
            GitHub(shell, facts["repo"], facts["trunk"]),
            Buildkite(shell, facts["pipeline"]),
            Activation(shell, checkout, facts["pipeline"], worktree),
            Comms(shell, facts["bus"], checkout, sender(incident.incident_id), facts["comms_lane"], facts["root_lane"], topic(incident.incident_id)),
            Records(shell, checkout),
        )


def sender(incident_id: str) -> str:
    return f"incident-{incident_id}"


def topic(incident_id: str) -> str:
    return f"incident:{incident_id}"


def lane_name(incident_id: str, role: str) -> str:
    return f"incident-{incident_id}-{role}"


def attempts(incident: Incident, base: str) -> list[Action]:
    return [action for key, action in incident.actions.items() if key == base or key.startswith(f"{base}#")]


def latest(incident: Incident, base: str) -> Action | None:
    found = attempts(incident, base)
    return found[-1] if found else None


def retryable(action: Action | None) -> bool:
    return action is None or (action.status == "failed" and (action.reason or "").startswith(RECONCILED) and not action.action_id.endswith(f"#{MAX_ATTEMPTS}"))


def next_id(incident: Incident, base: str) -> str:
    count = len(attempts(incident, base))
    return base if count == 0 else f"{base}#{count + 1}"


class Runner:
    def __init__(self, store: Store, incident_id: str, world: World, shell: Shell, briefs: Path | None = None):
        self.store, self.incident_id, self.world, self.shell = store, incident_id, world, shell
        self.generation = store.load(incident_id).owner_generation
        self.briefs = briefs or store.root / incident_id

    def owned(self):
        return self.store.owned(self.incident_id, self.generation)

    def now(self) -> datetime:
        return self.shell.now()

    def tick(self) -> Incident:
        incident = self.store.load(self.incident_id)
        if incident.status == "closed":
            return incident
        self.mark_lost()
        self.intake()
        self.mechanism()
        self.dispatch()
        self.design()
        self.review()
        self.landing()
        self.not_ours()
        self.activate()
        self.confirm_live()
        self.recover()
        self.settle_comms()
        self.exhausted()
        self.deadlines()
        self.finish()
        return self.store.load(self.incident_id)

    def run(self, interval: float) -> Incident:
        while (incident := self.tick()).status != "closed":
            self.shell.sleep(interval)
        return incident

    def decide(self, key: str, text: str) -> None:
        with self.owned() as incident:
            fresh = incident.decide(key, text, self.now())
        if fresh:
            self.world.comms.ask_root(f"{self.incident_id}: {text}")

    def resolve(self, key: str) -> None:
        with self.owned() as incident:
            incident.resolve(key, self.now())

    def milestone(self, name: str, text: str, status: str | None = None, facts: dict | None = None) -> bool:
        with self.owned() as incident:
            fresh = incident.milestone(name, text, self.now())
            incident.status = status or incident.status
            incident.facts |= facts or {}
        if fresh:
            self.world.comms.report(f"{self.incident_id} {name} at {pacific(self.now())}: {text}")
            self.record(name, text)
        return fresh

    def keep(self, key: str, call) -> None:
        try:
            call()
        except (EffectFailed, *LOST) as failure:
            self.decide(f"record:{key}", f"cc-notes refused the incident record ({key}): {failure}")

    def record(self, name: str, text: str) -> None:
        facts = self.store.load(self.incident_id).facts
        if not (records := facts.get("records")):
            return
        self.keep(name, lambda: self.world.records.entry(records["log"], f"{name} at {pacific(self.now())}: {text}"))
        verdicts = {"mechanism": ("root-cause", None), "landed": ("fix", facts.get("landed_sha")), "not-ours": ("exonerate", None)}
        if name in verdicts:
            verb, commit = verdicts[name]
            self.keep(f"{name}-verdict", lambda: self.world.records.verdict(records["investigation"], verb, text, commit))

    def open_records(self) -> None:
        facts = self.store.load(self.incident_id).facts
        if facts.get("records"):
            return
        title = f"Incident {self.incident_id}: {facts['kind']} outage on {facts['target']}"
        premise = f"{facts['alert']} reports a {facts['kind']} outage on {facts['code_path']} since {pacific(facts['onset'])}; thread {facts['thread']}."
        try:
            records = self.world.records.open(self.incident_id, title, premise)
        except (EffectFailed, *LOST) as failure:
            self.decide("record:open", f"cc-notes refused the incident record: {failure}; lanes launch without record ids")
            return
        with self.owned() as incident:
            incident.facts["records"] = records

    def effect(self, action_id: str, kind: str, target: str, authority: str | None, call, deadline: timedelta | None = None) -> dict | None:
        now = self.now()
        with self.owned() as incident:
            if incident.accept(action_id, kind, target, authority, now).status != "accepted":
                return None
            incident.start(action_id, now, now + deadline if deadline else None)
        try:
            response = call()
        except EffectFailed as failure:
            with self.owned() as incident:
                incident.fail(action_id, str(failure))
            self.decide(f"failed:{action_id}", f"{kind} {target} failed: {failure}")
            return None
        except LOST:
            return None
        with self.owned() as incident:
            incident.complete(action_id, response, dispatch_id=str(response.get("dispatch") or response.get("seq") or "") or None)
        return response

    def exhausted(self) -> None:
        incident = self.store.load(self.incident_id)
        for action in incident.actions.values():
            if action.status == "failed" and action.action_id.endswith(f"#{MAX_ATTEMPTS}"):
                self.decide(f"exhausted:{action.action_id}", f"{action.kind} {action.target} failed {MAX_ATTEMPTS} times: {action.reason}")

    def mark_lost(self) -> None:
        with self.owned() as incident:
            for action in incident.actions.values():
                if action.status == "started":
                    incident.lose(action.action_id)

    def grant(self, name: str) -> str | None:
        incident = self.store.load(self.incident_id)
        if ref := incident.facts["grants"].get(name):
            self.resolve(f"grant:{name}")
            return ref
        self.decide(f"grant:{name}", f"needs the {name} grant: incident.py grant --incident {self.incident_id} --name {name} --ref <grant id>")
        return None

    def comms(self, event: str, facts: dict, surface: str = "thread") -> None:
        base = f"comms:{event}"
        current = latest(self.store.load(self.incident_id), base)
        if current and current.status == "unverifiable":
            seq = self.world.comms.find(event)
            with self.owned() as incident:
                if seq:
                    incident.complete(current.action_id, {"seq": seq}, dispatch_id=str(seq))
                else:
                    incident.fail(current.action_id, f"{RECONCILED} no {event} post on the bus")
            current = latest(self.store.load(self.incident_id), base)
        if not retryable(current) or not (ref := self.grant(surface)):
            return
        incident = self.store.load(self.incident_id)
        payload = {"surface": surface, "thread": incident.facts["thread"], **facts}
        self.effect(next_id(incident, base), "comms", f"{surface}:{event}", ref, lambda: {"seq": self.world.comms.event(event, payload, ref)}, COMMS_DEADLINE)

    def intake(self) -> None:
        self.open_records()
        incident = self.store.load(self.incident_id)
        if self.milestone("opened", f"{incident.facts['kind']} outage from {incident.facts['thread']}; executor owns it"):
            self.world.comms.fence(f"fence {incident.facts['target']} from applies and deploys except {self.lane('fix')}")
        self.comms("ack", {"reaction": "eyes", "onset": pacific(incident.facts["onset"])})

    def mechanism(self) -> None:
        if mechanism := self.store.load(self.incident_id).facts.get("mechanism"):
            self.milestone("mechanism", mechanism)

    def lane(self, role: str) -> str:
        return self.store.load(self.incident_id).facts["adopted"].get(role) or lane_name(self.incident_id, role)

    def launch(self, role: str, section: str) -> None:
        base = f"dispatch:{role}"
        lane = self.lane(role)
        current = latest(self.store.load(self.incident_id), base)
        if current is None and role in self.store.load(self.incident_id).facts["adopted"]:
            now = self.now()
            with self.owned() as incident:
                incident.accept(base, "dispatch", lane, "adopted", now)
                incident.start(base, now)
                incident.complete(base, {"lane": lane, "adopted": True})
                incident.verify(base, {"lane": lane, "adopted": True})
            return
        if current and current.status == "unverifiable":
            receipt = self.world.orca.receipt(lane)
            if not receipt:
                self.decide(f"launch:{current.action_id}", f"launch of {lane} lost its response and left no receipt; check Orca, then `incident.py note` its outcome")
                return
            with self.owned() as incident:
                incident.complete(current.action_id, receipt, dispatch_id=receipt["dispatch"])
                incident.verify(current.action_id, receipt)
            return
        if current and current.status == "completed":
            with self.owned() as incident:
                incident.verify(current.action_id, current.response)
            return
        if not retryable(current):
            return
        incident = self.store.load(self.incident_id)
        model, effort = incident.facts["routes"][role]
        assert_route(role, model)
        brief = self.write_brief(incident, role, section)
        action_id = next_id(incident, base)
        if response := self.effect(action_id, "dispatch", lane, "R16", lambda: self.world.orca.launch(lane, model, effort, brief)):
            with self.owned() as incident:
                incident.verify(action_id, response)

    def write_brief(self, incident: Incident, role: str, section: str) -> Path:
        facts = incident.facts
        records = facts.get("records") or {}
        values = {
            "investigation id": records.get("investigation", "n/a"),
            "incident log id": records.get("log", "n/a"),
            "fix lane name": self.lane("fix"),
            "evidence lane name": self.lane("evidence"),
            "comms lane name": facts["comms_lane"],
            "root agent name": sender(self.incident_id),
            "bus id": facts["bus"],
            "incident topic": topic(self.incident_id),
            "incident id": self.incident_id,
            "alert link": facts["alert"],
            "named code path": facts["code_path"],
            "runbook": facts.get("runbook") or "n/a",
            "submit skill": "submit-pr",
            "break-glass skill": "break-glass",
            "design rulings": "\n  ".join(facts.get("rulings") or ["none recorded for this subsystem"]),
            "entry point": facts.get("entry_point") or "none named",
        }
        self.briefs.mkdir(parents=True, exist_ok=True)
        path = self.briefs / f"{lane_name(self.incident_id, role)}.full.md"
        common = Path(facts["common"]).read_text() + "\n" if facts.get("common") else ""
        path.write_text(common + render_brief(section, values))
        if records:
            self.keep(f"brief:{path.stem}", lambda: self.world.records.entry(records["log"], f"brief {path.name.removesuffix('.full.md')}", path))
        return path

    def dispatch(self) -> None:
        incident = self.store.load(self.incident_id)
        if incident.facts.get("repair_pr"):
            return
        self.launch("fix", "Fix lane brief")
        self.launch("evidence", "Evidence lane brief")
        fix = latest(self.store.load(self.incident_id), "dispatch:fix")
        incident = self.store.load(self.incident_id)
        quiet = not any(incident.facts.get(fact) for fact in ("mechanism", "repair_pr", "not_ours"))
        if fix and fix.status == "verified" and quiet and self.now() - parse_stamp(fix.started_at) >= BACKUP_AFTER:
            self.launch("backup", "Fix lane brief")

    def design(self) -> None:
        facts = self.store.load(self.incident_id).facts
        if not facts.get("rulings"):
            return
        check = facts.get("design_check")
        if facts.get("design_ok"):
            self.resolve("design")
            self.milestone("design", f"confirmed: {check or f'#{facts['repair_pr']} as opened'}")
            return
        confirm = f"confirm it calls {facts.get('entry_point') or 'the agreed entry point'} and meets {'; '.join(facts['rulings'])} with `incident.py note --incident {self.incident_id} --design-ok`, or redirect the fix lane"
        if check:
            self.decide("design", f"design check before the PR: {check}; {confirm}")
        elif pr := facts.get("repair_pr"):
            self.decide("design", f"#{pr} opened with no design check; hold it, then {confirm}")

    def review(self) -> None:
        incident = self.store.load(self.incident_id)
        if not (pr := incident.facts.get("repair_pr")):
            return
        url = f"https://github.com/{incident.facts['repo']}/pull/{pr}"
        self.milestone("pr", url)
        self.comms("pr", {"pr": url, "mechanism": incident.facts.get("mechanism")})
        if incident.facts["human_review"]:
            self.comms("review-request", {"pr": url, "why": "the reviewer is the broken pipeline, so its fix needs a human approval"}, incident.facts["review_surface"])

    def landing(self) -> None:
        incident = self.store.load(self.incident_id)
        if not (pr := incident.facts.get("repair_pr")) or incident.facts.get("landed_sha"):
            return
        if not (sha := self.world.github.landed(pr)):
            return
        with self.owned() as incident:
            incident.facts["landed_sha"] = sha
            incident.status = "activation_pending"
            incident.accept(f"land:{pr}", "land", f"#{pr}", None, self.now())
            incident.verify(f"land:{pr}", {"sha": sha})
        self.milestone("landed", f"#{pr} landed as {sha[:10]}; not live until {incident.facts['target']} is verified")
        self.comms("landed", {"pr": pr, "sha": sha, "live": False})

    def config_matches(self, tree: Path) -> dict | None:
        incident = self.store.load(self.incident_id)
        if self.world.activation.drifted(tree):
            return None
        configuration = self.world.buildkite.configuration()
        expected = incident.facts.get("expect_config")
        if expected and expected not in configuration:
            return None
        return {"digest": hashlib.sha256(configuration.encode()).hexdigest(), "expected": expected, "at": stamp(self.now())}

    def activate(self) -> None:
        incident = self.store.load(self.incident_id)
        if not incident.facts["pipeline"] or not (sha := incident.facts.get("landed_sha")) or incident.reached("activated"):
            return
        base = f"sync:{incident.facts['pipeline']}@{sha[:12]}"
        try:
            tree = self.world.activation.prepare(sha)
            current = latest(incident, base)
            if current and current.status == "verified":
                self.activated(current.action_id, current.authority_ref, current.verification_receipt)
                return
            if current and current.status in ("completed", "unverifiable"):
                self.read_back(current, tree)
                return
            if not retryable(current):
                return
            if receipt := self.config_matches(tree):
                self.activated(next_id(incident, base), None, receipt)
                return
            if not (ref := self.grant("sync")):
                return
            action_id = next_id(incident, base)
            if self.effect(action_id, "sync", incident.facts["pipeline"], ref, lambda: {"applied": self.world.activation.apply(tree)}, SYNC_DEADLINE):
                self.read_back(self.store.load(self.incident_id).action(action_id), tree)
        except EffectFailed as failure:
            self.decide(f"activation:{sha}", f"activation blocked: {failure}")

    def read_back(self, action: Action, tree: Path) -> None:
        if receipt := self.config_matches(tree):
            self.activated(action.action_id, action.authority_ref, receipt)
        elif action.status == "unverifiable":
            with self.owned() as incident:
                incident.fail(action.action_id, f"{RECONCILED} stored configuration still drifts")
        else:
            self.decide(f"activation:{action.action_id}", f"applied {action.target} but the read-back still drifts")

    def activated(self, action_id: str, authority: str | None, receipt: dict) -> None:
        with self.owned() as incident:
            if incident.accept(action_id, "sync", incident.facts["pipeline"], authority, self.now()).status != "verified":
                incident.verify(action_id, receipt)
            incident.status = "recovering"
            incident.facts.setdefault("activated_at", receipt["at"])
            text = f"{incident.facts['pipeline']} configuration matches the landed tree (sha256 {receipt['digest'][:12]})"
            fresh = incident.milestone("activated", text, self.now())
        if fresh:
            self.world.comms.report(f"{self.incident_id} activated at {pacific(self.now())}: {text}")
            self.record("activated", text)

    def unfinished(self) -> list[int]:
        incident = self.store.load(self.incident_id)
        window = (incident.facts["onset"], incident.facts["activated_at"])
        return [build["number"] for build in self.world.buildkite.unfinished(*window)]

    def inventory(self) -> list[dict]:
        incident = self.store.load(self.incident_id)
        until = incident.facts["activated_at"]
        failed = [build for build in self.world.buildkite.failed(incident.facts["onset"], until) if build.get("pull_request")]
        by_pr: dict[int, list[dict]] = {}
        for build in failed:
            by_pr.setdefault(int(build["pull_request"]["id"]), []).append(build)
        prs = self.world.github.prs(sorted(by_pr))
        entries = []
        for pr in sorted(by_pr):
            info = prs.get(pr)
            at_head = [build for build in by_pr[pr] if info and build["commit"] == info["head"]]
            latest_build = max(by_pr[pr], key=lambda build: build["number"])
            if not info or info["state"] != "OPEN":
                entries.append({"pr": pr, "account": "closed", "source": latest_build["number"]})
            elif not at_head:
                entries.append({"pr": pr, "account": "superseded", "source": latest_build["number"]})
            else:
                source = max(at_head, key=lambda build: build["number"])
                entries.append({"pr": pr, "account": "candidate", "source": source["number"], "head": info["head"], "url": info["url"]})
        return entries

    def recover(self) -> None:
        incident = self.store.load(self.incident_id)
        if not incident.reached("activated") or incident.reached("recovered"):
            return
        entries = self.inventory()
        self.reconcile_rebuilds()
        incident = self.store.load(self.incident_id)
        candidates = {rebuild_key(entry): entry for entry in entries if entry["account"] == "candidate"}
        pending = [entry for key, entry in candidates.items() if retryable(latest(incident, key))]
        canary = incident.facts.get("canary")
        if canary and canary not in candidates and retryable(latest(incident, canary)):
            canary = None
        if canary is None:
            if pending and self.grant("rebuild"):
                with self.owned() as incident:
                    incident.facts["canary"] = rebuild_key(pending[0])
                self.rebuild(pending[0])
            elif not pending and not self.unfinished():
                self.milestone("live", f"{incident.facts['pipeline']} configuration verified; no failed work to re-run")
                self.comms("live", {"canary": None, "at": pacific(self.store.load(self.incident_id).reached("live")["at"])})
                self.account(entries)
            return
        if retryable(latest(incident, canary)):
            self.rebuild(candidates[canary])
            return
        if not self.canary_passed(canary):
            return
        for entry in pending:
            self.rebuild(entry)
        if not self.unfinished():
            self.account(entries)

    def rebuild(self, entry: dict) -> None:
        if not (ref := self.grant("rebuild")):
            return
        key = rebuild_key(entry)
        with self.owned() as incident:
            incident.facts["sources"][key] = entry["source"]
            action_id = next_id(incident, key)

        def call() -> dict:
            build = self.world.buildkite.rebuild(entry["source"])
            return {"build": build["number"], "pr": entry["pr"], "source": entry["source"], "url": build.get("web_url")}

        self.effect(action_id, "rebuild", f"#{entry['pr']}@{entry['head'][:10]}", ref, call, REBUILD_DEADLINE)

    def reconcile_rebuilds(self) -> None:
        incident = self.store.load(self.incident_id)
        open_rebuilds = [action for action in incident.of_kind("rebuild") if action.status in ("completed", "unverifiable")]
        if not open_rebuilds:
            return
        builds = self.world.buildkite.since(incident.facts["activated_at"])
        by_number = {build["number"]: build for build in builds}
        by_source: dict[int, dict] = {}
        for build in sorted(builds, key=lambda build: build["number"]):
            if build.get("rebuilt_from"):
                by_source.setdefault(build["rebuilt_from"]["number"], build)
        with self.owned() as incident:
            for action in (incident.action(stale.action_id) for stale in open_rebuilds):
                if action.status == "unverifiable":
                    source = incident.facts["sources"][action.action_id.split("#")[0]]
                    if not (found := by_source.get(source)):
                        incident.fail(action.action_id, f"{RECONCILED} no rebuild of build {source}")
                        continue
                    pr = int(action.action_id.split(":")[1].split("@")[0])
                    incident.complete(action.action_id, {"build": found["number"], "pr": pr, "source": source, "url": found.get("web_url")})
                build = by_number.get(action.response["build"])
                if build and build["state"] in TERMINAL_BUILDS:
                    incident.verify(action.action_id, {"build": build["number"], "state": build["state"], "jobs": job_states(build)})

    def canary_passed(self, key: str) -> bool:
        incident = self.store.load(self.incident_id)
        action = latest(incident, key)
        if action is None or action.status in ("accepted", "started", "unverifiable"):
            return False
        if action.status == "failed":
            self.decide(f"canary:{key}", f"canary rebuild {key} failed to start: {action.reason}")
            return False
        build = self.find_build(action.response["build"])
        job = next((job for job in build["jobs"] if incident.facts["canary_job"] in (job.get("name") or "")), None)
        if job is None and build["state"] in TERMINAL_BUILDS:
            self.decide(f"canary:{key}", f"canary build {build['number']} finished {build['state']} without a {incident.facts['canary_job']} job")
            return False
        if job is None or job["state"] not in TERMINAL_JOBS:
            return False
        if job["state"] != GREEN:
            self.decide(f"canary:{key}", f"canary build {build['number']} {incident.facts['canary_job']} is {job['state']}: the landed fix is not working")
            return False
        self.milestone("live", f"canary {build.get('web_url') or build['number']} passed {incident.facts['canary_job']}")
        self.comms("live", {"canary": build.get("web_url") or build["number"], "at": pacific(self.store.load(self.incident_id).reached("live")["at"])})
        return True

    def not_ours(self) -> None:
        incident = self.store.load(self.incident_id)
        if not (verdict := incident.facts.get("not_ours")) or incident.status in ("recovered", "closed"):
            return
        self.milestone("not-ours", verdict, status="recovered", facts={"accounting": {"not_ours": verdict}})

    def confirm_live(self) -> None:
        incident = self.store.load(self.incident_id)
        if incident.facts["pipeline"] or incident.facts.get("not_ours") or not (evidence := incident.facts.get("live")):
            return
        self.milestone("live", evidence)
        self.milestone("recovered", evidence, status="recovered", facts={"accounting": {"live": evidence}})
        self.comms("live", {"evidence": evidence, "at": pacific(self.store.load(self.incident_id).reached("live")["at"])})

    def find_build(self, number: int) -> dict:
        incident = self.store.load(self.incident_id)
        return next(build for build in self.world.buildkite.since(incident.facts["activated_at"]) if build["number"] == number)

    def account(self, entries: list[dict]) -> None:
        incident = self.store.load(self.incident_id)
        rebuilds = incident.of_kind("rebuild")
        if any(action.status not in ("verified", "failed") for action in rebuilds):
            return
        if any(entry["account"] == "candidate" and retryable(latest(incident, rebuild_key(entry))) for entry in entries):
            return
        settled = [action for action in rebuilds if action.status == "verified"]
        failed = [action for action in rebuilds if action.status == "failed" and not retryable(action)]
        green = [action for action in settled if action.verification_receipt["state"] == GREEN]
        red = [action for action in settled if action.verification_receipt["state"] != GREEN]
        accounting = {
            "rerun": len(settled),
            "green": len(green),
            "red": [{"pr": action.response["pr"], "build": action.response["build"], "state": action.verification_receipt["state"]} for action in red],
            "unlaunched": [{"key": action.action_id, "reason": action.reason} for action in failed],
            "skipped": [{"pr": entry["pr"], "account": entry["account"]} for entry in entries if entry["account"] != "candidate"],
        }
        self.milestone("recovered", f"re-run {accounting['rerun']}, green {accounting['green']}, red {len(accounting['red'])}", status="recovered", facts={"accounting": accounting})

    def settle_comms(self) -> None:
        incident = self.store.load(self.incident_id)
        for action in incident.of_kind("comms"):
            if action.status == "completed" and (ts := self.world.comms.posted(int(action.dispatch_id))):
                with self.owned() as incident:
                    incident.verify(action.action_id, {"ts": ts})

    def deadlines(self) -> None:
        incident = self.store.load(self.incident_id)
        for action in incident.actions.values():
            if action.overdue(self.now()):
                self.decide(f"overdue:{action.action_id}", f"{action.kind} {action.target} is overdue: started {pacific(action.started_at)}, due {pacific(action.deadline)}, still {action.status}")

    def finish(self) -> None:
        incident = self.store.load(self.incident_id)
        if incident.status != "recovered":
            return
        accounting = dict(incident.facts["accounting"])
        repo = incident.facts["repo"]
        if "red" in accounting:
            accounting["red"] = [{"pr": f"https://github.com/{repo}/pull/{entry['pr']}", "state": entry["state"]} for entry in accounting["red"]]
        live = incident.reached("live")
        self.comms("recovered", {**accounting, **({"live_at": pacific(live["at"])} if live else {})})
        final = latest(self.store.load(self.incident_id), "comms:recovered")
        if final and final.status == "verified":
            with self.owned() as incident:
                incident.status = "closed"
            self.milestone("closed", f"final reply posted ts={final.verification_receipt['ts']}")


def rebuild_key(entry: dict) -> str:
    return f"rebuild:{entry['pr']}@{entry['head'][:12]}"


def job_states(build: dict) -> dict:
    return {job.get("name") or job.get("step_key") or job["id"]: job["state"] for job in build.get("jobs", [])}


def adopt_pair(value: str) -> tuple[str, str]:
    role, _, lane = value.partition("=")
    if role not in ADOPTABLE or not lane:
        raise argparse.ArgumentTypeError(f"{value!r} is not ROLE=LANE with ROLE one of {', '.join(ADOPTABLE)}")
    return role, lane


def grant_pair(value: str) -> tuple[str, str]:
    name, _, ref = value.partition("=")
    if name not in GRANTS or not ref:
        raise argparse.ArgumentTypeError(f"{value!r} is not NAME=REF with NAME one of {', '.join(GRANTS)}")
    return name, ref


def cmd_open(args: argparse.Namespace, store: Store, shell: Shell) -> int:
    now = shell.now()
    incident_id = args.incident or now.strftime("%m%d%H%M")
    profile = PROFILES[args.kind]
    pipeline = args.pipeline or profile["pipeline"]
    if not (target := args.target or pipeline):
        raise SystemExit(f"--kind {args.kind} names no pipeline; pass --target, the service or stack the alert is on")
    facts = {
        "kind": args.kind,
        "pipeline": pipeline,
        "target": target,
        "canary_job": profile["canary_job"],
        "human_review": profile["human_review"],
        "review_surface": profile["review_surface"],
        "repo": args.repo,
        "trunk": args.trunk,
        "checkout": str(args.checkout.resolve()),
        "thread": args.thread,
        "alert": args.alert or args.thread,
        "code_path": args.code_path or (f"the {pipeline} pipeline" if pipeline else f"the {target} service"),
        "runbook": args.runbook,
        "adopted": dict(args.adopt),
        "onset": args.onset,
        "bus": args.bus,
        "comms_lane": args.comms_lane,
        "root_lane": args.root_lane,
        "grants": dict(args.grant),
        "routes": {role: list(route) for role, route in ROUTES.items()},
        "expect_config": args.expect_config,
        "repair_pr": args.repair_pr,
        "orca_run": args.orca_run,
        "orca_repo": args.orca_repo,
        "common": str(args.common.resolve()) if args.common else None,
        "rulings": args.ruling,
        "entry_point": args.entry_point,
        "sources": {},
    }
    incident = store.create(Incident(incident_id, sender(incident_id), 1, stamp(now), facts))
    print(f"incident {incident.incident_id} at {store.path(incident.incident_id)}; run: incident.py run --incident {incident.incident_id}")
    return 0


def cmd_note(args: argparse.Namespace, store: Store, shell: Shell) -> int:
    with store.inputs(args.incident) as incident:
        if args.pr is not None:
            incident.facts["repair_pr"] = args.pr
        if args.mechanism:
            incident.facts["mechanism"] = args.mechanism
        if args.live:
            incident.facts["live"] = args.live
        if args.not_ours:
            incident.facts["not_ours"] = args.not_ours
        if args.design_check:
            incident.facts["design_check"] = args.design_check
        if args.design_ok:
            incident.facts["design_ok"] = True
    print(f"noted on {args.incident} at input revision {incident.input_revision}")
    return 0


def cmd_grant(args: argparse.Namespace, store: Store, shell: Shell) -> int:
    with store.inputs(args.incident) as incident:
        incident.facts["grants"][args.name] = args.ref
    print(f"{args.incident} grant {args.name}={args.ref}")
    return 0


def cmd_run(args: argparse.Namespace, store: Store, shell: Shell) -> int:
    store.root.mkdir(parents=True, exist_ok=True)
    with open(store.root / f".{args.incident}.runner", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{args.incident} already has a runner", file=sys.stderr)
            return 2
        record = store.load(args.incident)
        if record.owner != (args.owner or sender(args.incident)):
            print(f"{args.incident} is owned by {record.owner} at generation {record.owner_generation}; run with --owner {record.owner} only as that owner", file=sys.stderr)
            return 3
        runner = Runner(store, args.incident, World.live(record, shell), shell)
        try:
            incident = runner.tick() if args.once else runner.run(args.interval)
        except StaleGeneration as stale:
            print(f"{stale}; this runner no longer owns the incident", file=sys.stderr)
            return 3
    print(f"{incident.incident_id} {incident.status}")
    return 0


def cmd_status(args: argparse.Namespace, store: Store, shell: Shell) -> int:
    incident = store.load(args.incident)
    if args.json:
        print(json.dumps(incident.dump()))
        return 0
    print(f"{incident.incident_id} {incident.status} owner={incident.owner} generation={incident.owner_generation}")
    for entry in incident.milestones:
        print(f"  {pacific(entry['at'])} {entry['name']}: {entry['text']}")
    for decision in incident.open_decisions():
        print(f"  open since {pacific(decision['at'])}: {decision['text']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="incident.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_cmd = subparsers.add_parser("open", help="record an incident for the executor to own")
    open_cmd.add_argument("--kind", required=True, choices=sorted(PROFILES))
    open_cmd.add_argument("--incident", help="the incident id; default the UTC month, day, hour, and minute")
    open_cmd.add_argument("--thread", required=True, help="the Slack permalink the incident answers in")
    open_cmd.add_argument("--onset", required=True, help="ISO time the outage began; failed builds since then are owned")
    open_cmd.add_argument("--bus", required=True)
    open_cmd.add_argument("--comms-lane", required=True)
    open_cmd.add_argument("--root-lane", required=True)
    open_cmd.add_argument("--checkout", required=True, type=Path, help="a checkout of the repo; activation adds its own worktree from it")
    open_cmd.add_argument("--pipeline", help="the Buildkite pipeline whose stored settings the fix changes")
    open_cmd.add_argument("--target", help="what to fence; default the pipeline")
    open_cmd.add_argument("--alert", help="the alert's own link (Sentry issue, monitor); default the thread")
    open_cmd.add_argument("--code-path", help="where the fix lane starts; default the pipeline or target")
    open_cmd.add_argument("--runbook", help="where the fix lane logs a production apply")
    open_cmd.add_argument("--adopt", action="append", default=[], type=adopt_pair, metavar="ROLE=LANE", help="a fix or evidence lane already running; the executor records it instead of launching one")
    open_cmd.add_argument("--repo", default="Forge-AI/monorepo")
    open_cmd.add_argument("--trunk", default="dev")
    open_cmd.add_argument("--grant", action="append", default=[], type=grant_pair, metavar="NAME=REF")
    open_cmd.add_argument("--expect-config", help="text the stored pipeline configuration must carry once live")
    open_cmd.add_argument("--repair-pr", type=int, help="the fix PR, when one already exists; skips the launches")
    open_cmd.add_argument("--orca-run")
    open_cmd.add_argument("--orca-repo")
    open_cmd.add_argument("--common", type=Path, help="the drive's common.md lane contract, prepended to both briefs")
    open_cmd.add_argument("--ruling", action="append", default=[], help="an owner design ruling on the touched subsystem, verbatim with its id; the fix brief quotes it")
    open_cmd.add_argument("--entry-point", help="the symbol, at file:line, that the rulings make the fix call")
    open_cmd.set_defaults(handler=cmd_open)

    note = subparsers.add_parser("note", help="a worker reports the repair PR or the mechanism")
    note.add_argument("--incident", required=True)
    note.add_argument("--pr", type=int)
    note.add_argument("--mechanism")
    note.add_argument("--live", help="evidence the fix is live, for an incident with no pipeline to activate")
    note.add_argument("--not-ours", help="the evidence that the alert is not ours; the incident closes with no fix")
    note.add_argument("--design-check", help="the entry point the fix calls and how it meets each ruling, recorded before the PR opens")
    note.add_argument("--design-ok", action="store_true", help="the root confirms the design check")
    note.set_defaults(handler=cmd_note)

    grant = subparsers.add_parser("grant", help="add the authority a decision asked for")
    grant.add_argument("--incident", required=True)
    grant.add_argument("--name", required=True, choices=GRANTS)
    grant.add_argument("--ref", required=True)
    grant.set_defaults(handler=cmd_grant)

    run = subparsers.add_parser("run", help="own the incident until its final reply posts")
    run.add_argument("--incident", required=True)
    run.add_argument("--interval", type=float, default=INTERVAL)
    run.add_argument("--owner", help="the owner this runner acts as; default the executor that opened the incident")
    run.add_argument("--once", action="store_true")
    run.set_defaults(handler=cmd_run)

    status = subparsers.add_parser("status", help="milestones in Pacific time and open decisions")
    status.add_argument("--incident", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    return parser


def main(argv: list[str] | None = None, store: Store | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, store or Store(), shell or Shell())


if __name__ == "__main__":
    raise SystemExit(main())
