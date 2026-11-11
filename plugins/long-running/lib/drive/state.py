import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

from drive import picture

ORDER_STATES = ("open", "working", "waiting", "done", "dropped")
ORDER_KINDS = ("answer", "build")
CLOSED_ORDER_STATES = ("done", "dropped")
JOB_KINDS = ("builder", "reader", "design", "codex")
JOB_STATES = ("running", "waiting", "built", "done", "failed", "stopped")
FINISHED_JOB_STATES = ("done", "failed", "stopped")
INCIDENT_STOP = "stop"
IMMEDIATE_FIX = "immediate fix"
RESULT_STATES = {"DONE": "done", "READY": "waiting", "WAITING": "waiting", "DESIGN": "waiting", "ASK": "waiting", "BLOCKED": "failed", "HANDOFF": "stopped"}
BUILT_LINE = re.compile(r"BUILT (?P<branch>[^\s,]+),? waits on (?P<cards>.+)")
ASK_LINE = re.compile(r"ASK (?P<file>\S+)")
CHANGE_LINE = re.compile(r"^Change:[ \t]*(?P<change>.+?)(?:[ \t]*\(waits on change (?P<after>\d+)\))?[ \t]*$", re.MULTILINE)
BUILT_CARD = re.compile(r"https://[^\s,]+/archives/[^\s,]+/p\d+[^\s,]*|[\w.-]+:j\d+-\d+[\w-]*")
EXEMPT_CLASSES = ("pure port", "test-only", "ci-only", "flake fix", "incident fix")
TAP_LABELS = ("Approve", "Hold")
TOOLING = "Drive tooling:"
GUARDED_OWNER = "forge-ai"
NO_TAP_OWNER = "yasyf"
MONOREPO = "Forge-AI/monorepo"
ROLES = ("communicator", "coordinator", "meta")
HOLDERS = ("communicator", "meta")
HANDLER_ENV = "LONG_RUNNING_HANDLER"
LANE_ROLES = {"communicator": "communicator", "root": "coordinator", "meta": "meta"}
DRIVE_LANES = ("root", "communicator", "coordinator", "meta", "handler", "builder", "reader", "designer", "root-tooling", "root-skill", "root-dashboard", "watch")
NUMBERED_LANE = re.compile(r"(?P<stem>.+)-\d+")
LOCAL_ROUTE = "local"
DISPATCH_PREFIX = "ctx_"

PR_URL = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
RELEASE_WAIT = re.compile(r"release:(?P<service>[\w.-]+):(?P<pr>[^/\s#]+/[^/\s#]+#\d+)")
PR_KEY = re.compile(r"([^/\s#]+)/([^/\s#]+)#(\d+)")
PR_REF = re.compile(rf"{PR_URL.pattern}|{PR_KEY.pattern}|#(\d+)\b")
VERDICT = re.compile(r"(?:^|[;.]\s+)(NOT )?READY\b")
VERDICT_REASON = re.compile(r":\s+")
CARD_LINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:\?thread_ts=([\d.]+))?")
SLACK_SOURCE = re.compile(r"slack:([A-Z0-9]+)/(\d{10}\.\d{6})")
OFFSET = re.compile(r"\+(\d+)([mhd])")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?:\s*([AaPp])\.?[Mm]\.?)?")
OFFSET_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
AWAY_AFTER = timedelta(minutes=60)
PART_CHARS = 80
REBUILT_ASKS = ".rebuilt"
NOT_LANE = re.compile(r"[^a-z0-9]+")
WAITS_ID = re.compile(r"([\w.-]+):(j\d+)-(\d+)")
ORDER_LIST = re.compile(r"[\s,]+")
ROUTED_KINDS = ("owner", "answer", "posted", "refused")
APPROVALS_LABELLED = ".approvals-labelled"


class DriveError(Exception):
    pass


def root() -> Path:
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit)
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg) / "long-running"
    return Path.home() / ".local" / "state" / "long-running"


def scratch() -> bool:
    """Whether LONG_RUNNING_STATE moves the state root, as a proof's or a test's drive does; no real drive sets it."""
    return bool(os.environ.get("LONG_RUNNING_STATE"))


def drive_dir(name: str) -> Path:
    return root() / name


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def state_path(name: str) -> Path:
    return drive_dir(name) / "drive.json"


def events_path(name: str) -> Path:
    return drive_dir(name) / "events.jsonl"


def project_lane(project: str) -> str:
    """The cci lane of a project's sub-coordinator: its name lowercased, each run of other characters a dash."""
    return NOT_LANE.sub("-", project.lower()).strip("-")


def watch_suffix(project: str | None) -> str:
    return f"-{project_lane(project)}" if project else ""


def pid_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watchd{watch_suffix(project)}.pid"


def monitor_pid_path(name: str) -> Path:
    return drive_dir(name) / "watch.pid"


def watch_log_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watch{watch_suffix(project)}.log"


def cursor_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watch{watch_suffix(project)}.cursor"


def held_path(name: str) -> Path:
    return drive_dir(name) / "watch.held"


def cards_path(name: str) -> Path:
    return drive_dir(name) / "cards.json"


def replies_path(name: str, job_id: str) -> Path:
    return drive_dir(name) / "jobs" / f"{job_id}.replies.jsonl"


def report_path(name: str, job_id: str) -> Path:
    return drive_dir(name) / "reports" / f"{job_id}.md"


def repro_path(name: str, job_id: str, suffix: str) -> Path:
    return drive_dir(name) / "repro" / f"{job_id}{suffix}"


def report_text(name: str, job_id: str, job: dict) -> str:
    """A job's whole return as kept in the drive, or a remote lane's latest report, or nothing."""
    path = report_path(name, job_id)
    return path.read_text() if path.exists() else job.get("report") or ""


def keep_reply(name: str, job_id: str, message: dict) -> None:
    """Append one message a remote lane sent to its job's history, which a later result never replaces."""
    path = replies_path(name, job_id)
    path.parent.mkdir(exist_ok=True)
    fields = ("created_at", "id", "type", "subject", "body")
    with open(path, "a") as handle:
        handle.write(json.dumps({field: message[field] for field in fields}) + "\n")


def replies(name: str, job_id: str) -> list[dict]:
    """The job's kept messages, oldest first, each once: a watch stopped between keeping a message and saving its cursor keeps it again."""
    path = replies_path(name, job_id)
    kept = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return list({reply["id"]: reply for reply in kept}.values())


def names() -> list[str]:
    base = root()
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if (entry / "drive.json").is_file())


def exists(name: str) -> bool:
    return state_path(name).is_file()


def now() -> datetime:
    return datetime.now().astimezone()


def stamp(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat(timespec="seconds")


def parse_time(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.astimezone()


def parse_due(text: str, reference: datetime | None = None) -> datetime:
    """Parse a due time: "HH:MM" or the "3:30 AM" the drive prints (today, or tomorrow once passed), "+30m"/"+2h"/"+1d", or ISO 8601."""
    base = reference or now()
    if offset := OFFSET.fullmatch(text.strip()):
        return (base + timedelta(**{OFFSET_UNITS[offset[2]]: int(offset[1])})).replace(microsecond=0)
    if clock := CLOCK.fullmatch(text.strip()):
        hour = int(clock[1]) % 12 + (12 if clock[3].upper() == "P" else 0) if clock[3] else int(clock[1])
        due = base.replace(hour=hour, minute=int(clock[2]), second=0, microsecond=0)
        return due if due > base else due + timedelta(days=1)
    try:
        return parse_time(text.strip())
    except ValueError:
        raise DriveError(f"cannot read due time {text!r}: use HH:MM, 3:30 AM, +30m, +2h or ISO 8601") from None


def pr_key(ref: str) -> str:
    """The "owner/name#123" key of a GitHub PR url, or of a key already in that form."""
    match = PR_URL.search(ref) or PR_KEY.fullmatch(ref.strip())
    if not match:
        raise DriveError(f"not a GitHub PR url: {ref}")
    return f"{match[1]}/{match[2]}#{match[3]}"


def pr_url(key: str) -> str:
    repo, number = key.split("#")
    return f"https://github.com/{repo}/pull/{number}"


def new_drive(
    name: str, role: str, session: str | None, cwd: str, channel: str | None, terminal: str | None = None
) -> dict:
    return {
        "name": name,
        "roles": {role: {"session": session, "terminal": terminal, "cwd": cwd, "started": stamp()}},
        "subcoordinators": {},
        "handlers": {},
        "slack": {"channel": channel, "page": None, "watch": []},
        "owner": {"heard": None, "away": False},
        "orders": {},
        "jobs": {},
        "prs": {},
        "answers": {},
        "stand_ins": {},
        "held": {},
    }


def saved(name: str) -> dict:
    """A drive as saved, with the sessions bound to it: one saved with a single driver session reads as that session coordinating."""
    try:
        data = json.loads(state_path(name).read_text())
    except FileNotFoundError:
        raise DriveError(f"no drive named {name}") from None
    if "driver" in data:
        data["roles"] = {"coordinator": data.pop("driver")}
    data.setdefault("subcoordinators", {})
    data.setdefault("handlers", {})
    for held in data["roles"].values():
        held.setdefault("terminal", None)
    return data


def load(name: str) -> dict:
    """Read a drive, bringing a drive saved by an older install up to the fields this one reads, from what is on this Mac alone."""
    data = saved(name)
    for order in data["orders"].values():
        if "parts" not in order:
            whole = whole_order(order["text"])
            order["parts"] = {whole: new_part()}
            for job_id in order["jobs"]:
                data["jobs"][job_id].setdefault("parts", [whole])
        if "promised" not in order:
            order.update(promised=order.get("due") and order["updated"], next_due=None, kept=None, withdrawn=None)
        order.setdefault("next_set", None)
        order.setdefault("incident", False)
        order.pop("milestone", None)
        order.setdefault("project", None)
        order.setdefault("workstream", None)
        order.setdefault("kind", None)
        order.setdefault("complex", False)
        order.setdefault("options_only", False)
        order.setdefault("once", False)
        order.setdefault("thread", None)
        order.setdefault("from_thread", None)
        order.setdefault("releases", {})
        order.setdefault("shipped", {})
        order.setdefault("reporter", None)
        order.setdefault("owner", "communicator" if order["text"].startswith(TOOLING) else None)
        order["thread"] = dm_thread(data, order)
        for part in order["parts"].values():
            part.setdefault("design", None)
            part.setdefault("after", None)
    mail = data.pop("lane_mail", {})
    for job in data["jobs"].values():
        job.setdefault("carries", {})
        job["mail_seen"] = max(job.get("mail_seen", 0), mail.get(job.get("run"), 0))
        job.setdefault("waiting_said", [])
        job.setdefault("waiting_claims", {})
        job.setdefault("waits", [])
        job.setdefault("route", None)
        job.setdefault("may_settle", None)
        job.setdefault("due", None)
        job.setdefault("checks", {})
        job.setdefault("undelivered", [])
        if job.get("ask") and any(reported(text.lstrip()) for text in (job["said"] or "", job["report"] or "")):
            job["ask"] = None
        if job.get("ask"):
            job.setdefault("ask_at", job["updated"])
        if (job.get("agent") or "").startswith(DISPATCH_PREFIX):
            job.update(local_lane(job["agent"]), agent=None)
        if "questions" not in job:
            asked, card = job.get("question"), job.get("card")
            job["questions"] = [new_question(asked, job["said"], job["updated"], job.get("unsettled"), card)] if asked or card else []
    for order_id, order in data["orders"].items():
        if "joins" not in order:
            carriers = (data["jobs"][job_id]["order"] for job_id in order["jobs"]) if order["incident"] else ()
            order["joins"] = next((other for other in carriers if other != order_id and data["orders"][other]["incident"]), None)
    for pr in data["prs"].values():
        waits = pr.get("waits")
        pr["waits"] = waits if isinstance(waits, list) else [waits] if waits else []
        pr.setdefault("conflict", False)
        pr.setdefault("failed_checks", [])
        pr.setdefault("hold_label", False)
        pr.setdefault("ready_said", None)
        pr.setdefault("pulled", None)
        pr.setdefault("lander", None)
        pr.setdefault("ejected", False)
    if any("verdict" not in pr for pr in data["prs"].values()):
        judge_kept(data)
    data.setdefault("answers", {})
    for answer in data["answers"].values():
        answer.setdefault("jobs", {})
    data.setdefault("stand_ins", {})
    data.setdefault("held", {})
    if (pictures := drive_dir(name) / "pictures").is_dir() and not (pictures / picture.SOURCED).exists():
        picture.fill(pictures, [*map(Path.read_text, sorted((drive_dir(name) / "asks").glob("*.md"))), *pr_bodies(name)])
    if cards_path(name).exists() and not (drive_dir(name) / "asks" / REBUILT_ASKS).exists():
        rebuild_asks(name, data)
    if "coordinator" in data["roles"] and not (drive_dir(name) / APPROVALS_LABELLED).exists():
        label_approvals(name, data["roles"]["coordinator"]["cwd"])
    if not data.get("may_settle_skips_approvals"):
        for job in data["jobs"].values():
            job["may_settle"] = None
        data["may_settle_skips_approvals"] = True
    for job in data["jobs"].values():
        for question in job["questions"]:
            if question["waits"] and not question["file"] and (kept := rebuilt_ask(name, question["waits"])).exists():
                question["file"] = str(kept)
    return data


def rebuilt_ask(name: str, waits: str) -> Path:
    return drive_dir(name) / "asks" / f"{waits.partition(':')[2]}.md"


def label_approvals(name: str, cwd: str) -> None:
    """Label as approvals the taps on PR cards his answers in the coordinator's checkout recorded before `drive answer` labelled them, so the settled check never counts one; marked so this runs once."""
    from drive import settled

    settled.backfill(cwd)
    (drive_dir(name) / APPROVALS_LABELLED).touch()


def rebuild_asks(name: str, data: dict) -> None:
    """Keep an ASK file, rebuilt from the card the drive stored, for each of his open question cards whose job's question keeps none, so `drive cards redraw` redraws it; marked so this runs once, from cards read live."""
    from drive import limits

    if (stored_cards := json.loads(cards_path(name).read_text()))["source"] != "live":
        return
    unkept = {question["waits"] for job in data["jobs"].values() for question in job["questions"] if question["waits"] and not question["file"]}
    (asks := drive_dir(name) / "asks").mkdir(exist_ok=True)
    for stored in stored_cards["questions"]:
        if (waits := stored["card"].get("waits")) in unkept and not (kept := rebuilt_ask(name, waits)).exists():
            kept.write_text(limits.rebuilt(stored["card"]))
    (asks / REBUILT_ASKS).touch()


def pr_bodies(name: str) -> list[str]:
    if not cards_path(name).exists():
        return []
    return [row.get("description") or "" for card in json.loads(cards_path(name).read_text())["pr_cards"] for row in card["card"].get("prs") or []]


def dm_thread(data: dict, order: dict) -> str | None:
    """The order's thread, or, when it is the ts of the order's own post in a channel that is neither his DM nor one the drive watches, the thread its posts in his DM hang from."""
    posts = [(link[1], f"{link[2]}.{link[3]}", link[4]) for part in order["parts"].values() if (link := CARD_LINK.search(part["posted"] or ""))]
    ours = {data["slack"]["channel"], *data["slack"].get("watch", [])}
    if not any(ts == order["thread"] and channel not in ours for channel, ts, _ in posts):
        return order["thread"]
    return next((thread or ts for channel, ts, thread in posts if channel == data["slack"]["channel"]), None)


def job_parts(job: dict, order_id: str) -> list[str]:
    """The parts of an order a job delivers: its own order's, or those it carries for another."""
    return job["parts"] if job["order"] == order_id else job["carries"].get(order_id, [])


def carry(data: dict, job_id: str, order_id: str, parts: list[str]) -> None:
    job, order = data["jobs"][job_id], data["orders"][order_id]
    job["carries"][order_id] = list(dict.fromkeys([*job["carries"].get(order_id, []), *parts]))
    if job_id not in order["jobs"]:
        order["jobs"].append(job_id)
    order["updated"] = stamp()


def drop_carries(data: dict, job_id: str) -> None:
    """A done job stops carrying other orders' parts, so a part it leaves undelivered reads as nobody's again."""
    for order_id in data["jobs"][job_id]["carries"]:
        data["orders"][order_id]["jobs"].remove(job_id)
    data["jobs"][job_id]["carries"] = {}


def agent_job(data: dict, agent: str | None) -> str | None:
    return next((job_id for job_id, job in data["jobs"].items() if agent and job.get("agent") == agent), None)


def result_state(job: dict, line: str) -> str | None:
    """The state a job's returned line moves it to, or None when the line is no result: a reader's answer, its READY or NOT READY verdict on a PR or its DESIGN to done, since a design lane takes its choice on and a reader's READY is a check that passed, never a PR of its own; `BUILT <branch>, waits on <cards>` to built, and an ASK to waiting on his card. A codex job's line is its codex-ask run's, never its relay's: a `BLOCKED` one fails it and any other, an `ASK` included, since a codex lane keeps no card, is done."""
    opening = re.match(r"[A-Z]*", line)[0]
    reader = job["kind"] == "reader"
    if job["kind"] == "codex":
        return "failed" if opening == "BLOCKED" else "done"
    if BUILT_LINE.match(line):
        return "built"
    return "done" if reader and opening in ("DESIGN", "READY") else RESULT_STATES.get(opening, "done" if reader else None)


def reported(line: str) -> bool:
    """Whether a line opens with a result other than an ASK, so a lane that sent it as an Orca question returned that result and asked nothing."""
    opening = re.match(r"[A-Z]*", line)[0]
    return bool(BUILT_LINE.match(line)) or opening in RESULT_STATES and opening != "ASK"


def kept_result(name: str, job_id: str, job: dict, line: str) -> str | None:
    """The result the drive keeps for a job whose agent returned the line: that line when it is an `ASK <path>` naming the block `drive job ask` kept for one of its questions, else its kept report's first line, which is its result whatever line the agent ended on, or None when it keeps neither."""
    if (asked := ASK_LINE.fullmatch(line)) and kept_in(job, Path(asked["file"])):
        return line
    kept = report_text(name, job_id, job).strip().splitlines()
    return kept[0].strip() if kept else None


def kept_return(name: str, job_id: str, job: dict, line: str) -> bool:
    """Whether the line a job's agent returned is the result the drive keeps for it, word for word."""
    return kept_result(name, job_id, job, line) == line


def take_result(data: dict, job_id: str, line: str) -> None:
    """Move a running job to the state its result line opens with, with that line as its said line, built on its branch and waiting on each card a BUILT line names, and keep each verdict a reader's answer gives on the PR it names; an ASK waiting on his card keeps the said line drive job ask set, the question's title."""
    job = data["jobs"][job_id]
    taken = result_state(job, line)
    if job["state"] != "running" or not taken:
        return
    job.update(state=taken, updated=stamp())
    if taken != "waiting" or not re.match(r"ASK\b", line):
        job["said"] = line
    if built := BUILT_LINE.match(line):
        job.update(branch=built["branch"], waits=list(dict.fromkeys([*job["waits"], *BUILT_CARD.findall(built["cards"])])))
    if taken == "done":
        drop_carries(data, job_id)
    if taken == "done" and job["kind"] == "reader":
        judge(data, job_id, line, job["updated"])
    if taken == "done" and job["kind"] == "builder":
        for design_id, design in data["jobs"].items():
            if design["kind"] == "design" and design["state"] == "waiting" and design_builder(data, design_id) == job_id:
                design.update(state="done", said=f"built by {job_id}: {line}", updated=job["updated"])


def add_changes(data: dict, job_id: str, report: str) -> None:
    """Make each change a design job's DESIGN return lists on its `Change:` lines, when it lists more than one, a part of the design's order, naming the design and the earlier change it waits on, so the watch says ORPHAN for a change no builder carries; a design of one change is its own part."""
    job = data["jobs"][job_id]
    found = [(whole_order(line["change"]), line["after"]) for line in CHANGE_LINE.finditer(report)]
    if job["kind"] != "design" or not report.lstrip().startswith("DESIGN") or len(found) < 2:
        return
    order = data["orders"][job["order"]]
    for change, after in found:
        order["parts"].setdefault(change, new_part(job_id, found[int(after) - 1][0] if after else None))
    order["updated"] = stamp()


def design_builder(data: dict, design_id: str) -> str | None:
    """The builder carrying a design job's parts, or a change its DESIGN listed, on its order, the one that returned DONE first, else one still at work, since a design ends once its builder takes it on: its cards answered or built on meanwhile."""
    design = data["jobs"][design_id]
    changes = {part for part, held in data["orders"][design["order"]]["parts"].items() if held["design"] == design_id}
    builders = [
        (job["state"] != "done", job_id)
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder"
        and job["order"] == design["order"]
        and job["state"] not in ("failed", "stopped")
        and (set(design["parts"]) <= set(job["parts"]) or changes & set(job["parts"]))
    ]
    return min(builders)[1] if design["kind"] == "design" and builders else None


def verdicts(line: str) -> list[tuple[bool, str, str]]:
    """Each verdict a reader's line gives on PRs, as `READY <pr>…` or `NOT READY <pr>…: <why>` opening the line, a `; ` part or a sentence: whether it passed, the words naming its PRs, those before its first `: `, so a sibling PR its reason cites is not judged, and its reason."""
    marks = list(VERDICT.finditer(line))
    found = []
    for mark, after in pairwise([*marks, None]):
        clause = line[mark.end() : after.start() if after else None].split(";")[0].strip()
        named, *reason = VERDICT_REASON.split(clause, maxsplit=1)
        found.append((not mark[1], named, reason[0] if reason else clause))
    return found


def named_prs(data: dict, order_id: str, words: str) -> list[str]:
    """The drive PRs the words name by link, key or bare #N; a bare #N that drive PRs in several repositories carry names the one on the order."""
    folded = {key.casefold(): key for key in data["prs"]}
    keys = []
    for found in PR_REF.finditer(words):
        if not (number := found.groups()[-1]):
            keys.append(folded.get(pr_key(found[0]).casefold()))
            continue
        shared = [key for key in data["prs"] if key.rpartition("#")[2] == number]
        keys += shared if len(shared) == 1 else [key for key in shared if data["prs"][key]["order"] == order_id]
    return [key for key in dict.fromkeys(keys) if key]


def judge(data: dict, job_id: str, line: str, at: str, kept: bool = False) -> None:
    """Keep each verdict a reader's line gives on the reader job, on the head it was started on for that PR, else the PR's head now, or no head for an answer kept before verdicts carried their head, and on the PR as its newest verdict, unsaid until the watch acts on it."""
    job = data["jobs"][job_id]
    for passed, named, why in verdicts(line):
        for key in named_prs(data, job["order"], named):
            head = job["checks"].setdefault(key, None if kept else data["prs"][key]["head"])
            data["prs"][key]["verdict"] = {"job": job_id, "head": head, "ready": passed, "why": why, "at": at, "said": False}


def judge_kept(data: dict) -> None:
    """Give each PR of a drive kept before verdicts carried their head the verdict its readers' kept answers leave standing, each answer in the order it came, on the head its reader recorded or else on no head until `date_verdict` reads the PR from GitHub; a standing NOT READY on an open PR stays unsaid, so the watch names its owner once. It reads nothing from GitHub, since every verb and hook loads every drive."""
    for pr in data["prs"].values():
        pr.pop("not_ready", None)
        pr.update(verdict=None, check=None)
    answered = [(job_id, job) for job_id, job in data["jobs"].items() if job["kind"] == "reader" and job["state"] == "done" and job["said"]]
    for job_id, job in sorted(answered, key=lambda found: parse_time(found[1]["updated"])):
        judge(data, job_id, job["said"], job["updated"], kept=True)
    for pr in data["prs"].values():
        if verdict := pr["verdict"]:
            verdict["said"] = verdict["ready"] or pr["state"] != "open"


def date_verdict(data: dict, key: str, head: str, committed: str | None) -> None:
    """Put a PR's verdict kept on no head on the head GitHub reads for it now, when GitHub reports that head committed by the verdict's time; one committed after leaves the verdict on an older head."""
    verdict = data["prs"][key]["verdict"]
    if verdict and verdict["head"] is None and committed and parse_time(committed) <= parse_time(verdict["at"]):
        verdict["head"] = data["jobs"][verdict["job"]]["checks"][key] = head


def check_status(pr: dict) -> str:
    """Where his-words check stands on a PR's head: passed or rework by a verdict on this head, recheck under a NOT READY on an older head, else checking."""
    verdict = pr["verdict"]
    if verdict and verdict["head"] == pr["head"]:
        return "passed" if verdict["ready"] else "rework"
    return "recheck" if verdict and not verdict["ready"] else "checking"


def local_lane(dispatch: str) -> dict:
    """The fields of a job whose Orca worker runs on this Mac: a lane read through its Dispatch like a Sprite's, with no Sprite."""
    return {"where": "remote", "route": LOCAL_ROUTE, "dispatch": dispatch, "workspace": None, "environment": None}


def on_mac(job: dict) -> bool:
    return job["route"] == LOCAL_ROUTE


def lane_died(job: dict, dispatch: str, why: str, moment: datetime) -> bool:
    """Record once that the remote lane on this dispatch died or finished, so it takes no more messages; whether this call recorded it."""
    deaths = job.setdefault("deaths", [])
    if job["dispatch"] != dispatch or any(death["dispatch"] == dispatch for death in deaths):
        return False
    deaths.append({"dispatch": dispatch, "workspace": job["workspace"], "why": why, "at": stamp(moment)})
    job["updated"] = stamp(moment)
    return True


def whole_order(text: str) -> str:
    return " ".join(text.split())[:PART_CHARS]


def new_part(design: str | None = None, after: str | None = None) -> dict:
    return {"posted": None, "orphan_said": None, "design": design, "after": after}


def reporter(link: str) -> dict:
    """The bug report an order fixes, as the Slack thread its one fix-live note goes in, read from its permalink, a reply's carrying its thread's ts, or from slack:<channel>/<thread ts>, with no Slack call, since the bot may not be in that channel; none told yet."""
    if found := CARD_LINK.search(link):
        channel, thread = found[1], found[4] or f"{found[2]}.{found[3]}"
    elif found := SLACK_SOURCE.fullmatch(link):
        channel, thread = found[1], found[2]
    else:
        raise DriveError(f"--reporter {link}: refused, name the bug report by its Slack permalink or as slack:<channel>/<its thread's ts>")
    return {"link": link, "channel": channel, "thread": thread, "told": None, "tell_said": None}


def told_in_thread(reporter: dict) -> bool:
    """Whether the fix-live note went in the reporter's own thread, not to him in its place."""
    return bool((found := CARD_LINK.search(reporter["told"] or "")) and found[1] == reporter["channel"])


def new_job(order_id: str, kind: str, goal: str, parts: list[str], created: str) -> dict:
    return {
        "order": order_id,
        "kind": kind,
        "goal": goal,
        "agent": None,
        "session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "parts": parts,
        "carries": {},
        "questions": [],
        "where": "local",
        "route": None,
        "repo": None,
        "branch": None,
        "worktree": None,
        "prs": [],
        "checks": {},
        "waits": [],
        "waiting_said": [],
        "waiting_claims": {},
        "mail_seen": 0,
        "undelivered": [],
        "state": "running",
        "said": None,
        "may_settle": None,
        "due": None,
        "created": created,
        "updated": created,
    }


def new_question(file: str | None, title: str | None, asked: str, unsettled: str | None = None, card: str | None = None) -> dict:
    return {"file": file, "title": title, "asked": asked, "unsettled": unsettled, "waits": None, "card": card}


def kept_in(job: dict, file: Path) -> dict | None:
    """The job's newest question kept in this ASK file."""
    return next((question for question in reversed(job["questions"]) if question["file"] and Path(question["file"]).resolve() == file.resolve()), None)


def same_question(one: str | None, other: str | None) -> bool:
    """Whether two questions ask him the same thing: the same words, whatever their case and spacing."""
    return bool(one and other) and " ".join(one.split()).casefold() == " ".join(other.split()).casefold()


def save(name: str, data: dict) -> None:
    path = state_path(name)
    tmp = path.with_name("drive.json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def flocked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def locked(name: str) -> AbstractContextManager[None]:
    return flocked(drive_dir(name) / ".lock")


def binding(name: str) -> AbstractContextManager[None]:
    """Hold the coordinator terminal's one Run binding for a bind and the call that needs it: a launch's worker-start, a reply."""
    return flocked(drive_dir(name) / ".binding.lock")


@contextmanager
def edit(name: str) -> Iterator[dict]:
    """Load the drive under its lock, yield it for changes, and save it atomically on exit."""
    with locked(name):
        data = load(name)
        yield data
        save(name, data)


@contextmanager
def edit_or_create(name: str, fresh: dict) -> Iterator[dict]:
    """Like edit, but start from fresh when the drive does not exist yet; the lock covers the check too."""
    with locked(name):
        data = load(name) if exists(name) else fresh
        yield data
        save(name, data)


def create(data: dict) -> None:
    with locked(data["name"]):
        save(data["name"], data)


def next_id(records: dict, prefix: str) -> str:
    taken = [int(key[len(prefix) :]) for key in records if key.startswith(prefix) and key[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1}"


def session_role(data: dict, session: str | None) -> str | None:
    """The role a session holds; a sub-coordinator's is coordinator and a handler's the communicator's, so every rule of that role binds it too."""
    role = next((role for role, held in data["roles"].items() if session and held["session"] == session), None)
    if role:
        return role
    if session_project(data, session):
        return "coordinator"
    return "communicator" if handler_id(data, session) else None


def handler_id(data: dict, session: str | None) -> str | None:
    """The handler a `claude --bg` session runs for the communicator, as its SessionStart hook bound it."""
    return next((handler for handler, held in data["handlers"].items() if session and held["session"] == session), None)


def own_transcript(data: dict, job: dict) -> str | None:
    """The transcript of a job a handler holds, its own claude --bg session's rather than one beside the communicator's: the bound handler's while it runs, the job's own once it ended."""
    return job.get("transcript") or (data["handlers"].get(job.get("agent")) or {}).get("transcript")


def session_project(data: dict, session: str | None) -> str | None:
    """The project a session is the sub-coordinator for, or None for any other session."""
    return next((project for project, held in data["subcoordinators"].items() if session and held["session"] == session), None)


def bound_sessions(data: dict) -> dict[str, dict]:
    """Every session bound to the drive: each role's by the role's name, each sub-coordinator's as `sub-coordinator <its project>`."""
    return data["roles"] | {f"sub-coordinator {project}": held for project, held in data["subcoordinators"].items()}


def projects(data: dict) -> list[str]:
    return sorted({order["project"] for order in data["orders"].values() if order["project"]})


def order_lane(data: dict, order: dict) -> str:
    """The cci lane of the coordinator session that hears an order: the sub-coordinator's for its project, else root."""
    return project_lane(order["project"]) if order["project"] in data["subcoordinators"] else "root"


def heard_lane(data: dict, order_id: str) -> str:
    """The cci lane of the coordinator session hearing an order now, which an event about it is written with."""
    return order_lane(data, data["orders"][order_id]) if data["subcoordinators"] else "root"


def card_threads(data: dict, order: dict) -> list[tuple[str, str]]:
    """The channel and thread of each card the order's jobs drew, where his answers to that card go."""
    links = (CARD_LINK.search(question["card"]) for job_id in order["jobs"] for question in data["jobs"][job_id]["questions"] if question["card"])
    return list(dict.fromkeys((link[1], link[4] or f"{link[2]}.{link[3]}") for link in links if link))


def incident_orders(data: dict, order_id: str) -> list[str]:
    """The orders of the order's incident, the one an alert's order joined with --joins first, then every alert joined to it; an order nothing joined and joining none alone."""
    head = data["orders"][order_id]["joins"] or order_id
    return [head, *(other_id for other_id, other in data["orders"].items() if other["joins"] == head)]


def thread_order(data: dict, thread: str | None) -> str | None:
    """The newest order in a thread of his, its own or the thread of a card its jobs drew, or None when no order is in it."""
    return next(
        (order_id for order_id, order in reversed(data["orders"].items()) if thread and thread in (order["thread"], *(ts for _, ts in card_threads(data, order)))),
        None,
    )


def report_order(data: dict, attrs: dict) -> str | None:
    """The order a report is about: the one its order attr names, else the first its close attr names, else its job's, else the newest in its thread."""
    named = [attrs.get("order"), *closed_orders(attrs), (data["jobs"].get(attrs.get("job") or "") or {}).get("order")]
    return next((order_id for order_id in named if order_id in data["orders"]), None) or thread_order(data, attrs.get("thread"))


def serving_lane(data: dict, kind: str, attrs: dict) -> str | None:
    """The cci lane of the session a communicator's owner, answer, posted or refused record serves: the meta session's for an order it holds, else the coordinator session hearing the order of the job its attr job or waits id names, else of the newest order in its thread; None for another kind, or a record naming neither."""
    if kind not in ROUTED_KINDS:
        return None
    job = data["jobs"].get(named_job(data, attrs))
    order_id = job["order"] if job else thread_order(data, attrs.get("thread"))
    if order_id is None:
        return None
    order = data["orders"][order_id]
    return "meta" if order["owner"] == "meta" else order_lane(data, order)


def named_job(data: dict, attrs: dict) -> str | None:
    """The job of this drive a record's attr job or waits id names."""
    named = WAITS_ID.fullmatch(attrs.get("waits") or "")
    job_id = attrs.get("job") or (named[2] if named and named[1] == data["name"] else None)
    return job_id if job_id in data["jobs"] else None


def routed(drives: dict[str, dict], session: str | None, kind: str, attrs: dict, to: list[str]) -> list[str]:
    """Where a drive session's record goes: a defect to the top coordinator, the communicator or the meta session to the one holding drive tooling unless that is the poster, and the communicator's owner, answer, posted or refused record for a coordinator lane to the session hearing the order it serves, its owner record carrying a ruling to the meta session as well, which rewrites the skill while the coordinator applies it to the live work."""
    made = []
    for entry in to:
        lane, _, name = entry.partition("@")
        if not (data := drives.get(name)):
            made.append(entry)
            continue
        role = session_role(data, session)
        communicates = role == "communicator"
        if kind == "defect" and lane in ("root", *HOLDERS) and (holder := tooling_holder(data)) != role:
            lane = holder
        elif communicates and lane in ("root", *map(project_lane, data["subcoordinators"])) and (serving := serving_lane(data, kind, attrs)):
            lane = serving
        made.append(f"{lane}@{name}")
        if communicates and kind == "owner" and attrs.get("ruling") and "meta" in data["roles"]:
            made.append(f"meta@{name}")
    return list(dict.fromkeys(made))


def bound_drives(names: list[str]) -> dict[str, dict]:
    """Each drive of names, by name, whose records a session's post is routed in."""
    return {name: load(name) for name in names}


def hears(data: dict, order: dict, project: str | None) -> bool:
    """Whether the coordinator session for project hears an order: a sub-coordinator its project's, the top coordinator every other one, unfiled or filed under a project no sub-coordinator is bound for."""
    return order["project"] == project if project else order["project"] not in data["subcoordinators"]


def agent_gone(data: dict, job: dict) -> bool:
    """Whether the job's agent ended with its session, which no role holds any more."""
    return session_role(data, job.get("session")) is None


def tooling_holder(data: dict) -> str:
    """The session holding drive tooling: the meta session once one is bound, else the communicator, as before the meta session existed."""
    return "meta" if "meta" in data["roles"] else "communicator"


def holder_lane(order: dict) -> str | None:
    """The lane of the session holding an order whose watch lines, jobs, agents and reports are its own rather than a coordinator's: the meta session's for a drive tooling order or one a brief serving drive tooling gave it, the communicator's for its read-only question or drive tooling while no meta session is bound; None for a coordinator's."""
    return order["owner"] if order["owner"] in HOLDERS else None


def title(role: str) -> str:
    """A role as a sentence names it, the meta session by its full name."""
    return "meta session" if role == "meta" else role


def held_elsewhere(data: dict, order_id: str, role: str | None) -> str | None:
    """Why a session's role may not act on an order another session of the drive holds, naming the holder and its session; an order from before owners is held by the first role to add a job to it."""
    holder = data["orders"][order_id]["owner"]
    if role is None or holder in (None, role):
        return None
    session = (data["roles"].get(holder) or {}).get("session") or "not started"
    return f"{order_id} is the {title(holder)}'s order, session {session}: only the {title(holder)} adds its jobs, starts its agents and posts its reports; send the {title(holder)} a record instead"


def last_started(data: dict) -> str:
    return max(held["started"] for held in data["roles"].values())


def session_drives(session: str) -> list[str]:
    """The drives that bind the session, a handler's included, the newest binding first, read from each drive's saved bindings alone, so a call loads no drive it is not bound to."""
    bound = []
    for name in names():
        data = saved(name)
        bound += [(held["started"], name) for held in [*bound_sessions(data).values(), *data["handlers"].values()] if held["session"] == session]
    return list(dict.fromkeys(name for _, name in sorted(bound, reverse=True)))


def resolve(name: str | None = None) -> str:
    """The drive a verb acts on: --drive, $LONG_RUNNING_DRIVE, this session's drive, or the only drive."""
    chosen = name or os.environ.get("LONG_RUNNING_DRIVE")
    if chosen:
        if not exists(chosen):
            raise DriveError(f"no drive named {chosen}")
        return chosen
    if (session := os.environ.get("CLAUDE_CODE_SESSION_ID")) and (bound := session_drives(session)):
        return bound[0]
    available = names()
    if len(available) == 1:
        return available[0]
    if not available:
        raise DriveError("no drives; start one with: drive start <name> --role coordinator")
    raise DriveError(f"several drives, pass --drive: {', '.join(available)}")


def append_event(name: str, kind: str, **fields: object) -> dict:
    event = {"kind": kind, "at": stamp(), **fields}
    with open(events_path(name), "a") as handle:
        handle.write(json.dumps(event) + "\n")
    return event


def events_end(name: str) -> int:
    path = events_path(name)
    return path.stat().st_size if path.exists() else 0


def watch_cursor(name: str, project: str | None = None) -> int:
    """The events offset the drive's watch, or a project's, has read up to: for a watch that never ran, the log's end, or for a project's where the log ended as its sub-coordinator was first bound."""
    path = cursor_path(name, project)
    if path.exists():
        return int(path.read_text())
    return load(name)["subcoordinators"][project]["from"] if project else events_end(name)


def save_watch_cursor(name: str, offset: int, project: str | None = None) -> None:
    path = cursor_path(name, project)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(f"{offset}\n")
    os.replace(tmp, path)


def take_held_lines(name: str) -> list[str]:
    """The lines a watch run under a Monitor held for its next batch when it stopped, once, so the watch process delivers them."""
    path = held_path(name)
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    path.unlink()
    return lines


def read_events(name: str, offset: int = 0) -> Iterator[tuple[dict, int]]:
    """Yield each complete event after byte offset, paired with the offset just past it."""
    path = events_path(name)
    if not path.exists():
        return
    with open(path, "rb") as handle:
        handle.seek(offset)
        for line in handle:
            if not line.endswith(b"\n"):
                return
            offset += len(line)
            if line.strip():
                yield json.loads(line), offset


def owner_away(data: dict, moment: datetime) -> bool:
    """Whether he is away: he said so, or has written nothing for an hour; a drive that never heard him counts him present."""
    owner = data.get("owner") or {}
    heard = owner.get("heard")
    return bool(owner.get("away")) or bool(heard) and moment - parse_time(heard) >= AWAY_AFTER


def open_orders(data: dict) -> dict[str, dict]:
    return {key: order for key, order in data["orders"].items() if order["state"] not in CLOSED_ORDER_STATES}


def open_incidents(data: dict, order_ids: list[str]) -> list[str]:
    return [order_id for order_id in order_ids if data["orders"][order_id]["incident"] and data["orders"][order_id]["state"] not in CLOSED_ORDER_STATES]


def hurts_now(order: dict) -> bool:
    """Whether the bug an order fixes hurts someone now, as an incident or a bug someone reported does, so its immediate fix ships at once while he picks the proper fix."""
    return bool(order["incident"] or order["reporter"])


def running_stops(data: dict, order_id: str) -> list[str]:
    return [job_id for job_id in data["orders"][order_id]["jobs"] if INCIDENT_STOP in data["jobs"][job_id]["parts"] and data["jobs"][job_id]["state"] not in FINISHED_JOB_STATES]


def incident_job(data: dict, name: str, order_id: str, part: str, created: str) -> str:
    order = data["orders"][order_id]
    job_id = next_id(data["jobs"], "j")
    data["jobs"][job_id] = new_job(order_id, "codex", f"{part}: {whole_order(order['text'])}", [part], created)
    order["jobs"].append(job_id)
    return f'{job_id} on "{part}": start its agent now, its brief ending Drive: {name}, order {order_id}, job {job_id}'


def promise_thread(data: dict, order: dict) -> tuple[str, str] | None:
    """The channel and thread an order's promised line goes in: its thread, in his DM unless it is the Slack message the order came from or the thread that message is in, as an alert's is or his reply's; else that message's."""
    source = order["from"].removeprefix("slack:").split("/") if order["from"].startswith("slack:") else [None, None]
    thread = order["thread"] or source[1]
    channel = source[0] if thread in (source[1], order["from_thread"]) else data["slack"]["channel"]
    return (channel, thread) if channel and thread else None


def incident_threads(data: dict, order_ids: list[str]) -> list[str]:
    """The thread of each of these incident orders, where the drive says at once that a card of his blocks it."""
    return [where[1] for order_id in order_ids if (where := promise_thread(data, data["orders"][order_id]))]


def owed(order: dict) -> bool:
    """Whether the order owes him a line for a promised time: no drive post in its thread has kept it, and he has not withdrawn it."""
    return bool(order["due"]) and not order["kept"] and not order["withdrawn"]


def owed_at(order: dict) -> str:
    return min((due for due in (order["due"], order["next_due"]) if due), key=parse_time)


def promise(order: dict, due: str, moment: datetime) -> None:
    """Promise a line by due, owed from the next line in the order's thread, the one that tells him the time; a time already owed holds until that line lands."""
    if not owed(order):
        order.update(due=due, promised=stamp(moment), kept=None, withdrawn=None, due_said=None)
    order.update(next_due=due, next_set=stamp(moment))


def remind(order: dict, due: str, moment: datetime) -> None:
    """Set the time an order with no thread wakes the drive at: no post can keep it, so it replaces any time set before and is said once."""
    order.update(due=due, promised=stamp(moment), kept=None, withdrawn=None, due_said=None, next_due=None, next_set=None)


def keep(order: dict, posts: list[datetime], announced: bool = True) -> None:
    """Record the first drive post after the promise as keeping it; a post that lands before the next time starts that time from it."""
    kept = next((post for post in posts if post > parse_time(order["promised"])), None)
    if kept is None:
        return
    upcoming, set_at = order["next_due"], order["next_set"]
    order.update(kept=kept.isoformat(), next_due=None, next_set=None)
    if not announced or not upcoming or kept >= parse_time(upcoming):
        return
    if set_at and kept < parse_time(set_at):
        order.update(due=upcoming, promised=set_at, kept=None, due_said=None, next_due=upcoming)
        keep(order, posts)
        return
    order.update(due=upcoming, promised=kept.isoformat(), kept=None, due_said=None)
    keep(order, posts, announced=False)


def closed_orders(report: dict) -> list[str]:
    """The orders a report's close attr names, separated by commas, spaces or both, as `o1,o2` or `o1 o2`."""
    return [order for order in ORDER_LIST.split(report.get("close") or "") if order]


def keep_closing(order: dict, posts: list[datetime], written: datetime) -> None:
    """Record the line of a report that closes the order as keeping every time it owes: the first drive post after the promise, or the post that told him the time when the report was written before it, since that post is also the order's last line."""
    promised = parse_time(order["promised"])
    kept = next((post for post in posts if post > promised or post == promised >= written), None)
    if kept is not None:
        order.update(kept=kept.isoformat(), next_due=None, next_set=None)


def open_pr_keys(data: dict) -> list[str]:
    return [key for key, pr in data["prs"].items() if pr["state"] == "open"]


def open_prs(data: dict) -> list[dict]:
    return [data["prs"][key] for key in open_pr_keys(data)]


def card_wait(data: dict, wait: str) -> bool:
    """Whether a wait names a card of his, by its Slack link or waits id, rather than a job, a PR or a release of the drive."""
    return wait not in data["jobs"] and wait not in data["prs"] and not RELEASE_WAIT.fullmatch(wait)


def merges_untapped(key: str) -> bool:
    return key.split("/")[0] == NO_TAP_OWNER


def guarded(key: str) -> bool:
    """Whether only his tap or an exempt class lands a PR of this repository, a Forge-AI one, whoever opened it."""
    return key.split("/")[0].lower() == GUARDED_OWNER


def queued_on_tap(key: str) -> bool:
    """Whether his Approve lands the PR with no session turn: a Forge-AI/monorepo one, which the drive's lander queues through stack-enqueue."""
    return key.split("#")[0].lower() == MONOREPO.lower()


def owed_step(pr: dict) -> str | None:
    """What the drive owes a PR once it is green and approved: its landing when an exempt class lands it with no tap, nothing when its author or its own builder lands it, its builder's rework or a reader's recheck while a NOT READY stands and he has not tapped it, and else his card, drawn while his-words check runs."""
    if pr.get("exempt"):
        return "landing"
    if not pr["wants_tap"]:
        return None
    status = check_status(pr)
    return status if status in ("rework", "recheck") and not pr["tap"] else "card"


def new_pr(url: str, job_id: str, order_id: str) -> dict:
    return {
        "url": url,
        "job": job_id,
        "order": order_id,
        "wants_tap": True,
        "tap": None,
        "exempt": None,
        "fundamental": None,
        "state": "open",
        "title": "",
        "head": "",
        "checks": "pending",
        "failed_checks": [],
        "review": "none",
        "conflict": False,
        "hold_label": False,
        "verdict": None,
        "check": None,
        "ready_said": None,
        "pulled": None,
        "draft": False,
        "merged_sha": None,
        "stuck": None,
        "waits": [],
        "lander": None,
        "ejected": False,
        "updated": stamp(),
    }
