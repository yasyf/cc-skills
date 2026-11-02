"""The lanes mover: every worker the drive needs stays alive without the root.

The `lanes` table holds one row per worker, and only this mover writes it. Each tick folds the
bus records after the mover's cursor into it, reads once which lanes `cci who` lists live, and
acts on each desired lane. Typed records change a row:

- A launch, a `SPAWNED` state record or a cci `spawn` record, adds the worker with the brief,
  model, effort, role and placement its attrs carry. One naming `successor-of` takes that
  worker's PRs and retires it.
- A request, a `decide` to `supervisor` whose attrs name the `verb` (`stand-down`, `park`,
  `unpark`, `owner` or `done`) and the `lane`, with `successor`, `last-read` and `--pr` as the
  verb needs, is how anyone else changes a row; `drive_supervisor.py request` writes it.
  A stand-down or owner request that takes a live lane's place or PRs counts only when its
  `last-read` is no older than that lane's newest record; otherwise the lane is held with its
  PRs and root and the lane hear one defect (root #304721, #307233).
- The lane's own `done`, `stopped` or `stand-down` retires it, its `head` and `opened` records
  move PRs to it, and a `landed` record drops them.

Records written before those verbs existed still change rows the way they did, so a replay of
the last day keeps every lane they retired: a root `stand-down` naming lanes, a root
`<new> replaces <old>` or `owner=` decide, a root `PARKED <lane>:` state, the desk runner's
`worker_done succeeded` outcome, and a numbered successor's launch or live session.
TODO(t2-class-1): delete these readers once no record in the replay window uses them.

The supervisor alone posts the `stand-down --successor` record cci routes mail by, and `route`
answers every sender from the row. A lane whose process row reads not live, with no record
within `STALE_AFTER`, has its successor launched from the lane's brief and newest handoff; a
lane with no process row is held for root, since nothing proves it died; a lane waiting on an
ask to root keeps its clock until the answer. A lane past its context line is asked to rotate
and flushes in place. A fresh store adopts only the lanes alive at that moment, and a tick
whose sources do not all answer acts on nothing. The supervisor signals no process.
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
RUNNER = "desk-runner"
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
SPAWN = "spawn"
REQUEST = "decide"
VERBS = frozenset({"stand-down", "park", "unpark", "owner", "done"})
LANDED = "landed"
SLICE_STEP = re.compile(r"^Register slice: lane=\S+ step=(\S+)", re.MULTILINE)
WORKER_DONE = re.compile(r"^OUTCOME \S+ (?P<lane>[\w.-]+): worker_done succeeded\b")
STAND_DOWN_LIST = re.compile(r"\bstand[ -]?down\s+(?P<names>[^.(;:\n]+)", re.IGNORECASE)
CARD = re.compile(r"\bcard-[\w.-]*\w")
PARKED = re.compile(r"\bPARKED (?P<lane>[\w.-]+):")
PR_MENTION = re.compile(r"#(\d+)\b")
NEEDS_WORK = "needs-work"
STRIKE_WINDOW = 2 * STALE_AFTER
PAGE_ANSWER = 2 * STALE_AFTER
LIVE_WITHIN = 4 * STALE_AFTER
OWNER = re.compile(r"\bowner=([\w.-]+)")
LAST_READ = re.compile(r"\blast-read=#?(?P<seq>\d+)\b")
UNREAD = " down or hand its PRs on, but it "
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
    pr_states: Callable[[list[int]], dict[int, str]]
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


@dataclass(frozen=True)
class Spawn:
    name: str
    brief: str
    model: str
    effort: str
    role: str
    placement: str | None
    successor_of: str | None


def spawned(record: dict) -> Spawn | None:
    text, attrs = record.get("text") or "", attrs_of(record)
    if record["kind"] == SPAWN and (named := (record.get("refs") or {}).get("lanes")):
        name, brief = named[0], attrs.get("brief") or ""
    elif record["kind"] == "state" and text.startswith("SPAWNED "):
        name, brief = text.split()[1], attrs.get("brief") or text.split()[2]
    else:
        return None
    if not brief.startswith("/") or attrs.get("role") in HELPER_ROLES or name.endswith("-handoff"):
        return None
    return Spawn(name, brief, attrs.get("model") or "opus", attrs.get("effort") or "xhigh", attrs.get("role") or "lane", attrs.get("placement"), attrs.get("successor-of"))


def request(record: dict) -> dict | None:
    """The attrs of a typed registry request, a `decide` to the supervisor naming a known verb and a lane, or None."""
    attrs = attrs_of(record)
    return attrs if record["kind"] == REQUEST and SELF in addressed(record) and attrs.get("verb") in VERBS and attrs.get("lane") else None


def route(store: Store, name: str) -> str:
    """Where mail for `name` goes: the end of its successor chain in the registry, root when that end is retired with no successor, and `name` itself when the registry has no row for it."""
    lane = newest(store, name)
    if lane is None:
        return name
    return ROOT if lane.state == "retired" else lane.name


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
    lane, seen = store.lane(name), {name}
    while lane and lane.state == "retired" and lane.retired_by not in seen and (after := store.lane(lane.retired_by or "")):
        lane = after
        seen.add(lane.name)
    return lane


def retire(store: Store, name: str, by: str, at: datetime) -> None:
    store.lane_state(name, "retired", at, retired_by=by)
    store.release_worker(name)


def hold(store: Store, name: str, why: str, at: datetime) -> None:
    store.lane_state(name, "held", at, reason=why)
    store.release_worker(name)


@dataclass
class Folded:
    adopted: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    dead: list[str] = field(default_factory=list)
    unread: list[tuple[str, str]] = field(default_factory=list)
    stood: list[tuple[str, str | None, tuple[int, ...], int]] = field(default_factory=list)
    untyped: list[int] = field(default_factory=list)


def takes_over(store: Store, name: str, old: Lane, at: datetime) -> None:
    store.own(name, old.prs)
    if old.state in LIVE or not store.lane(old.retired_by or ""):
        retire(store, old.name, name, at)


def granted(store: Store, record: dict, asked: dict, live: set[str] | None, at: datetime, out: Folded) -> None:
    """Apply one typed request: `stand-down` retires the lane for its successor or root and queues the stand-down record cci routes by, `park` and `unpark` set and clear its wait, `owner` moves the request's PRs to it, and `done` retires it. A request that takes a live lane's place or PRs counts only when its `last-read` is no older than that lane's newest record (root #304721)."""
    name, verb, cited = asked["lane"], asked["verb"], asked.get("last-read")
    row = store.lane(name)
    if verb == "park" and row:
        store.lane_waiting(name, record["seq"])
    elif verb == "unpark" and row:
        store.lane_waiting(name, None, at)
    elif verb == "done" and row and row.state != "retired":
        retire(store, name, "done", at)
    elif verb == "owner":
        numbers = prs_of(record)
        running = {other.name for other in store.lanes(*LIVE)}
        taken = {held for held in store.owner_of(numbers).values() if held and held != name and held in running}
        if live is None and (refused := [(held, why) for held in sorted(taken) if (why := unread_by(store, record, held, cited))]):
            out.unread += refused
            for held, why in refused:
                hold(store, held, why, at)
            return
        store.own(name, numbers)
        if live is None and numbers and (probe := store.probes().get(name)) and probe["dead"]:
            out.dead.append(f"{name} for {' '.join(f'#{n}' for n in numbers)}: root named it owner at {pacific(at)}, but it left the supervisor's {pacific(probe['asked'])} ask unanswered; {successor_note(store, name)}")
    elif verb == "stand-down" and row:
        successor = asked.get("successor") or None
        if live is None and row.state in LIVE and (why := unread_by(store, record, name, cited)):
            out.unread.append((name, why))
            hold(store, name, why, at)
            return
        if row.state != "retired":
            retire(store, name, successor or ROOT, at)
        if successor:
            store.want_lane(successor, row.brief, row.model, row.effort, row.role, "launching", at, handoff=row.handoff, predecessor=name, placement=row.placement)
            store.own(successor, row.prs)
        else:
            store.own(ROOT, row.prs)
        if live is None:
            out.stood.append((name, successor, row.prs, record["seq"]))


def fold(store: Store, records: Iterable[dict], live: set[str] | None, briefs: Path) -> Folded:
    """Apply bus records to the registry; with `live` set, the store is fresh and registers only the spawned lanes in it. Returns the lanes adopted, the lanes this supervisor is launching that someone else spawned from another brief, one line per PR owner root named that the registry reads dead, each live lane a root record left in place because it cites no `last-read=<seq>` as new as that lane's newest record, and each stand-down a request granted."""
    out = Folded()
    adopted, hand, dead, unread = out.adopted, out.hand, out.dead, out.unread
    for record in records:
        lane, at = record["lane"], at_of(record)
        if found := spawned(record):
            name, brief, model, effort, role = found.name, found.brief, found.model, found.effort, found.role
            if found.successor_of and (old := store.lane(found.successor_of)) and old.name != name:
                takes_over(store, name, old, at)
                if live is None:
                    out.stood.append((old.name, name, old.prs, record["seq"]))
            for old in [store.lane(other) for other in store.names() if succeeds(name, other)]:
                takes_over(store, name, old, at)
            if (mine := store.lane(name)) and mine.state == "launching" and mine.reason and Path(brief).resolve() != (briefs / f"{name}.brief.md").resolve():
                hand.append(name)
            if live is None or name in live:
                if store.want_lane(name, brief, model, effort, role, "launching" if live is None else "desired", at, placement=found.placement):
                    adopted.append(name)
            continue
        row = store.lane(lane)
        kind, text = record["kind"], record.get("text") or ""
        store.lane_heard(lane, at, kind, record["seq"])
        if lane in (ROOT, "main"):
            store.order(record["seq"])
            for name in addressed(record):
                if store.lane(name):
                    store.lane_stopped_on(name, record["seq"])
        elif row:
            store.lane_stopped_on(lane, max(store.orders({record.get("re"), record.get("resolves"), *(record.get("also_resolves") or [])} - {None}), default=None))
        if row and row.state == "held" and at > row.since:
            store.lane_state(lane, "desired", at)
            row = store.lane(lane)
        if row and row.state in LIVE:
            card = CARD.search(text) if kind in WAITS else None
            store.lane_waiting(lane, record["seq"] if kind in WAITS and (card or ROOT in (record.get("to") or [])) else None, awaits=card[0] if card else None)
            store.lane_page(lane, None)
        if (kind == "blocker" or (kind == "ask" and lane == ROOT)) and live is None:
            named = set(prs_of(record)) | ({int(n) for n in PR_MENTION.findall(text)} if kind == "ask" else set())
            for name in addressed(record) - {lane}:
                if (paged := store.lane(name)) and paged.state in LIVE and (prs := sorted(named & set(paged.prs))):
                    topic, page = record.get("topic") or str(record["seq"]), paged.page or {}
                    fresh = {"topic": topic, "why": f"{'blocker' if kind == 'blocker' else 'root ask'} #{record['seq']} on your PR {' '.join(f'#{n}' for n in prs)} arrived after your last record: {text}", "text": text, "prs": prs, "seqs": []}
                    store.lane_page(name, (page if page.get("topic") == topic else fresh) | {"seqs": [*(page.get("seqs", []) if page.get("topic") == topic else []), record["seq"]]})
        resolved = {record.get("resolves"), *(record.get("also_resolves") or [])} - {None}
        if answers := resolved | {record.get("re")} - {None}:
            for name in store.answered(answers, LIVE):
                store.lane_waiting(name, None, at)
        if lane == ROOT and kind == "state" and (parked := PARKED.search(text)) and (resting := store.lane(parked["lane"])) and resting.state in LIVE:
            store.lane_waiting(resting.name, record["seq"])
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
        if lane in (ROOT, "main") and kind == REQUEST and SELF in addressed(record) and not request(record) and live is None:
            out.untyped.append(record["seq"])
        if asked := request(record):
            granted(store, record, asked, live, at, out)
        elif lane == RUNNER and (done := WORKER_DONE.match(text)) and (ended := store.lane(done["lane"])) and ended.state != "retired":
            retire(store, ended.name, "worker-done", at)
        elif kind == "handoff" and row and (doc := (record.get("refs") or {}).get("ccn")):
            store.lane_handoff(lane, doc, at if row.asked else None)
        elif (handed := HANDED_OFF.search(text)) and lane == f"{handed['lane']}-handoff" and (owner := newest(store, handed["lane"])):
            store.lane_handoff(owner.name, handed["doc"], None)
        elif kind in CARRIES and lane != ROOT and (numbers := {n for n, owner in store.owner_of(prs_of(record)).items() if owner not in addressed(record)}):
            for other in store.lanes("launching"):
                if other.name != lane and other.prs and set(other.prs) <= numbers:
                    retire(store, other.name, lane, at)
            store.own(lane, numbers)
        elif kind == "landed":
            numbers = prs_of(record)
            owners = {owner for owner in store.owner_of(numbers).values() if owner} if numbers else set()
            store.disown(numbers)
            for owner in owners:
                if (owned := store.lane(owner)) and owned.state in LIVE and not owned.prs:
                    store.lane_heard(owner, at, LANDED)
        elif kind == "done" and row and row.state != "retired":
            retire(store, lane, "done", at)
        elif row and row.state != "retired" and kind in ("stopped", "stand-down"):
            retire(store, lane, "stand-down", at)
        elif kind == "stand-down" and lane == ROOT:
            successor = attrs_of(record).get("successor")
            named = {name for listed in STAND_DOWN_LIST.finditer(text) for name in re.findall(r"[\w.-]+", listed["names"]) if store.lane(name)}
            for old in sorted((named or set(record.get("to") or [])) - {successor}):
                if not (gone := store.lane(old)):
                    continue
                if live is None and gone.state in LIVE and (why := unread_by(store, record, old, cited_in(record))):
                    unread.append((old, why))
                    hold(store, old, why, at)
                    continue
                if gone.state != "retired":
                    retire(store, old, successor or ROOT, at)
                    if successor:
                        store.want_lane(successor, gone.brief, gone.model, gone.effort, gone.role, "launching", at, handoff=gone.handoff, prs=gone.prs, predecessor=old)
                        continue
                if not successor:
                    store.own(ROOT, gone.prs)
        elif kind == "decide" and lane == ROOT and (owner := OWNER.search(text)):
            named = set(prs_of(record)) | {int(n) for n in PR_MENTION.findall(text)}
            running = {other.name for other in store.lanes(*LIVE)}
            taken = {held for held in store.owner_of(named).values() if held and held != owner[1] and held in running}
            if live is None and (refused := [(held, why) for held in sorted(taken) if (why := unread_by(store, record, held, cited_in(record)))]):
                unread += refused
                for held, why in refused:
                    hold(store, held, why, at)
                continue
            store.own(owner[1], numbers := prs_of(record))
            if live is None and numbers and (probe := store.probes().get(owner[1])) and probe["dead"]:
                dead.append(f"{owner[1]} for {' '.join(f'#{n}' for n in numbers)}: root named it owner at {pacific(at)}, but it left the supervisor's {pacific(probe['asked'])} ask unanswered; {successor_note(store, owner[1])}")
        elif lane == ROOT and (moved := REPLACES.search(text)) and (old := store.lane(moved["old"])) and old.state in LIVE and (taker := store.lane(moved["new"])):
            if live is None and (why := unread_by(store, record, old.name, cited_in(record))):
                unread.append((old.name, why))
                hold(store, old.name, why, at)
                continue
            retire(store, old.name, taker.name, at)
            store.own(taker.name, old.prs)
    return out


def cited_in(record: dict) -> str | None:
    return found["seq"] if (found := LAST_READ.search(record.get("text") or "")) else None


def unread_by(store: Store, record: dict, name: str, cited: str | None) -> str:
    """Why root's `record` may not stand `name` down or hand its PRs on: it cites no `last-read=<seq>`, or one older than the newest record `name` posted (root #304721)."""
    newest = store.newest_seq(name)
    if cited and (newest is None or int(cited) >= newest):
        return ""
    read = f"cites last-read={cited}, older than {name}'s newest record #{newest}" if cited else "cites no last-read=<seq>"
    return f"root's {record['kind']} #{record['seq']} would stand {name}{UNREAD}{read}"


def successor_note(store: Store, name: str) -> str:
    dead = {lane for lane, probe in store.probes().items() if probe["dead"]}
    heir = newest(store, name)
    return f"its live successor is {heir.name}" if heir and heir.name != name and heir.state != "retired" and heir.name not in dead else "it has no live successor"


def probe_owners(store: Store, deps: Deps, found: dict[str, Peer], now: datetime) -> None:
    """Ask each lane that has posted nothing for `LIVE_WITHIN` and owns a PR needing work (conflicting, red, or not approved) whether it is there, waking it if it idles on a roster, and name it to root only if the ask goes unanswered for `STALE_AFTER`; a green, approved, conflict-free PR waits only on the owner's tap and needs no live owner. A PR root holds because it stood down its lane with no successor is named to root once each time it comes to need work (root #280532)."""
    heard, probes, lines = store.heard(), store.probes(), []
    for name in [name for name, probe in probes.items() if name in heard and heard[name] > probe["asked"]]:
        store.unprobe(name)
        probes.pop(name)
    owners = store.owners()
    unowned = owners.pop(ROOT, [])
    quiet = {name: prs for name, prs in owners.items() if not (since := heard.get(name)) or now - since >= LIVE_WITHIN}
    try:
        states = deps.pr_states(sorted({n for prs in [*quiet.values(), unowned] for n in prs}))
    except Unread:
        return
    stranded = [n for n in unowned if states.get(n) == NEEDS_WORK]
    for n in unowned:
        if n not in stranded:
            store.unprobe(f"{ROOT}:{n}")
    if fresh := [n for n in stranded if f"{ROOT}:{n}" not in probes]:
        post(deps, "blocker", ROOT, f"{pacific(now)} {' '.join(f'#{n}' for n in fresh)} need{'s' if len(fresh) == 1 else ''} work and no lane owns {'it' if len(fresh) == 1 else 'them'}: root stood down the lane that did, naming no successor. Name one with a root decide carrying `owner=<lane>`.")
        for n in fresh:
            store.probe(f"{ROOT}:{n}", now)
    for name, prs in quiet.items():
        held, since, probe = [n for n in prs if states.get(n) == NEEDS_WORK], heard.get(name), probes.get(name)
        if not held:
            continue
        owned = " ".join(f"#{n}" for n in held)
        if probe is None:
            ask = f"{pacific(now)} {name}: you own {owned}, which still need work, and have posted nothing since {pacific(since) if since else 'you were named'}. Post any record within {STALE_AFTER.seconds // 60} minutes, such as your state, or root is told you are gone."
            post(deps, "ask", name, ask)
            store.probe(name, now)
            if (peer := found.get(name)) and peer.row.get("roster"):
                deps.wake(name, peer.row, {"topic": f"probe:{name}", "why": ask, "text": ask, "prs": held})
        elif probe["dead"] is None and now - probe["asked"] >= STALE_AFTER:
            store.probe_dead(name, now)
            lines.append(f"{name} ({owned}; asked at {pacific(probe['asked'])}; {successor_note(store, name)})")
    if lines:
        post(deps, "blocker", ROOT, f"{pacific(now)} {len(lines)} lane{'s' if len(lines) > 1 else ''} own PRs that need work and left the supervisor's direct ask unanswered for {STALE_AFTER.seconds // 60} minutes: {'; '.join(lines)}")


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
    """One registry row against what `cci who` shows now: `ok`, `came-up`, `no-show`, `dead`, `unproven`, `stood-down` or `ask-rotate`; only a process row that reads not live proves a death, a flushed lane keeps working in place, and a lane that stopped with root's record to it or its own answer to root's record as its newest word stopped on root's order, of any kind (root #311668)."""
    heard = max([t for t in (peer.seen if peer else None, lane.heard) if t], default=None)
    alive_now = alive(peer, heard, now)
    if lane.state == "launching":
        if peer is not None and alive_now:
            return Verdict("came-up")
        return Verdict("no-show", f"not live {STALE_AFTER.seconds // 60} minutes after its launch") if now - lane.since >= STALE_AFTER else Verdict("ok")
    if not alive_now:
        if lane.stopped_on is not None:
            return Verdict("stood-down", f"stopped on root's #{lane.stopped_on}")
        if peer is None:
            return Verdict("unproven", f"has no process row in cci who or Orca and was last heard {f'at {pacific(heard)}' if heard else 'never'}, so nothing proves it died")
        if peer.row.get("stood_down"):
            return Verdict("stood-down")
        if lane.waiting is not None or (lane.answered and now - lane.answered < STALE_AFTER):
            return Verdict("ok")
        return Verdict("dead", f"stopped at {pacific(heard)}")
    if tokens is not None and rotate_at and tokens >= rotate_at:
        return Verdict("ask-rotate", f"{tokens:,} tokens")
    return Verdict("ok")


def post(deps: Deps, kind: str, to: str, text: str, *flags: str) -> None:
    deps.cci(["post", "--drive", deps.drive, "--lane", SELF, "--kind", kind, "--to", to, *flags, "--stamp", "--text", text])


def escalate(store: Store, deps: Deps, subject: str, cause: str, now: datetime) -> None:
    if store.escalate(subject, cause, ROOT, now):
        post(deps, "blocker", ROOT, f"{pacific(now)} {subject}: {cause}", "--lane-ref", subject.removeprefix("lane:"))


def relaunch(store: Store, deps: Deps, old: Lane, peers: dict[str, Peer], why: str, taken: set[str], now: datetime, out: Outcome) -> None:
    if heir := next((p.lane for p in peers.values() if alive(p, p.seen, now) and succeeds(p.lane, old.name)), None):
        retire(store, old.name, heir, now)
        return
    peer = peers.get(old.name)
    strikes = old.strikes + 1 if old.predecessor and now - old.since < STRIKE_WINDOW else 0
    if strikes >= STRIKES or not Path(old.brief).is_file() or UNREAD in (old.reason or ""):
        hold(store, old.name, why, now)
        cause = f"its successors died within {STRIKE_WINDOW.seconds // 60} minutes of launch {STRIKES} times" if strikes >= STRIKES else f"its brief {old.brief} is gone" if not Path(old.brief).is_file() else f"{old.reason}, so the supervisor refused it, and the lane has since stopped; post the stand-down again citing last-read=<seq>, or relaunch it by hand"
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
    """Every lane that has posted or been registered, as the drive's one liveness answer: a lane is live unless it left the supervisor's direct ask unanswered for its window; quiet is never dead, and a `cci who` row is a process fact, never this."""
    registered = {lane.name: lane for lane in store.all_lanes()}
    heard, owned, probes = store.heard(), store.owners(), store.probes()
    return [
        {"lane": name, "state": lane.state if lane else "unregistered", "live": not (probes.get(name) or {}).get("dead"), "heard": stamp(heard[name]) if name in heard else None, "prs": owned.get(name, []), "handoff": lane.handoff if lane else None, "retired_by": lane.retired_by if lane else None, "placement": lane.placement if lane else None, "predecessor": lane.predecessor if lane else None, "route": route(store, name)}
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
    folded = fold(store, records, {lane for lane, peer in found.items() if alive(peer, peer.seen, now)} if fresh else None, deps.briefs)
    out.adopted, hand, dead, unread = folded.adopted, folded.hand, folded.dead, folded.unread
    for old, successor, prs, seq in folded.stood:
        post(deps, "stand-down", old, f"{pacific(now)} {old} stood down{f' for {successor}' if successor else ', its PRs to root'} by record #{seq}.", *(("--successor", successor) if successor else ()), "--re", str(seq), *[arg for n in prs for arg in ("--pr", str(n))])
    for line in dead:
        escalate(store, deps, f"lane:{line.split(' ', 1)[0]}", line, now)
    for seq in folded.untyped:
        post(deps, "defect", ROOT, f"{pacific(now)} root's decide #{seq} reaches the supervisor with no registry request, so the registry is unchanged and its lanes are still woken while their tasks read in progress; post `drive_supervisor.py request <{'|'.join(sorted(VERBS))}> <lane>` for each lane it names.", "--re", str(seq))
    for name, why in unread:
        post(deps, "defect", ROOT, f"{pacific(now)} {why}, so the supervisor refused it and holds {name} with its PRs, never relaunched or retired, until {name} posts again; {name}, keep working. Root: read {name} with `cci tail --lane {name} --since <its wake seq> --oldest` and the registry's heard time, then post it again citing last-read=<seq>.", "--to", name, "--lane-ref", name)
    probe_owners(store, deps, found, now)
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
        elif verdict.kind == "unproven":
            hold(store, lane.name, verdict.why, now)
            escalate(store, deps, f"lane:{lane.name}", f"held, not relaunched: it {verdict.why}; brief {lane.brief}", now)
            out.held.append(lane.name)
        elif verdict.kind == "stood-down":
            retire(store, lane.name, "stand-down", now)
        elif verdict.kind == "dead" and lane.last == LANDED:
            retire(store, lane.name, LANDED, now)
        elif verdict.kind == "dead":
            relaunch(store, deps, lane, found, verdict.why, taken, now, out)
        elif verdict.kind == "ask-rotate":
            post(deps, "ask", lane.name, f"ROTATE: {lane.name} is at {verdict.why}. Write anything not yet in the ledger or cc-notes to a cc-notes doc, post `cci post --lane {lane.name} --kind handoff --ccn <doc id> --re <this record>`, then keep working.")
            store.lane_asked(lane.name, now)
            out.asked.append(lane.name)
    hourly(store, deps, now)
    return out
