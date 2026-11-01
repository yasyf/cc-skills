"""The lanes mover: every lane the drive needs stays alive without the root.

Each tick folds the bus records after the mover's cursor into the `lanes` registry, reads
once which lanes `cci who` lists live, and acts on each desired lane. A lane with no live
process and no record within `STALE_AFTER` has its successor launched from the lane's brief
and newest handoff, and is stood down by one record naming that successor and the PRs it
takes over once the successor is up; a lane whose newest record is an unanswered ask to root
is waiting, and its clock restarts at the answer. A lane past its context line is asked to
rotate and flushes in place: its handoff becomes the newest one and it keeps working. Each
live lane beats in the store's worker table, and a lane it relaunches, holds or retires
leaves that table. A fresh store adopts only the lanes alive at that moment by the same
rule, and a tick whose sources do not all answer acts on nothing. A numbered successor's
`SPAWNED`, a root `<new> replaces <old>`, a root record telling a lane to stand down, the
lane's own stand-down, and a `head` or `opened` record that takes a never-launched lane's
last PR each retire a lane; a `<lane>-handoff` writer's `handoff <lane>: doc <id>` is the
lane's newest handoff. It signals no process: a live lane leaves only
through the record that stands it down.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lr.store import STALE_AFTER, Lane, Store, parse, stamp

SELF = "supervisor"
ROOT = "root"
CURSOR = "lanes"
HOUR = "lanes.hour"
ADOPT_WINDOW = "72h"
STRIKES = 2
LIVE = ("launching", "desired")
HELPER_ROLES = frozenset({"helper", "reader", "watch", "export", "evidence", "handoff", "comms", "triage"})
NUMBERED = re.compile(r"^(?P<family>.+)-(?P<n>\d{1,2})$")
REPLACES = re.compile(r"\b(?P<new>[\w.-]+) replaces (?P<old>[\w.-]+)\b")
HANDED_OFF = re.compile(r"\bhandoff (?P<lane>[\w.-]+): doc (?P<doc>[0-9a-f]{7,40})\b")
CARRIES = ("head", "opened")
WAITS = frozenset({"ask", "blocked", "blocker"})
SETTLED = frozenset({"handoff", "done", "stand-down", "stopped"})
SLICE_STEP = re.compile(r"^Register slice: lane=\S+ step=(\S+)", re.MULTILINE)
STAND_DOWN_LIST = re.compile(r"\bstand[ -]?down\s+(?P<names>[^.(;:\n]+)", re.IGNORECASE)
CARD = re.compile(r"\bcard-[\w.-]*\w")
STRIKE_WINDOW = 2 * STALE_AFTER
PAGE_ANSWER = 2 * STALE_AFTER
LIVE_WITHIN = 4 * STALE_AFTER
OWNER = re.compile(r"\bowner=([\w.-]+)")
PACIFIC = ZoneInfo("America/Los_Angeles")
HANDOFF_BRIEF = Path(__file__).parents[3] / "reference" / "handoff-subagent-brief.md"


class Unread(RuntimeError):
    """A liveness source or the bus did not answer; the tick acts on nothing rather than on half a picture."""


@dataclass(frozen=True)
class Peer:
    lane: str
    live: bool
    seen: datetime
    row: dict


@dataclass(frozen=True)
class Deps:
    drive: str
    checkout: str
    briefs: Path
    records: Callable[[int], list[dict]]
    who: Callable[[set[str] | None], list[dict]]
    tokens: Callable[[dict], int | None]
    transcript: Callable[[dict], Path | None]
    cci: Callable[[list[str]], str]
    launch: Callable[[str, str, str, Path], None]
    withdraw: Callable[[str], None]
    wake: Callable[[str, dict, dict | None], bool]
    rotate_at: int


@dataclass
class Outcome:
    relaunched: list[tuple[str, str, str]] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    woken: list[str] = field(default_factory=list)
    reassign: list[str] = field(default_factory=list)


def pacific(at: datetime) -> str:
    return at.astimezone(PACIFIC).strftime("%-I:%M %p")


def at_of(record: dict) -> datetime:
    return datetime.fromisoformat(record["at"].replace("Z", "+00:00")).astimezone(UTC)


def prs_of(record: dict) -> list[int]:
    return [int(str(ref).rsplit("#", 1)[-1]) for ref in (record.get("refs") or {}).get("prs") or []]


def addressed(record: dict) -> set[str]:
    return {to.split("@", 1)[0].rstrip(":") for to in record.get("to") or []}


def attrs_of(record: dict) -> dict:
    return (record.get("fields") or {}).get("attrs") or {}


def spawned(record: dict) -> tuple[str, str, str, str, str] | None:
    text = record.get("text") or ""
    if record["kind"] != "state" or not text.startswith("SPAWNED "):
        return None
    attrs = attrs_of(record)
    name, brief = text.split()[1], attrs.get("brief") or text.split()[2]
    if not brief.startswith("/") or attrs.get("role") in HELPER_ROLES or name.endswith("-handoff"):
        return None
    return name, brief, attrs.get("model") or "opus", attrs.get("effort") or "xhigh", attrs.get("role") or "lane"


def family(name: str) -> tuple[str, int]:
    match = NUMBERED.match(name)
    return (match["family"], int(match["n"])) if match else (name, 1)


def succeeds(name: str, old: str) -> bool:
    (base, n), (other, later) = family(old), family(name)
    return other == base and later > n


def successor_name(name: str, taken: set[str]) -> str:
    base, n = family(name)
    while f"{base}-{n + 1}" in taken:
        n += 1
    return f"{base}-{n + 1}"


def newest(store: Store, name: str) -> Lane | None:
    lane = store.lane(name)
    while lane and lane.state == "retired" and lane.retired_by and (after := store.lane(lane.retired_by)):
        lane = after
    return lane


def retire(store: Store, name: str, by: str, at: datetime) -> None:
    store.lane_state(name, "retired", at, retired_by=by)
    store.release_worker(name)


def hold(store: Store, name: str, why: str, at: datetime) -> None:
    store.lane_state(name, "held", at, reason=why)
    store.release_worker(name)


def fold(store: Store, records: Iterable[dict], live: set[str] | None, briefs: Path) -> tuple[list[str], list[str], list[str]]:
    """Apply bus records to the registry; with `live` set, the store is fresh and registers only the spawned lanes in it. Returns the lanes adopted, the lanes this supervisor is launching that someone else spawned from another brief, and one line per PR owner root named that the registry reads dead."""
    adopted, hand, dead = [], [], []
    for record in records:
        lane, at = record["lane"], at_of(record)
        if found := spawned(record):
            name, brief, model, effort, role = found
            for old in store.lanes(*LIVE):
                if succeeds(name, old.name):
                    retire(store, old.name, name, at)
            if (mine := store.lane(name)) and mine.state == "launching" and mine.reason and Path(brief).resolve() != (briefs / f"{name}.brief.md").resolve():
                hand.append(name)
            if live is None or name in live:
                if store.want_lane(name, brief, model, effort, role, "launching" if live is None else "desired", at):
                    adopted.append(name)
            continue
        row = store.lane(lane)
        kind, text = record["kind"], record.get("text") or ""
        store.lane_heard(lane, at, kind)
        if row and row.state in LIVE:
            card = CARD.search(text) if kind in WAITS else None
            store.lane_waiting(lane, record["seq"] if kind in WAITS and (card or ROOT in (record.get("to") or [])) else None, awaits=card[0] if card else None)
            store.lane_page(lane, None)
        if kind == "blocker" and live is None:
            for name in addressed(record) - {lane}:
                if (paged := store.lane(name)) and paged.state in LIVE and (prs := sorted(set(prs_of(record)) & set(paged.prs))):
                    topic, page = record.get("topic") or str(record["seq"]), paged.page or {}
                    fresh = {"topic": topic, "why": f"blocker #{record['seq']} on your PR {' '.join(f'#{n}' for n in prs)} arrived after your last record: {text}", "text": text, "prs": prs, "seqs": []}
                    store.lane_page(name, (page if page.get("topic") == topic else fresh) | {"seqs": [*(page.get("seqs", []) if page.get("topic") == topic else []), record["seq"]]})
        resolved = {record.get("resolves"), *(record.get("also_resolves") or [])} - {None}
        if answers := resolved | {record.get("re")} - {None}:
            for name in store.answered(answers, LIVE):
                store.lane_waiting(name, None, at)
        moved = set(prs_of(record)) if kind == "head" else set()
        for name, page in store.pages().items():
            if resolved & set(page.get("seqs", [])) or moved & set(page["prs"]):
                store.lane_page(name, None)
        if kind == "owner":
            for waiter in store.lanes(*LIVE):
                if waiter.awaits and {f"({waiter.awaits})", f"({waiter.awaits.removeprefix('card-')})"} & {match[0] for match in re.finditer(r"\([\w.-]+\)", text)}:
                    store.lane_waiting(waiter.name, None, at)
                    if live is None:
                        store.lane_page(waiter.name, {"topic": f"owner:{record['seq']}", "why": f"the owner answered {waiter.awaits}, the card you wait on: {text}", "text": text, "prs": list(waiter.prs)})
        if lane == SELF:
            continue
        if kind == "handoff" and row and (doc := (record.get("refs") or {}).get("ccn")):
            store.lane_handoff(lane, doc, at if row.asked else None)
        elif (handed := HANDED_OFF.search(text)) and lane == f"{handed['lane']}-handoff" and (owner := newest(store, handed["lane"])):
            store.lane_handoff(owner.name, handed["doc"], None)
        elif kind in CARRIES and (numbers := {n for n, owner in store.owner_of(prs_of(record)).items() if owner not in addressed(record)}):
            for other in store.lanes("launching"):
                if other.name != lane and other.prs and set(other.prs) <= numbers:
                    retire(store, other.name, lane, at)
            store.own(lane, numbers)
        elif kind == "landed":
            store.disown(prs_of(record))
        elif kind == "done" and row and row.state != "retired":
            retire(store, lane, "done", at)
        elif row and row.state != "retired" and kind in ("stopped", "stand-down"):
            retire(store, lane, "stand-down", at)
        elif kind == "stand-down" and lane == ROOT:
            successor = attrs_of(record).get("successor")
            named = {name for listed in STAND_DOWN_LIST.finditer(text) for name in re.findall(r"[\w.-]+", listed["names"])}
            for old in sorted({*(record.get("to") or []), *named} - {successor}):
                if (gone := store.lane(old)) and gone.state != "retired":
                    retire(store, old, successor or ROOT, at)
                    if successor:
                        store.want_lane(successor, gone.brief, gone.model, gone.effort, gone.role, "launching", at, handoff=gone.handoff, prs=gone.prs, predecessor=old)
        elif kind == "decide" and lane == ROOT and (owner := OWNER.search(text)):
            store.own(owner[1], numbers := prs_of(record))
            heard = store.heard()
            if live is None and numbers and (owner[1] not in heard or at - heard[owner[1]] >= LIVE_WITHIN):
                heir = newest(store, owner[1])
                successor = f"its live successor is {heir.name}" if heir and heir.name != owner[1] and heir.name in heard and at - heard[heir.name] < LIVE_WITHIN else "it has no live successor"
                dead.append(f"{owner[1]} for {' '.join(f'#{n}' for n in numbers)}: root named it owner at {pacific(at)}, but its last record is {f'from {pacific(heard[owner[1]])}' if owner[1] in heard else 'none'}; {successor}")
        elif lane == ROOT and (moved := REPLACES.search(text)) and (old := store.lane(moved["old"])) and old.state in LIVE and (taker := store.lane(moved["new"])):
            retire(store, old.name, taker.name, at)
            store.own(taker.name, old.prs)
    return adopted, hand, dead


def peers(rows: Iterable[dict]) -> dict[str, Peer]:
    out: dict[str, Peer] = {}
    for row in rows:
        if not (lane := row.get("lane")):
            continue
        seen = datetime.fromisoformat(row["seen_at"].replace("Z", "+00:00")).astimezone(UTC)
        live = bool(row.get("live"))
        have = out.get(lane)
        if have is None or (live, seen) > (have.live, have.seen):
            out[lane] = Peer(lane, live, seen, row)
    return out


def successor_brief(old: Lane, new: str, why: str, deps: Deps, transcript: Path | None) -> str:
    drive, checkout = deps.drive, deps.checkout
    if old.handoff:
        start = f"Your handoff is `ccn -R {checkout} doc show {old.handoff}`; read it first."
    else:
        start = (
            f"{old.name} left no handoff. Before anything else, spawn one `long-running:lane` subagent, model sonnet, effort xhigh, "
            f"named `{old.name}-handoff`, from the spawn brief in {HANDOFF_BRIEF}, with transcript {transcript or 'unknown: find it with cc-transcript list'}, "
            f"reference brief {old.brief}, cci drive {drive}, previous handoff none and drive checkout {checkout}; read the doc it returns."
        )
    owned = ", ".join(f"#{n}" for n in old.prs) or "none recorded"
    body = Path(old.brief).read_text()
    step = found[1] if (found := SLICE_STEP.search(body)) else "all"
    return "\n".join([
        f"Replaces: {old.name}",
        "ccx: place=local",
        f"Register slice: lane={new} step={step}",
        f"You are {new} and take over {old.name}'s work. The lane supervisor launched you because {old.name} {why}; {old.name} is stood down on cci, so never SendMessage it.",
        start,
        f"PRs you now own: {owned}.",
        f"If the handoff and {old.name}'s records show its deliverable is already complete, post `cci post --drive {drive} --lane {new} --kind done --to root --text '<what it delivered>'` and stop.",
        "",
        "Before any write (an edit, commit, push, ship, PR comment, or a record that claims work):",
        f"  1. From {checkout}, run `drive.py lane-status {old.name}`. Each line is a worktree {old.name} held, with its branch, dirty files, push state, and PR.",
        f"  2. From {checkout}, never from inside one of those worktrees, run for each worktree path it printed:",
        "     lsof -n -d cwd 2>/dev/null | awk -v w='<worktree>' '$NF == w || index($NF, w \"/\") == 1'",
        f"  3. If any line printed, write nothing: post `cci post --drive {drive} --lane {new} --kind blocker --to root --text \"LIVE {old.name}: <worktree> pids <pids>\"` and stop. Never stop or signal those processes.",
        f"  4. With no live process in any worktree, continue in {old.name}'s worktree and branch from the state lane-status shows.",
        "",
        f"When the deliverable is done, post `cci post --drive {drive} --lane {new} --kind done --to root` before your final message; told to stand down after a task, post that `done` when the task is finished. On `ROTATE`, write your state to cc-notes and post `cci post --drive {drive} --lane {new} --kind handoff --ccn <doc id> --re <the ROTATE record>`, then keep working.",
        "",
        body,
    ])


@dataclass(frozen=True)
class Verdict:
    kind: str
    why: str = ""


def alive(peer: Peer | None, heard: datetime | None, now: datetime) -> bool:
    """A lane is alive while its process is live or it was heard within `STALE_AFTER`; only both failing reads as dead."""
    return bool(peer and peer.live) or bool(heard and now - heard < STALE_AFTER)


def classify_lane(lane: Lane, peer: Peer | None, now: datetime, tokens: int | None = None, rotate_at: int = 0) -> Verdict:
    """One registry row against what `cci who` shows now: `ok`, `came-up`, `no-show`, `dead` or `ask-rotate`; a flushed lane keeps working in place."""
    heard = max([t for t in (peer.seen if peer else None, lane.heard) if t], default=None)
    alive_now = alive(peer, heard, now)
    if lane.state == "launching":
        if peer is not None and alive_now:
            return Verdict("came-up")
        return Verdict("no-show", f"not live {STALE_AFTER.seconds // 60} minutes after its launch") if now - lane.since >= STALE_AFTER else Verdict("ok")
    if not alive_now:
        if peer is not None and (lane.waiting is not None or (lane.answered and now - lane.answered < STALE_AFTER)):
            return Verdict("ok")
        if heard is None:
            return Verdict("dead", "never registered")
        return Verdict("dead", f"stopped at {pacific(heard)}")
    if tokens is not None and rotate_at and tokens >= rotate_at:
        return Verdict("ask-rotate", f"{tokens:,} tokens")
    return Verdict("ok")


def post(deps: Deps, kind: str, to: str, text: str, *flags: str) -> None:
    deps.cci(["post", "--drive", deps.drive, "--lane", SELF, "--kind", kind, "--to", to, *flags, "--text", text])


def escalate(store: Store, deps: Deps, subject: str, cause: str, now: datetime) -> None:
    if store.escalate(subject, cause, ROOT, now):
        post(deps, "blocker", ROOT, f"{pacific(now)} {subject}: {cause}", "--lane-ref", subject.removeprefix("lane:"))


def relaunch(store: Store, deps: Deps, old: Lane, peers: dict[str, Peer], why: str, taken: set[str], now: datetime, out: Outcome) -> None:
    if heir := next((p.lane for p in peers.values() if alive(p, p.seen, now) and succeeds(p.lane, old.name)), None):
        retire(store, old.name, heir, now)
        return
    peer = peers.get(old.name)
    strikes = old.strikes + 1 if old.predecessor and now - old.since < STRIKE_WINDOW else 0
    if strikes >= STRIKES or not Path(old.brief).is_file():
        hold(store, old.name, why, now)
        cause = f"its successors died within {STRIKE_WINDOW.seconds // 60} minutes of launch {STRIKES} times" if strikes >= STRIKES else f"its brief {old.brief} is gone"
        escalate(store, deps, f"lane:{old.name}", f"held, not relaunched: {cause}; it {why}", now)
        out.held.append(old.name)
        return
    new = successor_name(old.name, taken)
    taken.add(new)
    brief = deps.briefs / f"{new}.brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(successor_brief(old, new, why, deps, deps.transcript(peer.row) if peer else None))
    retire(store, old.name, new, now)
    store.want_lane(new, old.brief, old.model, old.effort, old.role, "launching", now, handoff=old.handoff, prs=old.prs, predecessor=old.name, reason=why, strikes=strikes)
    deps.launch(new, old.model, old.effort, brief)
    owned = " ".join(f"#{n}" for n in old.prs)
    post(deps, "report", ROOT, f"{pacific(now)} RELAUNCHED {old.name} as {new}: it {why}; handoff {old.handoff or 'none, the successor writes it'}; PRs {owned or 'none'}; brief {brief}", "--lane-ref", new)
    out.relaunched.append((old.name, new, why))


def wake(store: Store, deps: Deps, lane: Lane, peer: Peer, now: datetime, out: Outcome) -> None:
    if lane.last in SETTLED:
        return
    page = lane.page
    if page and page["prs"] and not set(page["prs"]) & set(lane.prs):
        store.lane_page(lane.name, None)
        return
    if page is None or "woke" not in page:
        if deps.wake(lane.name, peer.row, page):
            out.woken.append(lane.name)
            if page:
                store.lane_page(lane.name, page | {"woke": stamp(now)})
        return
    if "asked" in page or now - parse(page["woke"]) < PAGE_ANSWER:
        return
    owned = " ".join(f"#{n}" for n in page["prs"]) or f"{lane.name}'s work"
    post(deps, "ask", ROOT, f"{pacific(now)} reassign {owned}: {lane.name} has posted nothing since it was woken at {pacific(parse(page['woke']))} because {page['why'][:240]}. Post `<lane> replaces {lane.name}` or a decide naming owner=<lane>; it is not woken again for that blocker.", "--lane-ref", lane.name, *[arg for n in page["prs"] for arg in ("--pr", str(n))])
    store.lane_page(lane.name, page | {"asked": stamp(now)})
    out.reassign.append(lane.name)


def liveness(store: Store, now: datetime) -> list[dict]:
    """Every lane that has posted or been registered, as the drive's one liveness answer: live means a record within `LIVE_WITHIN`, whatever the registry state; a `cci who` row is a process fact, never this."""
    registered = {lane.name: lane for lane in store.all_lanes()}
    heard, owned = store.heard(), store.owners()
    return [
        {"lane": name, "state": lane.state if lane else "unregistered", "live": name in heard and now - heard[name] < LIVE_WITHIN, "heard": stamp(heard[name]) if name in heard else None, "prs": owned.get(name, []), "handoff": lane.handoff if lane else None, "retired_by": lane.retired_by if lane else None}
        for name in sorted(registered.keys() | heard.keys() | owned.keys())
        for lane in [registered.get(name)]
    ]


def hourly(store: Store, deps: Deps, now: datetime) -> None:
    hour = int(now.timestamp()) // 3600
    last = store.cursor(HOUR)
    store.advance(HOUR, hour)
    if not last or hour <= last:
        return
    start = datetime.fromtimestamp((hour - 1) * 3600, UTC)
    restarted = [lane for lane in store.all_lanes() if lane.predecessor and lane.reason and start <= lane.since < start + timedelta(hours=1)]
    if not restarted:
        return
    words = "; ".join(f"{lane.predecessor} {lane.reason}, so {lane.name} took over" for lane in restarted)
    post(deps, "report", ROOT, f"Between {pacific(start)} and {pacific(start + timedelta(hours=1))} the lane supervisor restarted {len(restarted)} lane{'s' if len(restarted) > 1 else ''} with no root action: {words}.", "--attr", "audience=owner")


def tick(store: Store, deps: Deps, now: datetime, generation: int) -> Outcome:
    out = Outcome()
    now = parse(stamp(now))
    fresh = store.cursor(CURSOR) == 0
    try:
        found = peers(deps.who(None if fresh else {lane.name for lane in store.lanes(*LIVE)}))
        records = deps.records(store.cursor(CURSOR))
    except Unread:
        return out
    out.adopted, hand, dead = fold(store, records, {lane for lane, peer in found.items() if alive(peer, peer.seen, now)} if fresh else None, deps.briefs)
    for line in dead:
        escalate(store, deps, f"lane:{line.split(' ', 1)[0]}", line, now)
    for name in hand:
        deps.withdraw(name)
        out.withdrawn.append(name)
    if records:
        store.advance(CURSOR, max(record["seq"] for record in records))
    taken = {lane.name for lane in store.all_lanes()} | set(found)
    for lane in store.lanes(*LIVE):
        peer = found.get(lane.name)
        silent = now - (lane.heard or lane.since)
        wants_tokens = lane.state == "desired" and lane.asked is None and peer is not None and peer.live and silent < STALE_AFTER and lane.last not in SETTLED
        verdict = classify_lane(lane, peer, now, deps.tokens(peer.row) if wants_tokens else None, deps.rotate_at)
        if peer and (verdict.kind == "came-up" or (lane.state == "desired" and verdict.kind in ("ok", "ask-rotate"))):
            store.heartbeat(lane.name, generation, max(peer.seen, lane.heard or peer.seen))
        if verdict.kind == "ok" and lane.state == "desired" and lane.waiting is None and peer and peer.row.get("roster") and silent >= STALE_AFTER:
            wake(store, deps, lane, peer, now, out)
        if verdict.kind == "came-up":
            store.lane_state(lane.name, "desired", now)
            if lane.predecessor and lane.reason and (old := store.lane(lane.predecessor)) and old.retired_by == lane.name:
                post(deps, "stand-down", old.name, f"{pacific(now)} {old.name} stood down for {lane.name}, now up; {old.name} {lane.reason}.", "--successor", lane.name, *[arg for n in lane.prs for arg in ("--pr", str(n))])
        elif verdict.kind == "no-show":
            hold(store, lane.name, verdict.why, now)
            escalate(store, deps, f"lane:{lane.name}", f"{verdict.why}; brief {deps.briefs / f'{lane.name}.brief.md'}", now)
            out.held.append(lane.name)
        elif verdict.kind == "dead":
            relaunch(store, deps, lane, found, verdict.why, taken, now, out)
        elif verdict.kind == "ask-rotate":
            post(deps, "ask", lane.name, f"ROTATE: {lane.name} is at {verdict.why}. Write anything not yet in the ledger or cc-notes to a cc-notes doc, post `cci post --lane {lane.name} --kind handoff --ccn <doc id> --re <this record>`, then keep working.")
            store.lane_asked(lane.name, now)
            out.asked.append(lane.name)
    hourly(store, deps, now)
    return out
