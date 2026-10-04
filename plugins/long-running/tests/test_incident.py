from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import actions
import incident
import pytest
from actions import Incident, StaleGeneration, Store, parse_stamp, stamp
from incident import ResponseLost, RouteRefused, Runner, World

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPLAY = json.loads((FIXTURES / "pr-review-replay.json").read_text())
ONSET = parse_stamp(REPLAY["onset"])
INTAKE = parse_stamp(REPLAY["intake"])
REPAIR = REPLAY["repair"]
PR_OPENED = parse_stamp("2026-10-01T17:35:00Z")
OLD_CONFIG = "steps:\n  - commands:\n      - yarn workspace pr-reviewer build\n"
NEW_CONFIG = "steps:\n  - commands:\n      - bun install --cwd infra\n      - bun infra/generate.ts\n"
ADVANCED_PR = 28945
ADVANCED_SOURCE = 72957
ADVANCED_FROM = "1c7fea72dfa62a07be8a167874c98d1d19467ff2"
ADVANCED_TO = "38cefac65c896f897cbe84037160857ff77c5930"
LATE_PR = 29008
LATE_SOURCE = 72943
GRANTS = {"thread": "g-thread", "channel": "g-channel", "sync": "g-sync", "rebuild": "g-rebuild"}
TICK = timedelta(seconds=30)


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def now(self) -> datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Replay:
    def __init__(self, clock: Clock):
        self.clock = clock
        self.configured = False
        self.apply_changes_config = True
        self.applies: list[str] = []
        self.launches: list[tuple[str, str, str]] = []
        self.receipts: dict[str, dict] = {}
        self.rebuilds: list[int] = []
        self.lose_rebuild: dict[int, str] = {}
        self.lose_post: set[str] = set()
        self.posts: list[dict] = []
        self.asks: list[str] = []
        self.reports: list[str] = []
        self.fences: list[str] = []
        self.entries: list[tuple[str, str, Path | None]] = []
        self.verdicts: list[tuple[str, str, str, str | None]] = []
        self.refuse_records = False
        self.created: dict[int, dict] = {}
        self.outcomes = {row["source"]: row for row in REPLAY["builds"]}
        self.prs: dict[int, dict] = {}
        self.sources: list[dict] = []
        for index, row in enumerate(REPLAY["builds"]):
            closed = row["outcome"] == "failed" and row["final_pr_state"] == "closed"
            self.prs[row["pr"]] = {"state": "CLOSED" if closed else "OPEN", "head": row["commit"], "url": f"https://github.com/Forge-AI/monorepo/pull/{row['pr']}"}
            created = ONSET + timedelta(seconds=30 * index)
            self.sources.append({"number": row["source"], "commit": row["commit"], "pr": row["pr"], "created": created, "fails_at": created + timedelta(minutes=2)})
        late = next(source for source in self.sources if source["number"] == LATE_SOURCE)
        late["created"] = parse_stamp(REPAIR["landed_at"]) - timedelta(seconds=30)
        late["fails_at"] = late["created"] + timedelta(minutes=3)
        self.sources.append({"number": ADVANCED_SOURCE, "commit": ADVANCED_FROM, "pr": ADVANCED_PR, "created": ONSET + timedelta(minutes=50), "fails_at": ONSET + timedelta(minutes=52)})
        self.advanced = {"at": parse_stamp(REPAIR["landed_at"]), "head": ADVANCED_TO}
        self.prs[ADVANCED_PR]["head"] = ADVANCED_FROM

    def now(self) -> datetime:
        return self.clock.at

    def launch(self, lane: str, model: str, effort: str, brief: Path) -> dict:
        assert brief.exists()
        self.launches.append((lane, model, effort))
        receipt = {"lane": lane, "state": "ready", "task": f"task-{lane}", "dispatch": f"dispatch-{lane}", "terminal": "t1", "worktree": "/w"}
        self.receipts[lane] = receipt
        return receipt

    def receipt(self, lane: str) -> dict | None:
        return self.receipts.get(lane)

    def pr_view(self, number: int) -> dict:
        view = dict(self.prs[number])
        if number == ADVANCED_PR and self.now() >= self.advanced["at"]:
            view["head"] = self.advanced["head"]
        return view

    def gh_prs(self, numbers: list[int]) -> dict[int, dict]:
        return {number: self.pr_view(number) for number in numbers if number in self.prs}

    def landed(self, pr: int) -> str | None:
        assert pr == REPAIR["pr"]
        return REPAIR["sha"] if self.now() >= parse_stamp(REPAIR["landed_at"]) else None

    def configuration(self) -> str:
        return NEW_CONFIG if self.configured else OLD_CONFIG

    def source_state(self, source: dict) -> str:
        if self.now() < source["fails_at"]:
            return "running"
        return "failed"

    def failed(self, since: str, until: str) -> list[dict]:
        low, high = parse_stamp(since), parse_stamp(until)
        return [
            {"number": source["number"], "commit": source["commit"], "state": "failed", "pull_request": {"id": str(source["pr"])}, "created_at": stamp(source["created"])}
            for source in self.sources
            if low <= source["created"] <= high and self.source_state(source) == "failed"
        ]

    def unfinished(self, since: str, until: str) -> list[dict]:
        low, high = parse_stamp(since), parse_stamp(until)
        return [{"number": source["number"], "state": "running"} for source in self.sources if low <= source["created"] <= high and self.source_state(source) == "running"]

    def since(self, since: str) -> list[dict]:
        low = parse_stamp(since)
        return [self.view(build) for build in self.created.values() if build["created"] >= low]

    def view(self, build: dict) -> dict:
        elapsed = self.now() - build["created"]
        state = build["outcome"] if elapsed >= timedelta(minutes=3) else "running"
        prepare = "passed" if elapsed >= timedelta(minutes=1) else "running"
        jobs = [{"id": "j1", "name": ":robot_face: Prepare PR evidence", "state": prepare}, *({"id": f"j{i}", "name": job["name"], "state": job["state"] if state != "running" else "waiting"} for i, job in enumerate(REPLAY["canary_jobs"][1:], 2))]
        return {"number": build["number"], "state": state, "commit": build["commit"], "rebuilt_from": {"number": build["source"]}, "web_url": f"https://buildkite.com/forge/pr-review/builds/{build['number']}", "jobs": jobs}

    def rebuild(self, number: int) -> dict:
        assert self.configured, "a rebuild fired while the stored configuration was old"
        self.rebuilds.append(number)
        mode = self.lose_rebuild.pop(number, None)
        if mode == "before":
            raise ResponseLost(f"rebuild {number} timed out before Buildkite took it")
        outcome = self.outcomes.get(number)
        source = next(source for source in self.sources if source["number"] == number)
        rebuilt = outcome["rebuild"] if outcome else 80000 + len(self.created)
        self.created[rebuilt] = {"number": rebuilt, "source": number, "commit": source["commit"], "created": self.now(), "outcome": outcome["outcome"] if outcome else "passed"}
        if mode == "after":
            raise ResponseLost(f"rebuild {number} response lost")
        return self.view(self.created[rebuilt])

    def prepare(self, sha: str) -> Path:
        assert sha == REPAIR["sha"]
        return Path("/activation")

    def drifted(self, tree: Path) -> bool:
        return not self.configured

    def apply(self, tree: Path) -> list[str]:
        self.applies.append(stamp(self.now()))
        self.configured = self.configured or self.apply_changes_config
        return ["applied patched pipeline pr-review"]

    def event(self, event: str, facts: dict, grant: str) -> int:
        self.posts.append({"event": event, "grant": grant, "facts": facts, "at": self.now(), "seq": len(self.posts) + 1})
        if event in self.lose_post:
            self.lose_post.discard(event)
            raise ResponseLost(f"bus post {event} lost")
        return len(self.posts)

    def find(self, event: str) -> int | None:
        return next((post["seq"] for post in self.posts if post["event"] == event), None)

    def posted(self, seq: int) -> str | None:
        post = self.posts[seq - 1]
        return f"{post['at'].timestamp() + 20:.6f}" if self.now() - post["at"] >= timedelta(seconds=20) else None

    def ask_root(self, text: str) -> int:
        self.asks.append(text)
        return len(self.asks)

    def report(self, text: str) -> int:
        self.reports.append(text)
        return len(self.reports)

    def fence(self, text: str) -> int:
        self.fences.append(text)
        return len(self.fences)

    def open_records(self, incident_id: str, title: str, premise: str) -> dict:
        if self.refuse_records:
            raise ResponseLost("ccn: malformed refspec")
        self.premise = premise
        return {"investigation": "inv1234", "log": "log1234"}

    def record_entry(self, log: str, text: str, attach: Path | None = None) -> None:
        self.entries.append((log, text, attach))

    def record_verdict(self, investigation: str, verb: str, text: str, commit: str | None = None) -> None:
        self.verdicts.append((investigation, verb, text, commit))


class Adapter:
    def __init__(self, fake: Replay, **methods):
        self.fake = fake
        for name, method in methods.items():
            setattr(self, name, method)


def world_of(fake: Replay) -> World:
    return World(
        orca=Adapter(fake, launch=fake.launch, receipt=fake.receipt),
        github=Adapter(fake, prs=fake.gh_prs, landed=fake.landed),
        buildkite=Adapter(fake, configuration=fake.configuration, failed=fake.failed, unfinished=fake.unfinished, since=fake.since, rebuild=fake.rebuild),
        activation=Adapter(fake, prepare=fake.prepare, drifted=fake.drifted, apply=fake.apply),
        comms=Adapter(fake, event=fake.event, find=fake.find, posted=fake.posted, ask_root=fake.ask_root, report=fake.report, fence=fake.fence),
        records=Adapter(fake, open=fake.open_records, entry=fake.record_entry, verdict=fake.record_verdict),
    )


@pytest.fixture
def clock() -> Clock:
    return Clock(INTAKE)


@pytest.fixture
def fake(clock) -> Replay:
    return Replay(clock)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "incidents")


def open_incident(store: Store, clock: Clock, grants: dict | None = None, kind: str = "pr-review", **facts) -> str:
    argv = [
        "open", "--kind", kind, "--incident", "pr-review-1001", *(["--target", "api"] if kind == "alert" else []),
        "--thread", "https://forge-ai.slack.com/archives/C0BQSADC7SS/p1790875497000000",
        "--onset", REPLAY["onset"], "--bus", "bus1", "--comms-lane", "incident-comms", "--root-lane", "release-v3",
        "--checkout", "/monorepo", "--expect-config", "infra/generate.ts", "--orca-run", "run1", "--orca-repo", "repo1",
    ]
    for name, ref in (GRANTS if grants is None else grants).items():
        argv += ["--grant", f"{name}={ref}"]
    assert incident.main(argv, store, clock) == 0
    if facts:
        with store.inputs("pr-review-1001") as record:
            record.facts |= facts
    return "pr-review-1001"


def fix_worker(store: Store, clock: Clock, incident_id: str) -> None:
    record = store.load(incident_id)
    if clock.at >= INTAKE + timedelta(minutes=4) and not record.facts.get("mechanism"):
        incident.main(["note", "--incident", incident_id, "--mechanism", "#28934 stopped committing infra/ci/src/generated; prepare never generates it"], store, clock)
    if clock.at >= PR_OPENED and not record.facts.get("repair_pr"):
        incident.main(["note", "--incident", incident_id, "--pr", str(REPAIR["pr"])], store, clock)


def drive(runner: Runner, store: Store, clock: Clock, until: datetime, worker=fix_worker) -> Incident:
    record = store.load(runner.incident_id)
    while clock.at <= until and record.status != "closed":
        worker(store, clock, runner.incident_id)
        record = runner.tick()
        clock.at += TICK
    return record


def rebuild_counts(fake: Replay) -> dict[int, int]:
    counts: dict[int, int] = {}
    for number in fake.rebuilds:
        counts[number] = counts.get(number, 0) + 1
    return counts


def test_replay_reaches_the_final_reply_with_no_root_turn(store, clock, fake):
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(hours=2))

    assert record.status == "closed"
    assert fake.asks == []
    assert [lane for lane, _, _ in fake.launches] == [f"incident-{incident_id}-fix", f"incident-{incident_id}-evidence"]
    assert {model for _, model, _ in fake.launches} == {"sol"}
    assert len(fake.applies) == 1
    assert max(rebuild_counts(fake).values()) == 1
    assert ADVANCED_SOURCE not in fake.rebuilds
    assert LATE_SOURCE in fake.rebuilds
    assert 72923 not in fake.rebuilds
    closed = {pr for pr, view in fake.prs.items() if view["state"] == "CLOSED"}
    assert not closed & {fake.outcomes[number]["pr"] for number in fake.rebuilds}
    assert fake.rebuilds[0] == 72867
    events = [post["event"] for post in fake.posts]
    assert events == ["ack", "pr", "review-request", "landed", "live", "recovered"]
    review = next(post for post in fake.posts if post["event"] == "review-request")
    assert (review["grant"], review["facts"]["surface"]) == ("g-channel", "channel")
    final = fake.posts[-1]["facts"]
    accounting = record.facts["accounting"]
    assert final["rerun"] == accounting["rerun"] == len(fake.rebuilds)
    assert final["green"] == accounting["green"]
    assert {entry["pr"] for entry in accounting["skipped"]} >= closed | {ADVANCED_PR}
    assert final["live_at"].endswith(("am", "pm")) and "Z" not in final["live_at"]
    names = [entry["name"] for entry in record.milestones]
    assert names == ["opened", "mechanism", "pr", "landed", "activated", "live", "recovered", "closed"]


def test_merge_without_a_sync_grant_stays_activation_pending_and_rekicks_nothing(store, clock, fake):
    incident_id = open_incident(store, clock, grants={key: value for key, value in GRANTS.items() if key != "sync"})
    runner = Runner(store, incident_id, world_of(fake), clock)
    record = drive(runner, store, clock, parse_stamp(REPAIR["landed_at"]) + timedelta(minutes=30))

    assert record.status == "activation_pending"
    assert fake.applies == [] and fake.rebuilds == []
    assert [ask for ask in fake.asks if "sync grant" in ask] == [fake.asks[0]] and len(fake.asks) == 1
    assert "landed" in [post["event"] for post in fake.posts]

    incident.main(["grant", "--incident", incident_id, "--name", "sync", "--ref", "g-sync"], store, clock)
    record = drive(runner, store, clock, clock.at + timedelta(hours=1))
    assert record.status == "closed"
    assert store.load(incident_id).open_decisions() == []


def test_old_configuration_after_apply_blocks_the_canary(store, clock, fake):
    fake.apply_changes_config = False
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world_of(fake), clock), store, clock, parse_stamp(REPAIR["landed_at"]) + timedelta(minutes=20))

    assert record.status == "activation_pending"
    assert fake.rebuilds == []
    assert len(fake.applies) == 1
    assert any("read-back still drifts" in ask for ask in fake.asks)


def test_restart_and_duplicate_notes_send_each_command_once(store, clock, fake):
    incident_id = open_incident(store, clock)
    first = Runner(store, incident_id, world_of(fake), clock)
    drive(first, store, clock, parse_stamp(REPAIR["landed_at"]) + timedelta(minutes=2))
    incident.main(["note", "--incident", incident_id, "--pr", str(REPAIR["pr"])], store, clock)
    second = Runner(store, incident_id, world_of(fake), clock)
    record = drive(second, store, clock, clock.at + timedelta(hours=1))

    assert record.status == "closed"
    assert max(rebuild_counts(fake).values()) == 1
    assert len(fake.applies) == 1
    assert len(fake.launches) == 2
    assert len([post for post in fake.posts if post["event"] == "pr"]) == 1


def test_a_lost_rebuild_response_is_reconciled_not_retried(store, clock, fake):
    fake.lose_rebuild[72860] = "after"
    incident_id = open_incident(store, clock)
    runner = Runner(store, incident_id, world_of(fake), clock)
    record = drive(runner, store, clock, INTAKE + timedelta(hours=2))

    assert record.status == "closed"
    assert rebuild_counts(fake)[72860] == 1
    action = store.load(incident_id).actions[next(key for key in record.actions if key.startswith("rebuild:28349@"))]
    assert action.status == "verified" and action.response["build"] == 72978


def test_a_lost_rebuild_that_never_reached_buildkite_is_retried_once_after_reconciling(store, clock, fake):
    fake.lose_rebuild[72860] = "before"
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(hours=2))

    assert record.status == "closed"
    assert rebuild_counts(fake)[72860] == 2
    attempts = [action for key, action in record.actions.items() if key.startswith("rebuild:28349@")]
    assert [action.status for action in attempts] == ["failed", "verified"]
    assert attempts[0].reason.startswith("reconciled:")


def test_a_lost_response_stays_unverifiable_until_reconciled(store, clock, fake):
    fake.lose_rebuild[72860] = "after"
    incident_id = open_incident(store, clock)
    runner = Runner(store, incident_id, world_of(fake), clock)
    while 72860 not in fake.rebuilds:
        fix_worker(store, clock, incident_id)
        runner.tick()
        clock.at += TICK
    key = next(key for key in store.load(incident_id).actions if key.startswith("rebuild:28349@"))
    assert store.load(incident_id).actions[key].status == "started"
    runner.mark_lost()
    assert store.load(incident_id).actions[key].status == "unverifiable"
    runner.reconcile_rebuilds()
    assert store.load(incident_id).actions[key].status in ("completed", "verified")


def test_a_lost_comms_post_is_found_on_the_bus_not_reposted(store, clock, fake):
    fake.lose_post.add("ack")
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(hours=2))

    assert record.status == "closed"
    assert [post["event"] for post in fake.posts].count("ack") == 1
    assert record.actions["comms:ack"].status == "verified"


def test_claude_is_refused_as_the_incident_fix_route(store, clock, fake):
    incident_id = open_incident(store, clock)
    with store.inputs(incident_id) as record:
        record.facts["routes"]["fix"] = ["opus", "xhigh"]
    with pytest.raises(RouteRefused):
        Runner(store, incident_id, world_of(fake), clock).tick()
    assert fake.launches == []


@pytest.mark.parametrize(("role", "model"), [("fix", "opus"), ("evidence", "claude-opus-5-5"), ("backup", "fable"), ("backup", "codex")])
def test_route_assertions(role, model):
    with pytest.raises(RouteRefused):
        incident.assert_route(role, model)


def test_backup_launches_on_opus_after_fifteen_quiet_minutes(store, clock, fake):
    incident_id = open_incident(store, clock)
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=16), worker=lambda *_: None)

    assert [(lane.rsplit("-", 1)[1], model) for lane, model, _ in fake.launches] == [("fix", "sol"), ("evidence", "sol"), ("backup", "opus")]


def test_a_transferred_incident_refuses_the_former_owner(store, clock, fake):
    incident_id = open_incident(store, clock)
    former = Runner(store, incident_id, world_of(fake), clock)
    former.tick()
    assert actions.main(["transfer", "--incident", incident_id, "--expect-generation", "1", "--to", "runner-b"], store) == 0
    assert actions.main(["ack", "--incident", incident_id, "--owner", "runner-b"], store) == 0
    posts = len(fake.posts)

    clock.at += TICK
    with pytest.raises(StaleGeneration):
        former.tick()
    assert len(fake.posts) == posts
    assert Runner(store, incident_id, world_of(fake), clock).generation == 2


def test_a_transfer_names_the_current_generation(store, clock):
    incident_id = open_incident(store, clock)
    assert actions.main(["transfer", "--incident", incident_id, "--expect-generation", "4", "--to", "runner-b"], store) == 3
    with pytest.raises(SystemExit):
        store.ack(incident_id, "runner-c")


def test_missing_comms_answer_escalates_once(store, clock, fake):
    fake.posted = lambda seq: None
    incident_id = open_incident(store, clock)
    drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(minutes=10), worker=lambda *_: None)

    overdue = [ask for ask in fake.asks if "comms" in ask and "is overdue" in ask]
    assert len(overdue) == 1


def test_duplicate_acceptance_returns_the_stored_action(clock):
    record = Incident("i1", "runner", 1, stamp(clock.at), {})
    first = record.accept("rebuild:1@abc", "rebuild", "#1", "g", clock.at)
    record.start("rebuild:1@abc", clock.at)
    second = record.accept("rebuild:1@abc", "rebuild", "#1", "g", clock.at)
    assert second is first and second.status == "started"


def test_pacific_times_carry_no_zone():
    assert incident.pacific("2026-10-01T17:49:00Z") == "10:49am"
    assert incident.pacific("2026-10-01T18:08:43Z") == "11:08am"


def test_render_brief_fills_every_placeholder():
    text = incident.render_brief("Fix lane brief", {"fix lane name": "incident-x-fix", "incident id": "x", "break-glass skill": "break-glass"})
    assert "  break-glass without asking" in text
    assert '`env -u AWS_PROFILE tools/ci break-glass --reason "INCIDENT x: WHY" --stack DOMAIN/ENV`' in text
    assert "incident-x-fix" in text
    assert "incident.py note --incident x --pr <PR number>" in text
    assert not incident.PLACEHOLDER.search(text)


class Shell(incident.Shell):
    def __init__(self, outputs: dict[tuple[str, ...], str], codes: dict[tuple[str, ...], int] | None = None):
        self.outputs, self.codes, self.calls = outputs, codes or {}, []

    def match(self, argv: list[str]) -> tuple[str, ...]:
        return next(key for key in self.outputs if tuple(argv[: len(key)]) == key or key[-1] in " ".join(argv))

    def run(self, argv, cwd=None, env=None):
        self.calls.append(list(argv))
        return self.outputs[self.match(argv)]

    def call(self, argv, cwd=None):
        self.calls.append(list(argv))
        key = self.match(argv)
        return self.codes.get(key, 0), self.outputs[key]


def test_buildkite_reads_the_stored_configuration_and_pages_builds():
    page = json.loads((FIXTURES / "pr-review-builds.json").read_text())
    shell = Shell({("&page=1",): json.dumps(page * 100), ("&page=2",): json.dumps(page), ("bk", "api", "/pipelines/pr-review"): (FIXTURES / "pr-review-pipeline.json").read_text()})
    buildkite = incident.Buildkite(shell, "pr-review")

    assert "infra/generate.ts" in buildkite.configuration()
    builds = buildkite.failed(REPLAY["onset"], "2026-10-01T18:08:00Z")
    assert len(builds) == 101 and builds[0]["rebuilt_from"]["number"] == 72867
    assert "state[]=failed&state[]=canceled&created_from=2026-10-01T16:45:36Z&created_to=2026-10-01T18:08:00Z" in shell.calls[1][2]
    assert shell.calls[2][2].endswith("page=2")
    job = next(job for job in builds[0]["jobs"] if "Prepare PR evidence" in job["name"])
    assert job["state"] == "passed"


def test_github_reads_pr_heads_in_one_query_and_finds_a_queue_landing():
    graph = {"data": {"repository": {"p28345": {"number": 28345, "state": "OPEN", "headRefOid": "4813", "url": "u"}, "p28841": {"number": 28841, "state": "CLOSED", "headRefOid": "8152", "url": "v"}}}}
    commits = [{"sha": "aaaa", "commit": {"message": "other (#1)"}}, {"sha": REPAIR["sha"], "commit": {"message": "ci: 🐛 pr-review generates the infra graphs (#28998)\n\nbody"}}]
    shell = Shell({("gh", "api", "graphql"): json.dumps(graph), ("gh", "pr", "view"): json.dumps({"state": "CLOSED", "mergedAt": None, "mergeCommit": None, "closedAt": "2026-10-01T17:45:48Z"}), ("gh", "api", "repos/"): json.dumps(commits)})
    github = incident.GitHub(shell, "Forge-AI/monorepo", "dev")

    assert github.prs([28345, 28841]) == {28345: {"state": "OPEN", "head": "4813", "url": "u"}, 28841: {"state": "CLOSED", "head": "8152", "url": "v"}}
    assert len([call for call in shell.calls if call[:3] == ["gh", "api", "graphql"]]) == 1
    assert github.landed(28998) == REPAIR["sha"]
    assert "since=2026-10-01T17:35:48Z" in shell.calls[-1][2]


def test_orca_parses_the_launch_line():
    for state in ("ready", "unsupervised"):
        shell = Shell({("orca-launch.sh",): f"booting\nincident-x-fix {state} task=t1 dispatch=d1 terminal=term-1 worktree=/w/x\n"})
        receipt = incident.Orca(shell, "run1", "repo1").launch("incident-x-fix", "sol", "xhigh", Path("/b.md"))
        assert (receipt["state"], receipt["dispatch"]) == (state, "d1")
    shell = Shell({("orca-launch.sh",): "incident-x-fix failed worker-start terminal=: refused\n"})
    with pytest.raises(incident.EffectFailed):
        incident.Orca(shell, "run1", "repo1").launch("incident-x-fix", "sol", "xhigh", Path("/b.md"))
    with pytest.raises(incident.EffectFailed):
        incident.Orca(shell, None, None).launch("incident-x-fix", "sol", "xhigh", Path("/b.md"))


@pytest.mark.parametrize(
    ("code", "out", "drifted"),
    [(1, "pr-review: patch\nci sync: pipelines drifted from their resources: pr-review\n", True), (0, "1 v2 pipeline resource(s) match Buildkite\n", False)],
)
def test_activation_reads_the_sync_dry_run(code, out, drifted):
    shell = Shell({("bun", "infra/ci/src/cli.ts", "sync"): out}, {("bun", "infra/ci/src/cli.ts", "sync"): code})
    assert incident.Activation(shell, Path("/m"), "pr-review", Path("/w")).drifted(Path("/w")) is drifted
    assert shell.calls == [["bun", "infra/ci/src/cli.ts", "sync", "pr-review"]]


def test_activation_refuses_an_unreadable_dry_run():
    shell = Shell({("bun",): "ci sync: BUILDKITE_TOKEN is not set\n"}, {("bun",): 1})
    with pytest.raises(incident.EffectFailed):
        incident.Activation(shell, Path("/m"), "pr-review", Path("/w")).drifted(Path("/w"))


def test_comms_posts_to_the_lane_and_reads_its_posted_ts():
    answers = [{"seq": 9, "re": 7, "from": "incident-comms", "text": "posted ts=1790877617.443359"}]
    shell = Shell({("post",): "#7 ask incident:x incident-x -> incident-comms\n", ("read",): json.dumps(answers)})
    comms = incident.Comms(shell, "bus1", Path("/m"), "incident-x", "incident-comms", "release-v3", "incident:x")

    assert comms.event("ack", {"reaction": "eyes"}, "g-thread") == 7
    post = shell.calls[0]
    assert post[post.index("--kind") + 1] == "ask"
    assert post[post.index("--to") + 1] == "incident-comms" and json.loads(post[post.index("--text") + 1])["grant"] == "g-thread"
    assert comms.posted(7) == "1790877617.443359"
    assert comms.posted(8) is None


def test_an_alert_with_no_pipeline_closes_on_live_evidence(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert")
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=5))
    incident.main(["note", "--incident", incident_id, "--live", "API-1N82 error rate back to 0 since 11:20am"], store, clock)
    record = drive(runner, store, clock, clock.at + timedelta(minutes=5))

    assert record.status == "closed"
    assert fake.asks == [] and fake.applies == [] and fake.rebuilds == []
    assert fake.fences == ["fence api from applies and deploys except incident-pr-review-1001-fix"]
    assert [post["event"] for post in fake.posts] == ["ack", "live", "recovered"]


def test_comms_find_skips_the_fence_broadcast():
    entries = [
        {"seq": 1, "kind": "decision", "from": "incident-x", "to": [], "text": "fence api from applies and deploys except incident-x-fix"},
        {"seq": 2, "kind": "ask", "from": "incident-x", "to": ["incident-comms"], "text": json.dumps({"event": "ack"})},
    ]
    comms = incident.Comms(Shell({("read",): json.dumps(entries)}), "bus1", Path("/m"), "incident-x", "incident-comms", "release-v3", "incident:x")
    assert comms.find("ack") == 2
    assert comms.find("pr") is None


def test_orca_receipt_reads_a_receipt_left_before_its_rename(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCA_LAUNCH_STATE", str(tmp_path))
    (tmp_path / "incident-x-fix.json.new").write_text(json.dumps({"result": {"taskId": "t1", "dispatchId": "d1", "state": "ready"}}))
    assert incident.Orca(Shell({}), "run1", "repo1").receipt("incident-x-fix")["dispatch"] == "d1"
    assert incident.Orca(Shell({}), "run1", "repo1").receipt("incident-x-evidence") is None


def test_a_lost_launch_without_a_receipt_asks_instead_of_relaunching(store, clock, fake):
    incident_id = open_incident(store, clock)
    launch = fake.launch

    def lost(lane, model, effort, brief):
        if lane.endswith("-fix") and not any(name.endswith("-fix") for name, _, _ in fake.launches):
            fake.launches.append((lane, model, effort))
            raise ResponseLost("worker-start timed out")
        return launch(lane, model, effort, brief)

    world = world_of(fake)
    world.orca.launch = lost
    drive(Runner(store, incident_id, world, clock), store, clock, INTAKE + timedelta(minutes=3), worker=lambda *_: None)

    assert [lane for lane, _, _ in fake.launches].count(f"incident-{incident_id}-fix") == 1
    assert any("left no receipt" in ask for ask in fake.asks)


def test_a_canary_waiting_on_its_grant_launches_once_granted(store, clock, fake):
    incident_id = open_incident(store, clock, grants={key: value for key, value in GRANTS.items() if key != "rebuild"})
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, parse_stamp(REPAIR["landed_at"]) + timedelta(minutes=10))
    assert fake.rebuilds == [] and "canary" not in store.load(incident_id).facts

    incident.main(["grant", "--incident", incident_id, "--name", "rebuild", "--ref", "g-rebuild"], store, clock)
    record = drive(runner, store, clock, clock.at + timedelta(hours=1))
    assert record.status == "closed" and fake.rebuilds[0] == 72867


def test_a_crash_after_verifying_the_sync_resumes_without_a_second_apply(store, clock, fake):
    incident_id = open_incident(store, clock)
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, parse_stamp(REPAIR["landed_at"]) + timedelta(seconds=40))
    with store.owned(incident_id, 1) as record:
        record.milestones = [entry for entry in record.milestones if entry["name"] != "activated"]
        record.status = "activation_pending"
    record = drive(runner, store, clock, clock.at + timedelta(hours=1))

    assert record.status == "closed" and len(fake.applies) == 1


def test_a_rebuild_that_keeps_failing_to_reach_buildkite_stops_at_the_attempt_cap(store, clock, fake):
    real = fake.rebuild

    def never(number):
        if number == 72860:
            fake.rebuilds.append(number)
            raise ResponseLost("timed out")
        return real(number)

    world = world_of(fake)
    world.buildkite.rebuild = never
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world, clock), store, clock, INTAKE + timedelta(hours=2))

    assert rebuild_counts(fake)[72860] == incident.MAX_ATTEMPTS
    assert len([ask for ask in fake.asks if "failed 3 times" in ask]) == 1
    assert record.status == "closed"
    assert [entry["key"] for entry in record.facts["accounting"]["unlaunched"]][-1].endswith("#3")


def test_no_failed_work_still_reaches_the_final_reply(store, clock, fake):
    for view in fake.prs.values():
        view["state"] = "CLOSED"
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(hours=2))

    assert record.status == "closed" and fake.rebuilds == []
    assert record.facts["accounting"]["rerun"] == 0


def test_a_runner_for_a_former_owner_refuses_to_start(store, clock):
    incident_id = open_incident(store, clock)
    store.transfer(incident_id, 1, "runner-b")
    store.ack(incident_id, "runner-b")
    assert incident.main(["run", "--incident", incident_id, "--once"], store, clock) == 3


def test_an_adopted_lane_is_recorded_not_launched(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert")
    with store.inputs(incident_id) as record:
        record.facts["adopted"] = {"fix": "api-1n85-fix"}
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=1), worker=lambda *_: None)

    assert [lane for lane, _, _ in fake.launches] == [f"incident-{incident_id}-evidence"]
    assert fake.fences == ["fence api from applies and deploys except api-1n85-fix"]
    assert store.load(incident_id).actions["dispatch:fix"].verification_receipt == {"lane": "api-1n85-fix", "adopted": True}
    brief = (store.root / incident_id / f"incident-{incident_id}-evidence.full.md").read_text()
    assert "Feed api-1n85-fix and the executor" in brief


def test_a_not_ours_verdict_closes_with_a_final_reply(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert")
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=3))
    incident.main(["note", "--incident", incident_id, "--not-ours", "one employee sand-cli event; no user traffic"], store, clock)
    record = drive(runner, store, clock, clock.at + timedelta(minutes=20), worker=lambda *_: None)

    assert record.status == "closed"
    assert [post["event"] for post in fake.posts] == ["ack", "recovered"]
    assert fake.posts[-1]["facts"]["not_ours"].startswith("one employee")
    assert "backup" not in " ".join(lane for lane, _, _ in fake.launches)


def test_open_takes_the_alert_link_code_path_and_runbook(store, clock):
    incident_id = open_incident(store, clock, kind="alert")
    facts = store.load(incident_id).facts
    assert (facts["alert"], facts["code_path"], facts["runbook"], facts["adopted"]) == (facts["thread"], "the api service", None, {})
    assert incident.main(["open", "--kind", "alert", "--incident", "x2", "--target", "api", "--thread", "t", "--onset", REPLAY["onset"], "--bus", "b", "--comms-lane", "c", "--root-lane", "r", "--checkout", "/m", "--alert", "https://forge-rf.sentry.io/issues/7766636402/", "--runbook", "4950740", "--adopt", "fix=api-1n85-fix"], store, clock) == 0
    facts = store.load("x2").facts
    assert (facts["alert"], facts["runbook"], facts["adopted"]) == ("https://forge-rf.sentry.io/issues/7766636402/", "4950740", {"fix": "api-1n85-fix"})


def test_the_record_lives_in_cc_notes_and_briefs_carry_its_ids(store, clock, fake):
    incident_id = open_incident(store, clock)
    record = drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(hours=2))

    assert record.facts["records"] == {"investigation": "inv1234", "log": "log1234"}
    assert fake.premise.startswith(f"{record.facts['alert']} reports a pr-review outage on the pr-review pipeline since ")
    attached = [path.name for _, _, path in fake.entries if path]
    assert attached == [f"incident-{incident_id}-fix.full.md", f"incident-{incident_id}-evidence.full.md"]
    brief = (store.root / incident_id / f"incident-{incident_id}-evidence.full.md").read_text()
    assert "log1234" in brief and "inv1234" in brief
    logged = [text.split(" at ")[0] for _, text, path in fake.entries if not path]
    assert logged == [entry["name"] for entry in record.milestones]
    assert [(verb, commit) for _, verb, _, commit in fake.verdicts] == [("root-cause", None), ("fix", REPAIR["sha"])]


def test_a_refused_record_asks_the_root_once_and_still_launches(store, clock, fake):
    fake.refuse_records = True
    incident_id = open_incident(store, clock)
    drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(minutes=2), worker=lambda *_: None)

    assert [lane for lane, _, _ in fake.launches] == [f"incident-{incident_id}-fix", f"incident-{incident_id}-evidence"]
    assert len([ask for ask in fake.asks if "cc-notes refused" in ask]) == 1
    assert fake.entries == []


def test_a_not_ours_verdict_exonerates_the_investigation(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert")
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=3))
    incident.main(["note", "--incident", incident_id, "--not-ours", "one employee sand-cli event; no user traffic"], store, clock)
    drive(runner, store, clock, clock.at + timedelta(minutes=20), worker=lambda *_: None)

    assert ("inv1234", "exonerate", "one employee sand-cli event; no user traffic", None) in fake.verdicts


def test_records_reuse_an_investigation_and_log_already_labelled_for_the_incident():
    listed = json.dumps([{"id": "abcdef0123", "title": "t"}])
    shell = Shell({("ccn", "-R", "/m", "investigation", "list"): listed, ("ccn", "-R", "/m", "log", "list"): listed})
    assert incident.Records(shell, Path("/m")).open("x1", "t", "p") == {"investigation": "abcdef0", "log": "abcdef0"}
    assert [call[3:5] for call in shell.calls] == [["investigation", "list"], ["log", "list"]]
    assert all("incident:x1" in call for call in shell.calls)


RULING = "379b70a: deploy ordering calls the release pipeline's own resolver, never a second implementation"
ENTRY_POINT = "releaseDAG at go/ci/release.go:170"


def test_open_records_the_rulings_and_entry_point_the_fix_brief_quotes(store, clock, fake):
    assert incident.main(["open", "--kind", "alert", "--incident", "x3", "--target", "api", "--thread", "t", "--onset", REPLAY["onset"], "--bus", "b", "--comms-lane", "c", "--root-lane", "r", "--checkout", "/m", "--ruling", RULING, "--entry-point", ENTRY_POINT], store, clock) == 0
    assert (store.load("x3").facts["rulings"], store.load("x3").facts["entry_point"]) == ([RULING], ENTRY_POINT)
    drive(Runner(store, "x3", world_of(fake), clock), store, clock, INTAKE + timedelta(minutes=1), worker=lambda *_: None)

    brief = (store.root / "x3" / "incident-x3-fix.full.md").read_text()
    assert f"  {RULING}\n  entry point: {ENTRY_POINT}\n" in brief
    assert '--design-check "<symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>"' in brief


def test_a_brief_with_no_rulings_says_so_and_asks_nothing(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert")
    drive(Runner(store, incident_id, world_of(fake), clock), store, clock, INTAKE + timedelta(minutes=1), worker=lambda *_: None)

    brief = (store.root / incident_id / f"incident-{incident_id}-fix.full.md").read_text()
    assert "  none recorded for this subsystem\n  entry point: none named\n" in brief
    assert fake.asks == []


def test_a_design_check_reaches_the_root_once_and_its_confirmation_is_a_milestone(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert", rulings=[RULING], entry_point=ENTRY_POINT)
    runner = Runner(store, incident_id, world_of(fake), clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=1), worker=lambda *_: None)
    assert fake.asks == []

    check = f"{ENTRY_POINT} called from deploy.planned; 379b70a met; leaves out: none"
    incident.main(["note", "--incident", incident_id, "--design-check", check], store, clock)
    drive(runner, store, clock, clock.at + timedelta(minutes=2), worker=lambda *_: None)
    assert fake.asks == [f"{incident_id}: design check before the PR: {check}; confirm it calls {ENTRY_POINT} and meets {RULING} with `incident.py note --incident {incident_id} --design-ok`, or redirect the fix lane"]

    incident.main(["note", "--incident", incident_id, "--design-ok"], store, clock)
    record = drive(runner, store, clock, clock.at + timedelta(minutes=1), worker=lambda *_: None)
    assert record.reached("design")["text"] == f"confirmed: {check}"
    assert record.open_decisions() == []
    assert len(fake.asks) == 1


def test_a_pr_on_a_ruled_subsystem_with_no_design_check_asks_the_root_to_hold_it(store, clock, fake):
    incident_id = open_incident(store, clock, kind="alert", rulings=[RULING], entry_point=ENTRY_POINT)
    runner = Runner(store, incident_id, world_of(fake), clock)
    incident.main(["note", "--incident", incident_id, "--pr", str(REPAIR["pr"])], store, clock)
    drive(runner, store, clock, INTAKE + timedelta(minutes=1), worker=lambda *_: None)

    assert [ask for ask in fake.asks if "design" in ask] == [f"{incident_id}: #{REPAIR['pr']} opened with no design check; hold it, then confirm it calls {ENTRY_POINT} and meets {RULING} with `incident.py note --incident {incident_id} --design-ok`, or redirect the fix lane"]
