import json
import mmap
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from drive import dashboard, github, orca, page, reads, slack, state, view
from drive.install import config_dir
from drive.render import cut, when
from drive.state import DriveError

POLL_SECONDS = 5
READ_EVERY = timedelta(seconds=60)
FAILURES_BEFORE_SAYING = 3
FAILURE_EVERY = timedelta(minutes=10)
TICK_EVERY = timedelta(minutes=30)
DUE_EVERY = timedelta(minutes=10)
LINE_LIMIT = 300
OWING_STATES = ("working", "waiting")
LIVE_JOB_STATES = ("running", "waiting")
LINE_BREAKS = re.compile(r"[\r\n]+")
HANDOFF = timedelta(minutes=2)
QUIET_FOR = timedelta(minutes=30)
STUCK_FOR = timedelta(minutes=30)
TAIL_BYTES = 65536
COMPACT_PAST_TOKENS = 120_000
COMPACT_WHEN_QUIET = timedelta(minutes=10)
NOT_SLUG = re.compile(r"[^A-Za-z0-9]")
PR_LINE = re.compile(r"\s*(?:READY|PR:)")
STUCK_WORDS = {"red": "still red", "ready": "green and approved with no card drawn"}


@dataclass
class Watcher:
    name: str
    offset: int
    ticked_at: datetime
    read_at: datetime | None = None
    failures: dict[str, int] = field(default_factory=dict)
    failure_said: dict[str, datetime] = field(default_factory=dict)
    page_shape: str | None = None
    cards: dict | None = None
    agent_gaps: dict[str, str | None] = field(default_factory=dict)


def read_failed(watcher: Watcher, source: str, error: Exception, moment: datetime) -> list[str]:
    """Retry a failed read on the next poll; say so once it fails three times running, then at most every ten minutes."""
    failures = watcher.failures[source] = watcher.failures.get(source, 0) + 1
    if failures < FAILURES_BEFORE_SAYING:
        watcher.read_at = None
        return []
    said = watcher.failure_said.get(source)
    if said and moment - said < FAILURE_EVERY:
        return []
    watcher.failure_said[source] = moment
    return [f"WATCH {source} read failed {failures} times in a row: {error}"]


def ids(pr: dict) -> str:
    return f"[{pr['order']} {pr['job']}]"


def pr_changes(pr: dict, read: dict) -> list[str]:
    fresh = github.live(read)
    changes = []
    if fresh["head"] != pr["head"] and pr["head"]:
        changes.append(f"new head {fresh['head'][:7]}")
    if fresh["checks"] != pr["checks"] and fresh["checks"] in ("pass", "fail"):
        changes.append("checks passed" if fresh["checks"] == "pass" else "checks failed")
    if fresh["review"] != pr["review"] and fresh["review"] != "none":
        changes.append("approved" if fresh["review"] == "approved" else "changes requested")
    if fresh["state"] != pr["state"]:
        changes.append(fresh["state"])
    pr.update(fresh, draft=read["isDraft"], merged_sha=read["mergeCommit"])
    return changes


def github_lines(watcher: Watcher, moment: datetime) -> list[str]:
    keys = state.open_pr_keys(state.load(watcher.name))
    if not keys:
        return []
    try:
        reads = github.fetch(keys)
    except github.GithubError as error:
        return read_failed(watcher, "github", error, moment)
    watcher.failures.pop("github", None)
    lines = []
    with state.edit(watcher.name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            changes = pr_changes(pr, read)
            if stuck := stuck_for(pr, data, watcher.cards, moment):
                changes.append(stuck)
            pr["updated"] = state.stamp(moment)
            title = cut(pr["title"], 120)
            lines.extend(f'PR {key} "{title}": {change} {ids(pr)}' for change in changes)
    return lines


def stuck_as(pr: dict, holds: list[str], cards: dict | None) -> str | None:
    if pr["state"] != "open":
        return None
    if pr["checks"] == "fail":
        return "red"
    card_owed = cards is not None and pr["wants_tap"] and not held(pr) and not holds and not view.card_for(cards["pr_cards"], pr["url"])
    return "ready" if pr["checks"] == "pass" and pr["review"] == "approved" and card_owed else None


def held(pr: dict) -> bool:
    return (pr.get("tap") or {}).get("label") == "Hold"


def stuck_for(pr: dict, data: dict, cards: dict | None, moment: datetime) -> str | None:
    """Say a PR stuck red, or green and approved with no card while nothing it waits on holds, once it has held 30 minutes, then again only when what it is changes: its head, checks, review, tap, job's state, or what it waits on."""
    holds = view.holding(data, pr["waits"], cards)
    what, stuck = stuck_as(pr, holds, cards), pr.get("stuck")
    if what is None or not stuck or stuck["as"] != what:
        pr["stuck"] = what and {"as": what, "since": state.stamp(moment), "said": None}
        return None
    since = state.parse_time(stuck["since"])
    seen = {
        "head": pr["head"],
        "checks": pr["checks"],
        "review": pr["review"],
        "tap": (pr.get("tap") or {}).get("label"),
        "job": data["jobs"][pr["job"]]["state"],
        "waits": holds or None,
    }
    if moment - since < STUCK_FOR or stuck["said"] and stuck.get("seen") == seen:
        return None
    pr["stuck"] = {"as": what, "since": stuck["since"], "said": state.stamp(moment), "seen": seen}
    waiting = f", waiting on {', '.join(map(bare, holds))}" if holds else ""
    return f"{STUCK_WORDS[what]} for {minutes(moment - since)} minutes{waiting}"


def bare(wait: str) -> str:
    return wait.split("?")[0]


def waits_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Drop each wait of a job or PR that ended, his card answered or the job finished, and say once, as its last wait ends, that nothing holds it."""
    data = state.load(watcher.name)
    owners = [*data["jobs"].values(), *data["prs"].values()]
    if all(view.holding(data, owner["waits"], watcher.cards) == owner["waits"] for owner in owners):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if (ended := released(data, job, watcher.cards)) and job["state"] not in state.FINISHED_JOB_STATES:
                job["updated"] = state.stamp(moment)
                built = f", built on {job['branch']}" if job["state"] == "built" else ""
                lines.append(f"FREE {job_id} {job['order']}: {ended} answered or done, nothing else holds it{built}")
        for key, pr in data["prs"].items():
            if (ended := released(data, pr, watcher.cards)) and pr["state"] == "open":
                lines.append(f"FREE {key} {pr['job']} {pr['order']}: {ended} answered or done, nothing else holds it")
    return lines


def released(data: dict, owner: dict, cards: dict | None) -> str:
    """Drop the waits of a job or PR that ended; the ended ones, when nothing else holds it."""
    holds = view.holding(data, owner["waits"], cards)
    ended = [wait for wait in owner["waits"] if wait not in holds]
    owner["waits"] = holds
    return "" if holds else ", ".join(map(bare, ended))


def minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def mail_line(job_id: str, job: dict, message: dict, moment: datetime) -> str | None:
    """Record one message from a remote lane on its job; the event line it wakes the coordinator with, if any."""
    kind = message["type"]
    body = message["body"] or ""
    job["updated"] = state.stamp(moment)
    if kind == "escalation":
        last = first_line(body.split("Last output:")[-1])
        return f"JOB {job_id} {job['order']} STOPPED without a report, last output: {cut(last, 160)}"
    job["said"] = first_line(body) or message["subject"]
    if kind == "status":
        return None
    job["report"] = body
    if kind == "question":
        job["ask"] = message["id"]
        job["state"] = "waiting"
    return f"JOB {job_id} {job['order']} {job['said']}"


def lane_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each live remote lane's mail, read from its own Run and kept in its job's history: reports become JOB lines, read questions queue on their job to be answered here, and mail from a worker the job has since replaced is only kept."""
    data = state.load(watcher.name)
    lanes: dict[str, dict[str, str]] = {}
    for job_id, job in live_lanes(data).items():
        for dispatch in [job["dispatch"], *(ended["dispatch"] for ended in job.get("deaths", []))]:
            lanes.setdefault(job["run"], {})[f"dispatch:{dispatch}"] = job_id
    lines, messages = [], []
    for run, handles in lanes.items():
        source = f"lane mail of {run}"
        try:
            messages += [(run, handles, message) for message in orca.mail(run)]
        except orca.OrcaError as error:
            lines += read_failed(watcher, source, error, moment)
            continue
        watcher.failures.pop(source, None)
    if messages:
        with state.edit(watcher.name) as data:
            seen = data.setdefault("lane_mail", {})
            for run, handles, message in messages:
                if not unseen(seen.get(run), message):
                    continue
                seen[run] = message["sequence"]
                job_id = handles.get(message["from_handle"])
                if not job_id or message["type"] == "heartbeat":
                    continue
                state.keep_reply(watcher.name, job_id, message)
                if message["from_handle"] != f"dispatch:{data['jobs'][job_id]['dispatch']}":
                    continue
                if message["type"] == "question" and (question := reads.request(message["body"] or "")):
                    data["jobs"][job_id].setdefault("reads", {})[message["id"]] = question
                    continue
                lines.append(mail_line(job_id, data["jobs"][job_id], message, moment))
                track_lane_prs(data, job_id, message["body"] or "")
    return [line for line in lines + read_lines(watcher, data, moment) if line]


def live_lanes(data: dict) -> dict[str, dict]:
    return {
        job_id: job
        for job_id, job in data["jobs"].items()
        if job.get("where") == "remote" and job.get("dispatch") and job["state"] in LIVE_JOB_STATES
    }


def unseen(last: int | None, message: dict) -> bool:
    """Whether the watch has yet to act on a lane's message: past its last one in this Run, or, before it has one, any message `check` left unread."""
    return message["sequence"] > last if last is not None else not message["read"]


def track_lane_prs(data: dict, job_id: str, body: str) -> None:
    job = data["jobs"][job_id]
    for line in body.splitlines():
        if not PR_LINE.match(line):
            continue
        for match in state.PR_URL.finditer(line):
            key = f"{match[1]}/{match[2]}#{match[3]}"
            url = state.pr_url(key)
            data["prs"].setdefault(key, state.new_pr(url, job_id, job["order"]) | {"wants_tap": True})
            if url not in job["prs"]:
                job["prs"].append(url)


def read_lines(watcher: Watcher, data: dict, moment: datetime) -> list[str]:
    """Answer each `LOG` or `READ` question a live lane queued, here, since a Sprite holds no CI credentials or drive records. A question leaves its job's queue only once its answer goes through, since a lane whose wait ends waits again on the same question: a refused reply or a watch stopped mid-answer only delays it."""
    checkout = Path(data["roles"]["coordinator"]["cwd"])
    lines = []
    for job_id, job in live_lanes(data).items():
        for message_id, question in job.get("reads", {}).items():
            source = f"the answer to {job_id}'s"
            try:
                orca.reply(watcher.name, job["run"], message_id, reads.answer(question, checkout))
            except orca.OrcaError as error:
                lines += read_failed(watcher, source, error, moment)
                continue
            watcher.failures.pop(source, None)
            with state.edit(watcher.name) as fresh:
                fresh["jobs"][job_id]["reads"].pop(message_id, None)
    return lines


def page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Redraw the drive's Slack status page when what it shows changed since the last drawing, its "Updated" line aside; say once, kept in the drive across watch restarts, when Slack starts refusing so the page he sees is stale, once when it takes a redraw again, and with the link whenever the page moved to a new tab."""
    markdown = page.render(view.read(watcher.name, moment), dashboard.link(watcher.name))
    shape = markdown.split("\n", 1)[1]
    if shape == watcher.page_shape:
        return []
    stale = state.load(watcher.name)["slack"].get("page_stale")
    try:
        drawn = page.draw(watcher.name, markdown)
    except page.PageError as error:
        if stale:
            return []
        with state.edit(watcher.name) as data:
            data["slack"]["page_stale"] = state.stamp(moment)
        return [f"WATCH status page stale since {when(state.stamp(moment))}: {error}"]
    watcher.page_shape = shape
    if stale:
        with state.edit(watcher.name) as data:
            data["slack"].pop("page_stale")
    if drawn.new_tab:
        return [f"WATCH status page redrawn on a new tab: {drawn.url}"]
    if stale:
        return [f"WATCH status page redrawn: Slack takes its redraws again, after refusing them since {when(stale)}"]
    return []


def event_line(event: dict, prs: dict) -> str:
    if event["kind"] == "tap":
        key = f"{event['repo']}#{event['number']}"
        suffix = f" {ids(prs[key])}" if key in prs else ""
        return f"TAP {event['label']} {key}{suffix}"
    details = " ".join(f"{name}={value}" for name, value in event.items() if name not in ("kind", "at"))
    return f"EVENT {event['kind']} {details}".rstrip()


def event_lines(watcher: Watcher) -> list[str]:
    events = list(state.read_events(watcher.name, watcher.offset))
    if not events:
        return []
    watcher.offset = events[-1][1]
    prs = state.load(watcher.name)["prs"]
    return [event_line(event, prs) for event, _ in events]


def overdue(order: dict, moment: datetime, away: bool) -> bool:
    if order["state"] in state.CLOSED_ORDER_STATES or not state.owed(order) or state.parse_time(state.owed_at(order)) > moment:
        return False
    said = order["due_said"]
    return said is None or not away and moment - state.parse_time(said) >= DUE_EVERY


def due_lines(watcher: Watcher, moment: datetime) -> list[tuple[str, str]]:
    """Each promised time that passed with no drive post in its order's thread since it was promised, read from Slack, again every ten minutes until one is there; only the first while he is away."""
    data = state.load(watcher.name)
    away = state.owner_away(data, moment)
    threads = {order_id: slack.order_posts(data, order) for order_id, order in state.open_orders(data).items() if overdue(order, moment, away)}
    if not threads:
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for order_id, (posts, unread) in threads.items():
            order = data["orders"][order_id]
            if overdue(order, moment, away):
                state.keep(order, posts)
            if overdue(order, moment, away):
                order["due_said"] = state.stamp(moment)
                why = unread or f"no line in its thread since {when(order['promised'])}"
                lines.append((order_id, f'DUE {order_id} "{cut(order["text"], 80)}" was due {when(state.owed_at(order))}: {why}'))
    return lines


def transcript(holder: dict, agent: str) -> Path:
    project = NOT_SLUG.sub("-", holder["cwd"])
    return config_dir() / "projects" / project / holder["session"] / "subagents" / f"agent-{agent}.jsonl"


def last_turn(path: Path) -> dict | None:
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") in ("user", "assistant"):
                    return entry
            if not start:
                return None
            span *= 4


def running_build(path: Path) -> tuple[str | None, str | None]:
    """The Claude Code version the session's newest API request reports, and the model its newest turn ran on."""
    version = model = None
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                if version is None and b'"api-request-shape"' in line:
                    version = json.loads(line).get("version")
                elif model is None and b'"type":"assistant"' in line.replace(b" ", b""):
                    model = json.loads(line)["message"].get("model")
                if version and model:
                    return version, model
            if not start:
                return version, model
            span *= 4


def unknown_model(version: str | None, model: str | None, builds: Path) -> str | None:
    """What his line says when the session's Claude Code build has no entry for its model, so it runs it at a default window."""
    binary = builds / version if version else None
    if not model or not binary or not binary.is_file():
        return None
    with open(binary, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as image:
        if image.find(model.encode()) >= 0:
            return None
    return f"runs {model} on Claude Code {version}, which has no entry for that model and runs it at a default context window: restart it on the installed build on his word"


def context_tokens(entry: dict) -> int:
    usage = entry["message"].get("usage") or {}
    return sum(usage.get(key, 0) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def compact_due(data: dict, moment: datetime) -> str | None:
    """Why the communicator should compact now: past the mark, and both it and he quiet, so no message lands mid-compaction."""
    held = data["roles"].get("communicator")
    if not held or not held.get("terminal"):
        return None
    path = config_dir() / "projects" / NOT_SLUG.sub("-", held["cwd"]) / f"{held['session']}.jsonl"
    if not path.exists() or moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone() < COMPACT_WHEN_QUIET:
        return None
    heard = (data.get("owner") or {}).get("heard")
    if heard and moment - state.parse_time(heard) < COMPACT_WHEN_QUIET:
        return None
    turn = last_turn(path)
    if not turn or turn["type"] != "assistant" or turn["message"].get("stop_reason") != "end_turn":
        return None
    tokens = context_tokens(turn)
    return f"at {tokens} tokens of context, quiet 10 minutes" if tokens >= COMPACT_PAST_TOKENS else None


def compact_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Type /compact into the communicator's Orca terminal at a quiet moment, never at its context limit mid-conversation."""
    data = state.load(watcher.name)
    if not (why := compact_due(data, moment)):
        return []
    try:
        terminal = data["roles"]["communicator"]["terminal"]
        orca.request("terminal", "send", "--terminal", terminal, "--text", "/compact", "--enter")
    except orca.OrcaError as error:
        return read_failed(watcher, "communicator compaction", error, moment)
    watcher.failures.pop("communicator compaction", None)
    return [f"WATCH compacted the communicator {why}"]


def said_last(entry: dict) -> str:
    texts = [block["text"] for block in entry["message"]["content"] if block.get("type") == "text"]
    return cut(first_line(texts[-1]), 100) if texts else "nothing"


def local_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    session = job.get("session")
    holder = next((held for held in data["roles"].values() if session and held["session"] == session), None)
    if holder is None:
        return f"{job_id}'s agent ended with an earlier session"
    if not job.get("agent"):
        return f"{job_id} has no agent recorded"
    path = transcript(holder, job["agent"])
    if not path.exists():
        return f"{job_id}'s agent {job['agent']} has no transcript"
    quiet = moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    if quiet < HANDOFF:
        return None
    turn = last_turn(path)
    if turn and turn["type"] == "assistant" and turn["message"].get("stop_reason") == "end_turn":
        return f'{job_id}\'s agent finished and its result is not recorded: "{said_last(turn)}"'
    if quiet >= QUIET_FOR:
        return f"{job_id}'s agent has written nothing for {minutes(quiet)} minutes"
    return None


def remote_gap(job_id: str, worker: dict, moment: datetime) -> str | None:
    projection = worker["projection"]
    if projection["outcome"] != "in_progress":
        return f"{job_id}'s lane {projection['outcome']} and its result is not recorded"
    output = (worker.get("terminal") or {}).get("lastOutputAt")
    quiet = output and moment - datetime.fromtimestamp(output / 1000).astimezone()
    if "input" in projection["attention"]["categories"] and quiet and quiet >= QUIET_FOR:
        return f"{job_id}'s lane has sat at its prompt for {minutes(quiet)} minutes"
    return None


def agent_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each running job's gap from its agent or lane, and a remote lane, running or waiting, whose worker died."""
    data = state.load(watcher.name)
    gaps, deaths, lines = {}, {}, []
    for order in state.open_orders(data).values():
        for job_id in order["jobs"]:
            job = data["jobs"][job_id]
            running = job["state"] == "running"
            if job.get("where") != "remote":
                if running:
                    gaps[job_id] = local_gap(job_id, job, data, moment)
                continue
            if not job.get("dispatch"):
                if running:
                    gaps[job_id] = f"{job_id} never started on its Sprite"
                continue
            if job["state"] not in LIVE_JOB_STATES or any(death["dispatch"] == job["dispatch"] for death in job.get("deaths", [])):
                continue
            source = f"lane of {job_id}"
            try:
                worker = orca.worker(job["dispatch"])
            except orca.OrcaError as error:
                if orca.GONE in str(error):
                    deaths[job_id] = (job["dispatch"], "Orca no longer holds its worker")
                    continue
                lines += read_failed(watcher, source, error, moment)
                continue
            watcher.failures.pop(source, None)
            if why := orca.death(worker):
                deaths[job_id] = (job["dispatch"], why)
            elif running:
                gaps[job_id] = remote_gap(job_id, worker, moment)
    watcher.agent_gaps = gaps
    return lines + death_lines(watcher.name, deaths, moment)


def death_lines(name: str, deaths: dict[str, tuple[str, str]], moment: datetime) -> list[str]:
    """Record each remote lane's death on its job and say it once, with what the coordinator's next resume does."""
    if not deaths:
        return []
    lines = []
    with state.edit(name) as data:
        for job_id, (dispatch, why) in deaths.items():
            job = data["jobs"][job_id]
            if not state.lane_died(job, dispatch, why, moment):
                continue
            doing = "waited on" if job["state"] == "waiting" else "worked on"
            lines.append(
                f'LANE {job_id} {job["order"]} died on Sprite {job["workspace"]}: {why}, while it {doing} '
                f'"{cut(job["said"] or job["goal"], 60)}". A message sent to it reaches no one; drive job resume '
                f'{job_id} "<its next step, or his answer>" relaunches it on a fresh Sprite with a handoff'
            )
    return lines


def can_wake(job_id: str, job: dict, data: dict, cards: dict | None, moment: datetime) -> bool:
    if job.get("ask") or job.get("card") and view.card_open(job["card"], cards) or view.holding(data, job["waits"], cards):
        return True
    if any(pr["job"] == job_id and pr["state"] == "open" for pr in data["prs"].values()):
        return True
    dues = [data["orders"][order_id]["due"] for order_id in [job["order"], *job["carries"]]]
    return any(due and state.parse_time(due) > moment for due in dues)


def job_gap(watcher: Watcher, job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    if moment - state.parse_time(job["updated"]) < HANDOFF:
        return None
    if job["state"] == "running":
        return watcher.agent_gaps.get(job_id)
    if job["state"] in ("waiting", "built"):
        if can_wake(job_id, job, data, watcher.cards, moment):
            return None
        waiting = f"is built on {job['branch']}" if job["state"] == "built" else f'waits on "{cut(job["said"] or "nothing named", 80)}"'
        return f"{job_id} {waiting} and no open PR, card or due time can wake it"
    if job["state"] == "done":
        if not job.get("card"):
            return f"{job_id} answered and the answer is not posted to him"
        return None if view.card_open(job["card"], watcher.cards) else f"{job_id}'s card is answered and no job carries his answer"
    return f"{job_id} {job['state']}"


def part_gap(watcher: Watcher, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    order = data["orders"][order_id]
    jobs = [(job_id, data["jobs"][job_id]) for job_id in order["jobs"] if part in state.job_parts(data["jobs"][job_id], order_id)]
    if order["parts"][part]["posted"] or any(job["state"] == "done" and job["kind"] == "builder" for _, job in jobs):
        return None
    if not jobs:
        return "no job"
    gaps = [job_gap(watcher, job_id, job, data, moment) for job_id, job in jobs]
    return None if None in gaps else gaps[-1]


def part_gaps(watcher: Watcher, data: dict, moment: datetime) -> dict[tuple[str, str], str | None]:
    return {
        (order_id, part): part_gap(watcher, data, order_id, part, moment)
        for order_id, order in state.open_orders(data).items()
        for part in order["parts"]
    }


def orphan_change(data: dict, key: tuple[str, str], gap: str | None, due: set[str], moment: datetime) -> str | None:
    said = data["orders"][key[0]]["parts"][key[1]]["orphan_said"]
    if gap is None:
        return "heal" if said else None
    if key[0] in due or not said or moment - state.parse_time(said) >= TICK_EVERY:
        return "say"
    return None


def orphan_lines(watcher: Watcher, moment: datetime, due: set[str]) -> list[str]:
    data = state.load(watcher.name)
    if not any(orphan_change(data, key, gap, due, moment) for key, gap in part_gaps(watcher, data, moment).items()):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for key, gap in part_gaps(watcher, data, moment).items():
            change = orphan_change(data, key, gap, due, moment)
            if change:
                data["orders"][key[0]]["parts"][key[1]]["orphan_said"] = None if change == "heal" else state.stamp(moment)
            if change == "say":
                lines.append(f'ORPHAN {key[0]} "{cut(key[1], 80)}": {gap}')
    return lines


def changed_since(order: dict, jobs: dict, last: datetime) -> bool:
    stamps = [order["updated"], *(jobs[job_id]["updated"] for job_id in order["jobs"])]
    return any(state.parse_time(stamp) > last for stamp in stamps)


def due_soon(order: dict, moment: datetime) -> bool:
    return bool(order["due"]) and moment <= state.parse_time(order["due"]) < moment + TICK_EVERY


def owes_line(order: dict, jobs: dict, moment: datetime) -> bool:
    last = state.parse_time(order["last_line"] or order["created"])
    if moment - last < TICK_EVERY:
        return False
    if order["state"] == "done":
        return changed_since(order, jobs, max(last, moment - TICK_EVERY))
    if order["state"] not in OWING_STATES:
        return False
    return changed_since(order, jobs, last) or due_soon(order, moment)


def tick_lines(watcher: Watcher, moment: datetime) -> list[str]:
    data = state.load(watcher.name)
    if state.owner_away(data, moment):
        return []
    owing = [key for key, order in data["orders"].items() if owes_line(order, data["jobs"], moment)]
    return [f"TICK {' '.join(owing)}"] if owing else []


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One loop of the watch: his cards, GitHub, the waits his answers or a job's end release, lane mail, agents, the communicator's compaction and the status page every 60 s, GitHub again next poll after a failure, new events, promised times still owed a line, parts nothing carries, a tick every 30 minutes while he is here."""
    lines = []
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        view.refresh_cards(watcher.name)
        found, source = view.stored_cards(watcher.name)
        watcher.cards = found if source == "live" else None
        lines += github_lines(watcher, moment)
        lines += waits_lines(watcher, moment)
        lines += lane_lines(watcher, moment)
        lines += agent_lines(watcher, moment)
        lines += compact_lines(watcher, moment)
        lines += page_lines(watcher, moment)
    lines += event_lines(watcher)
    due = due_lines(watcher, moment)
    lines += [line for _, line in due]
    lines += orphan_lines(watcher, moment, {order_id for order_id, _ in due})
    if moment - watcher.ticked_at >= TICK_EVERY:
        watcher.ticked_at = moment
        lines += tick_lines(watcher, moment)
    return [LINE_BREAKS.sub(" ", line)[:LINE_LIMIT] for line in lines]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def claim(name: str) -> bool:
    path = state.pid_path(name)
    with state.locked(name):
        if path.exists() and (held := path.read_text().strip()).isdigit() and alive(int(held)):
            return False
        path.write_text(f"{os.getpid()}\n")
    return True


def release(name: str) -> None:
    path = state.pid_path(name)
    with state.locked(name):
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def main(drive_name: str | None = None) -> int:
    try:
        name = state.resolve(drive_name)
    except DriveError:
        return 0
    if state.session_role(state.load(name), os.environ.get("CLAUDE_CODE_SESSION_ID")) != "coordinator":
        return 0
    if not claim(name):
        return 0
    watcher = Watcher(name, offset=state.watch_cursor(name), ticked_at=state.now())
    state.save_watch_cursor(name, watcher.offset)
    try:
        while True:
            read_from = watcher.offset
            for line in run_pass(watcher, state.now()):
                print(line, flush=True)
            if watcher.offset != read_from:
                state.save_watch_cursor(name, watcher.offset)
            time.sleep(POLL_SECONDS)
    finally:
        release(name)
