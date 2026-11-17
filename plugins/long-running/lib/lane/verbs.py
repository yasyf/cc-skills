"""The verbs that change a lane: open, his message, his ruling, park, end_turn and the owner's own writes. Each is one locked write, and no verb writes a fourth state."""

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lane import facts, store


def woken(lane: dict, why: str) -> None:
    """Leave a parked or waiting lane waiting with its wait fired, so the next pass starts its owner."""
    moment = store.stamp()
    lane["state"], lane["parked"] = store.WAITING, None
    lane["wait"] = {"on": [{"kind": "his_reply"}], "until": moment, "why": why, "set": moment, "fired": moment, "fired_why": why}


TAP_LABELS = ("Approve", "Hold")


def live_drives_holding(repo: str, number: int) -> list[str]:
    """Each live lane drive with a lane whose rows carry the PR."""
    found = store.root()
    if not found.is_dir():
        return []
    return [folder.name for folder in sorted(found.iterdir()) if (folder / store.LIVE).exists() and any((row["repo"], row["number"]) == (repo, number) for lane in store.load_all(folder.name).values() for row in lane["prs"])]


def tapped(drive: str, repo: str, number: int, label: str, head: str, source: str, ts: str | None = None, named: bool = False) -> list[str]:
    """His Approve or Hold on a lane PR at a head, written on each row carrying it: an Approve sets its approved_head, so lane-land lands it on its next pass, and lifts his Hold only as his later word on that PR: a tap that names it, not a stack card covering it, whose ts is after the Hold, or within PRESENT_SECONDS for a Hold whose time is unknown, so a replayed or carried Approve never clears a Hold, as #17808's did; and a Hold names when and where he gave it; a waiting lane carrying it wakes. The lanes it was written on."""
    if label not in TAP_LABELS:
        raise store.Refused(f"his tap is Approve or Hold, not {label!r}")
    carrying = [lane_id for lane_id, lane in store.load_all(drive).items() if any((row["repo"], row["number"]) == (repo, number) for row in lane["prs"])]
    if not carrying:
        raise store.Refused(f"no lane of {drive} carries {repo}#{number}")
    moment = store.stamp()

    def tapping(lane: dict) -> None:
        for row in lane["prs"]:
            if (row["repo"], row["number"]) == (repo, number):
                if label == "Approve":
                    row["approved_head"], row["tapped_head"] = head, head
                    row.pop("carried", None)
                    if row["held"] and named and later(ts, row.get("held_at")):
                        row["held"], row["held_at"] = None, None
                else:
                    row["held"], row["held_at"] = f"his Hold at {head[:12]}, {source}, {moment}", ts or f"{store.now().timestamp():.6f}"
        store.note(lane, f"his {label} on {repo}#{number} at {head[:12]}, {source}")
        if lane["state"] == store.WAITING:
            woken(lane, f"his {label} on {repo}#{number}")

    for lane_id in carrying:
        store.update(drive, lane_id, tapping)
    store.kick(drive)
    return carrying


def carry(drive: str, repo: str, number: int, head: str, compatible: bool, why: str) -> list[str]:
    """A model's judgement of whether his Approve carries to a PR's new head, by his words "the approve carries if its semantically similar, as determined by the model (if its going to make a semantically incompatible change it needs to revoke the approval)": compatible, the Approve stands at head and lane-land may land it; incompatible, the Approve is revoked, the owner is told what changed, and his tap is needed again. The lanes it was written on."""
    carrying = [lane_id for lane_id, lane in store.load_all(drive).items() if any((row["repo"], row["number"]) == (repo, number) for row in lane["prs"])]
    if not carrying:
        raise store.Refused(f"no lane of {drive} carries {repo}#{number}")
    from lane import work

    row = next(row for row in store.load(drive, carrying[0])["prs"] if (row["repo"], row["number"]) == (repo, number))
    item = work.load(drive, work.item_id("carry", f"{repo}#{number}"))
    tapped = row.get("tapped_head") or row.get("approved_head") or ""
    if not item or item["state"] not in (work.QUEUED, work.TAKEN) or item.get("head") != head or tapped[:12] not in item["words"]:
        raise store.Refused(f"no open carry item judges {repo}#{number} at {head[:12]} against his tap at {tapped[:12]}; a verdict counts only from the item that names the head he tapped")

    def judging(lane: dict) -> None:
        for row in lane["prs"]:
            if (row["repo"], row["number"]) == (repo, number):
                if not row.get("approved_head"):
                    raise store.Refused(f"{repo}#{number} carries no Approve of his to judge")
                row["carried"] = {"head": head, "from": row.get("tapped_head") or row["approved_head"], "compatible": compatible, "why": why, "at": store.stamp()}
                if not compatible:
                    row["revoked"] = {"head": row.get("tapped_head") or row["approved_head"], "at": store.stamp(), "why": why}
                    row["approved_head"], row["tapped_head"] = None, None
        store.note(lane, f"his Approve on {repo}#{number} {'carries' if compatible else 'is revoked'} at {head[:12]}: {why}")
        if not compatible:
            if lane["state"] == store.WAITING:
                woken(lane, f"his Approve on {repo}#{number} was revoked at {head[:12]}: {why}")
            elif lane["state"] == store.RUNNING:
                with store.note_path(drive, lane["id"]).open("a") as note:
                    note.write(f"His Approve on {repo}#{number} was revoked at its new head {head[:12]}, since the change is not what he approved: {why}. Tell him what changed and ask his tap again.\n\n")

    for lane_id in carrying:
        store.update(drive, lane_id, judging)
    store.kick(drive)
    return carrying


def premerge_exempt(drive: str, repo: str, number: int, head: str, why: str, by: str) -> list[str]:
    """Mark one PR of a premerge lane as not the work its pre-merge test is for, at its head, by his 5:02 PM words "nothing other than the ssql work needs to run before merging", so it lands on his Approve with its staging test after the merge."""
    carrying = [lane_id for lane_id, lane in store.load_all(drive).items() if any((row["repo"], row["number"]) == (repo, number) for row in lane["prs"])]
    if not carrying:
        raise store.Refused(f"no lane of {drive} carries {repo}#{number}")

    def marking(lane: dict) -> None:
        for row in lane["prs"]:
            if (row["repo"], row["number"]) == (repo, number):
                row["premerge_exempt"] = {"head": head, "why": why, "by": by, "at": store.stamp()}
        store.note(lane, f"{repo}#{number} at {head[:12]} is not the work its pre-merge test is for, so no staging test before merge: {why} ({by})")

    for lane_id in carrying:
        store.update(drive, lane_id, marking, leaving_unreplied=True)
    store.kick(drive)
    return carrying

def premerge_revoke(drive: str, repo: str, number: int, why: str, by: str) -> list[str]:
    """Take one PR's premerge exemption away, so its premerge_question verdict decides again whether it is tested before merge. The lanes whose row lost a mark."""

    def revoking(lane: dict) -> None:
        for row in lane["prs"]:
            if (row["repo"], row["number"]) == (repo, number) and row.pop("premerge_exempt", None):
                store.note(lane, f"{repo}#{number} lost its premerge exemption, so its verdict decides its test before merge again: {why} ({by})")

    marked = [lane_id for lane_id, lane in store.load_all(drive).items() if any((row["repo"], row["number"]) == (repo, number) and row.get("premerge_exempt") for row in lane["prs"])]
    for lane_id in marked:
        store.update(drive, lane_id, revoking, leaving_unreplied=True)
    store.kick(drive)
    return marked



def open_lane(drive: str, lane_id: str, title: str, words: str | None = None, channel: str | None = None, thread: str | None = None, link: str | None = None, ts: str | None = None, parks: str | None = None, park_words: str | None = None, prs: list[tuple[str, int]] = (), worktree: str | None = None, next_step: str | None = None, incident: dict | None = None) -> dict:
    """Open a lane, woken at once so the same call starts its owner; at the cap it must name the lane it parks, with his words."""
    cap = store.config(drive)["active"]
    if not worktree:
        raise store.Refused("a lane needs its worktree, a checkout of a repository Claude trusts, where its owner starts: --worktree")

    def opening(lanes: dict[str, dict]) -> dict:
        if lane_id in lanes:
            raise store.Refused(f"lane {lane_id} is already open")
        if parks:
            if parks not in lanes or lanes[parks]["state"] == store.PARKED:
                raise store.Refused(f"{parks} is not an active lane to park")
            if not park_words:
                raise store.Refused("parking a lane takes his words: --park-words")
            park_in(lanes[parks], park_words, None)
        elif len(store.active(lanes)) >= cap and not incident:
            raise store.Refused(f"{len(store.active(lanes))} lanes are active, the cap is {cap}: name the lane this one parks with --parks, and his words for it")
        lane = store.new_lane(lane_id, title, words, channel, thread, link, ts)
        lane["prs"] = [pr_row(repo, number, None) for repo, number in prs]
        lane["worktree"] = worktree
        lane["next"] = next_step
        lane["incident"] = incident
        store.note(lane, "opened")
        lanes[lane_id] = lane
        return lane

    lane = store.transact(drive, opening)
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return store.load(drive, lane["id"])


def pr_row(repo: str, number: int, words: str | None) -> dict:
    return {"repo": repo, "number": number, "words": words, "approved_head": None, "held": None, "dangerous": None, "staging": None}


def park_in(lane: dict, words: str, link: str | None) -> None:
    """Park a lane on his words. A waiting lane parks now; a running one parks at its owner's end_turn, which reads the parked request."""
    if lane["state"] == store.RUNNING:
        lane["park"] = {"words": words, "link": link}
    else:
        lane["state"], lane["wait"], lane["parked"] = store.PARKED, None, {"words": words, "link": link}
    store.note(lane, f"parked: {words}")


def park(drive: str, lane_id: str, words: str, link: str | None = None) -> None:
    store.update(drive, lane_id, lambda lane: park_in(lane, words, link))
    store.kick(drive)


def unpark(drive: str, lane_id: str) -> None:
    cap = store.config(drive)["active"]

    def unparking(lanes: dict[str, dict]) -> None:
        lane = lanes[lane_id]
        if lane["state"] != store.PARKED:
            raise store.Refused(f"lane {lane_id} is not parked")
        if len(store.active(lanes)) >= cap:
            raise store.Refused(f"{cap} lanes are active: park one before unparking {lane_id}")
        woken(lane, "unparked")
        store.note(lane, "unparked")

    store.transact(drive, unparking)
    store.kick(drive)


def take_messages(drive: str, lanes: dict[str, dict], lane_id: str, asks: list[dict], cap: int) -> int:
    """Append his messages and taps to a lane by ts, once each, the one inbound path for his words. An owner started before its hook read his unanswered words, which fires only on the handover note, gets each one appended to that note whole, so it reads it at its next tool call. A parked lane unparks on his word, since his ask would otherwise wait unanswered; past the cap the check names it. The count appended."""
    lane = lanes[lane_id]
    known = {ask.get("ts") for ask in lane.get("asks", []) if ask.get("ts")}
    fresh = [ask for ask in asks if not ask.get("ts") or ask["ts"] not in known]
    if not fresh:
        return 0
    lane["asks"] = [*lane.get("asks", []), *fresh]
    if lane["state"] == store.RUNNING and not (lane.get("owner") or {}).get("asks_hook"):
        with store.note_path(drive, lane_id).open("a") as note:
            note.write("".join(f"His words to this lane at {ask['at']}{', ' + ask['link'] if ask.get('link') else ''}, not answered yet: answer them with `lane say` now.\n{ask['words']}\n\n" for ask in fresh))
    if lane["state"] == store.PARKED:
        woken(lane, "his word in a parked lane")
        store.note(lane, f"unparked by his word{'' if len(store.active(lanes)) <= cap else f'; {len(store.active(lanes))} lanes are active, over the cap of {cap}'}")
    return len(fresh)


def ask_row(words: str, link: str | None, ts: str | None) -> dict:
    return {"words": words, "link": link, "at": store.stamp(), "ts": ts, "replied": None, "done": None}


def message(drive: str, lane_id: str, words: str, link: str | None = None, ts: str | None = None) -> int:
    """His message in a lane's channel: appended to its file, and that write is the owner's wake."""
    cap = store.config(drive)["active"]
    taken = store.transact(drive, lambda lanes: take_messages(drive, lanes, lane_id, [ask_row(words, link, ts)], cap))
    if taken:
        from lane import supervisor

        supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return taken


def plain(text: str) -> str:
    return " ".join(text.casefold().split())


def ruling(drive: str, lane_id: str | None, words: str | None, link: str | None, binds: str, relayed_by: str | None = None, reading: str | None = None) -> None:
    """Keep one answer of his as his words and nothing else: with a link, the text of his message there, read from Slack, or the part of it --words quotes, any words not in it refused; with no link, his words as someone relayed them, marked relayed by them. An agent's reading of it is kept apart, as a reading. On the lane it binds it wakes its owner; with no lane it goes in the drive's shared rulings and wakes every waiting lane, so every wait set before it is judged again under it."""
    if link:
        said = facts.said_at(link)
        if words and plain(words) not in plain(said):
            raise store.Refused(f"--words are not his words at {link}, which read: {said!r}; quote them as he typed them, and put what they mean in --reading")
        words = words or said
    elif not relayed_by:
        raise store.Refused("a ruling with no link to his message names who relayed his words: --relayed-by")
    elif not words:
        raise store.Refused("a ruling with no link gives his words: --words")
    row = {"words": words, "link": link, "at": store.stamp(), "binds": binds, **({"relayed_by": relayed_by} if not link else {}), **({"reading": reading} if reading else {})}
    if lane_id is None:
        with store.locked(drive):
            path = store.drive_dir(drive) / store.RULINGS
            kept = store.shared_rulings(drive)
            fresh = path.with_name(".rulings.json.tmp")
            fresh.write_text(json.dumps({"shared": [*kept, row]}, indent=1, ensure_ascii=False) + "\n")
            fresh.replace(path)

        rejudge(drive, row)
        return

    def recording(lane: dict) -> None:
        lane["rulings"] = [*lane.get("rulings", []), row]
        if lane["state"] != store.RUNNING:
            woken(lane, f"his ruling: {binds}")
        store.note(lane, f"ruling: {binds}")

    store.update(drive, lane_id, recording)
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)


def rejudge(drive: str, row: dict) -> None:
    """Have a model read his shared ruling against each waiting lane's wait and each queued staging test before merge, waking only the lanes whose wait it could end and closing each staging test it no longer requires, detached so the ruling verb returns at once; every other lane keeps waiting and reads the ruling in its next brief."""
    import subprocess

    from lane import session

    from lane import work

    waiting = {lane_id: {"title": lane["title"], "waits_on": lane["wait"]["on"], "why": lane["wait"].get("why"), "next": lane.get("next")} for lane_id, lane in store.load_all(drive).items() if lane["state"] == store.WAITING}
    staging = [{"id": item["id"], "lane": item["lane"], "subject": item["subject"], "touches": item.get("touches")} for item in work.items(drive) if item["kind"] == "staging-before" and item["state"] == work.QUEUED]
    if not waiting and not staging:
        return
    lane = store.launcher(drive)
    prompt = (
        f"His ruling, just stored for every lane of drive {drive}: {json.dumps(row, ensure_ascii=False)}\n\n"
        f"Each lane waiting now, with what it waits on and why: {json.dumps(waiting, ensure_ascii=False)}\n\n"
        "For each lane whose wait this ruling could end or change, because the ruling decides what the lane is waiting to learn, removes the reason it waits, or forbids what it waits for, run once: "
        f"`{lane} --drive {drive} wake <lane id> --why \"his ruling: <one line on how it changes this lane's wait>\"`. "
        f"Each staging test still queued to run before its PRs merge: {json.dumps(staging, ensure_ascii=False)}. For each one this ruling no longer requires before merge, run once: `{lane} --drive {drive} work done <id> --result \"not needed before merge: his ruling <one line>\"`, which wakes its lane. "
        "Wake no other lane and close no other item: a lane the ruling does not touch keeps waiting and reads the ruling in its next brief. Run nothing else, and end with one line naming the lanes you woke and the items you closed."
    )
    folder = store.drive_dir(drive) / "rejudged"
    folder.mkdir(exist_ok=True)
    with (folder / f"{store.stamp()}.log").open("w") as out:
        subprocess.Popen(session.argv(store.config(drive), "auditor", store.drive_dir(drive), {}, ["-p"], ["--permission-mode", "bypassPermissions", "--", prompt]), cwd=store.drive_dir(drive), stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)


def parse_event(text: str) -> dict:
    """One wait event as typed: <kind>:<ref> for pr_merged, check, staging_result, release, queue_grant and work, or his_reply."""
    kind, _, ref = text.partition(":")
    if kind == "his_reply" and not ref:
        return {"kind": kind}
    if kind in store.REF_KINDS and ref:
        if kind in ("pr_merged", "check"):
            facts.pr_ref(ref)
        return {"kind": kind, "ref": ref}
    raise store.Refused(f"a wait event is pr_merged:<owner/repo>#<n>, check:<owner/repo>#<n>, staging_result:<cut id>, release:<release id>, queue_grant:release, work:<item id> or his_reply, not {text!r}")


def end_turn(drive: str, lane_id: str, token: str, on: list[dict], until: str, why: str, next_step: str | None = None) -> dict:
    """The only way out of running: refuses a wait on no outside fact, with no time bound, past its bound or the drive's longest, on a fact that already holds, or while his ask is unanswered; else writes the wait, clears the owner and frees the slot in one write. A lane he parked while it ran parks here."""
    shape = store.config(drive)
    moment = store.now()
    bound = store.when(until, moment)
    if not on:
        raise store.Refused("a wait names at least one outside fact; with nothing else to wait for, wait on his_reply")
    if bound <= moment:
        raise store.Refused(f"the bound {store.stamp(bound)} has passed; a wait needs a time ahead")
    if bound > moment + timedelta(hours=shape["max_wait_hours"]):
        raise store.Refused(f"the bound {store.stamp(bound)} is past the drive's longest wait of {shape['max_wait_hours']}h")
    lane = store.load(drive, lane_id)
    store.fenced(lane, token)
    from lane import owner as owners

    if flying := owners.in_flight(lane["owner"]):
        raise store.Refused(f"{flying} background agents or tasks of yours still run, and ending your turn on a wait would stop them: stay running, end your turn without end-turn, and their return wakes you; the Stop hook lets you idle while they run")
    from lane import queue

    if all(event["kind"] == "his_reply" for event in on) and (idle := facts.unproduced(drive, lane, on[0], {})):
        raise store.Refused(f"{idle}: {unserved(lane) or 'its done checks are unmet'}. Work toward it, ask him with `lane say \"<question>\" --options ...`, or wait on the fact your step needs")
    if why_unserved := unserved(lane):
        raise store.Refused(f"{why_unserved}, and your step names none: write `lane next \"<plan>\" --says \"<sentence>\" --serves <n>`, or `--serves-none \"<why>\"`, before you wait")
    if missing := [resource for resource in queue.asking(drive, lane_id) if {"kind": "queue_grant", "ref": resource} not in on]:
        raise store.Refused(f"this lane stands in line for {', '.join(missing)}: wait on {' and '.join(f'queue_grant:{resource}' for resource in missing)}, which wakes it the moment its grant is free, so its wait never reads as his reply")
    from lane import stager

    if unstarted := [event["ref"] for event in on if event["kind"] == "staging_result" and not stager.starts(drive, event["ref"])]:
        raise store.Refused(f"no stager has taken or queued cut {', '.join(unstarted)}, so a wait on it names no start and never fires: ask with `lane-land want --lane {lane_id}` and wait on the cut id it prints, or wait on what this step does wait on")
    from lane import work

    if missing := [event["ref"] for event in on if event["kind"] == "work" and not work.load(drive, event["ref"])]:
        raise store.Refused(f"no work item {', '.join(missing)}: add it with `lane work add <kind> <subject> \"<words>\"` and wait on the id it prints")
    prs = facts.read_prs({facts.pr_ref(event["ref"]) for event in on if event["kind"] in ("pr_merged", "check")})
    if held := [event for event in on if event["kind"] != "his_reply" and facts.holds(drive, lane, event, prs)]:
        raise store.Refused(f"already true, act on it instead of waiting: {held}")

    def ending(lane: dict) -> dict:
        store.fenced(lane, token)
        if lane["state"] != store.RUNNING:
            raise store.Refused(f"lane {lane_id} is not running")
        promised = (lane.get("promise") or {}).get("by")
        kept = min(bound, store.parse(promised)) if promised and store.parse(promised) > moment else bound
        owner = lane["owner"]
        lane["owner"] = None
        lane["last_owner"] = {"session": owner.get("session"), "job": owner.get("job"), "ended": store.stamp()}
        if next_step:
            lane["next"] = next_step
        if parked := lane.pop("park", None):
            lane["state"], lane["parked"] = store.PARKED, parked
            store.note(lane, f"parked at turn end: {parked['words']}")
        else:
            lane["state"] = store.WAITING
            lane["wait"] = {"on": on, "until": store.stamp(kept), "why": why, "set": store.stamp(moment)}
            store.note(lane, f"waits: {why}")
        return owner

    owner = store.update(drive, lane_id, ending)
    from lane import owner as owners

    owners.stop(owner)
    store.kick(drive)
    return store.load(drive, lane_id)


def owner_write(drive: str, lane_id: str, token: str | None, change) -> None:
    def writing(lane: dict) -> None:
        store.fenced(lane, token)
        change(lane)

    store.update(drive, lane_id, writing)


def unmet(lane: dict) -> list[int]:
    """The numbers, from 1, of the lane's done checks not yet met."""
    return [number for number, check in enumerate(lane.get("done", []), 1) if not check.get("met_at")]


def unserved(lane: dict) -> str | None:
    """Why the lane's work moves toward none of its unmet done checks, naming the nearest, or None: its step names no unmet check and gives no reason it serves none, as when its checks were written after its step or the check its step served is met."""
    if not (open_checks := unmet(lane)):
        return None
    serves = lane.get("serves") or {}
    if serves.get("none") or serves.get("check") in open_checks:
        return None
    return f"the nearest unmet done check is {open_checks[0]}: {lane['done'][open_checks[0] - 1]['words']}"


def home(drive: str, lane_id: str, channel: str, thread: str | None) -> None:
    """Move a lane's home, where it posts and reads his words, to its project channel, at the root or in thread."""
    if channel.startswith(("D", "U")):
        raise store.Refused("a lane's home is a project channel, never his DM")

    def moving(lane: dict) -> None:
        store.note(lane, f"home moved from {lane.get('channel')}/{lane.get('thread')} to {channel}/{thread}")
        lane["channel"], lane["thread"] = channel, thread

    store.update(drive, lane_id, moving, leaving_unreplied=True)


def blocked(drive: str, lane_id: str, what: str, by: str) -> None:
    """A refusal or blocker, written on the lane that waits on it and waking it, so no blocker lives only in a record another session must read."""

    def blocking(lane: dict) -> None:
        store.note(lane, f"blocked, from {by}: {what}")
        lane["blocks"] = [*lane.get("blocks", []), {"what": what, "by": by, "at": store.stamp()}]
        if lane["state"] == store.WAITING:
            woken(lane, f"a blocker from {by}: {what}")

    store.update(drive, lane_id, blocking)
    store.kick(drive)


def set_next(drive: str, lane_id: str, token: str | None, text: str, says: str, serves: int | None = None, serves_none: str | None = None) -> None:
    """Write the lane's plan, the one plain sentence his page shows of it, and which unmet done check the step serves, or why it serves none; refused while a check is unmet and the step names neither, so no lane moves without moving toward what he asked for."""

    def setting(lane: dict) -> None:
        if (serves is None) == (serves_none is None) and (open_checks := unmet(lane)):
            raise store.Refused(f"name the done check this step serves, --serves <n> of {', '.join(map(str, open_checks))}, or say why it serves none with --serves-none \"<why>\"")
        if serves is not None and serves not in unmet(lane):
            raise store.Refused(f"done check {serves} is no unmet check of lane {lane_id}: unmet are {', '.join(map(str, unmet(lane))) or 'none'}")
        lane["next"], lane["says"] = text, says
        lane["serves"] = {"check": serves} if serves is not None else {"none": serves_none} if serves_none else None
        store.note(lane, f"next: {text}" + (f" (serves done check {serves})" if serves is not None else f" (serves no done check: {serves_none})" if serves_none else ""))

    owner_write(drive, lane_id, token, setting)


def add_check(drive: str, lane_id: str, token: str | None, words: str) -> int:
    """Add one done check, in his words, a thing a person could run to see the lane's work done; its number from 1."""
    found = {}

    def adding(lane: dict) -> None:
        lane.setdefault("done", []).append({"words": words, "at": store.stamp()})
        found["number"] = len(lane["done"])
        store.note(lane, f"done check {found['number']}: {words}")

    owner_write(drive, lane_id, token, adding)
    return found["number"]


def reword_check(drive: str, lane_id: str, token: str | None, number: int, words: str | None) -> None:
    """Give done check number new words, his words or the nearest to them, keeping whether it is met; with no words, drop it, the checks after it moving up one."""

    def rewording(lane: dict) -> None:
        checks = lane.get("done", [])
        if not 1 <= number <= len(checks):
            raise store.Refused(f"lane {lane_id} has done checks 1 to {len(checks)}, not {number}")
        if words is None:
            dropped = checks.pop(number - 1)
            store.note(lane, f"done check {number} dropped: {dropped['words']}")
            if (lane.get("serves") or {}).get("check") == number:
                lane["serves"] = None
            elif (served := (lane.get("serves") or {}).get("check")) and served > number:
                lane["serves"] = {"check": served - 1}
            return
        store.note(lane, f"done check {number} reworded from {checks[number - 1]['words']!r} to {words!r}")
        checks[number - 1]["words"] = words

    owner_write(drive, lane_id, token, rewording)


def mark_check(drive: str, lane_id: str, token: str | None, number: int, met: bool, how: str) -> None:
    """Mark done check number met, with how it was seen met, or unmet again, with why."""

    def marking(lane: dict) -> None:
        checks = lane.get("done", [])
        if not 1 <= number <= len(checks):
            raise store.Refused(f"lane {lane_id} has done checks 1 to {len(checks)}, not {number}")
        check = checks[number - 1]
        if met:
            check.update(met_at=store.stamp(), how=how)
        else:
            check.pop("met_at", None)
            check["how"] = how
        store.note(lane, f"done check {number} {'met' if met else 'unmet'}: {how}")

    owner_write(drive, lane_id, token, marking)


def log(drive: str, lane_id: str, token: str | None, text: str) -> None:
    owner_write(drive, lane_id, token, lambda lane: store.note(lane, text))


def add_pr(drive: str, lane_id: str, token: str | None, repo: str, number: int, words: str | None, why: str | None = None) -> None:
    def adding(lane: dict) -> None:
        for row in lane["prs"]:
            if row["repo"] == repo and row["number"] == number:
                row["words"] = words or row.get("words")
                row["why"] = why or row.get("why")
                return
        lane["prs"].append(pr_row(repo, number, words) | ({"why": why} if why else {}))

    owner_write(drive, lane_id, token, adding)


def done(drive: str, lane_id: str, token: str | None, ts: str) -> None:
    """Mark one of his asks done, by its ts."""

    def marking(lane: dict) -> None:
        for ask in lane["asks"]:
            if ask.get("ts") == ts:
                ask["done"] = store.stamp()
                return
        raise store.Refused(f"no ask of his with ts {ts} on lane {lane_id}")

    owner_write(drive, lane_id, token, marking)


def set_incident(drive: str, lane_id: str, token: str | None, link: str | None = None, monitor: str | None = None, fix_live: str | None = None, hotfix_only: str | None = None) -> None:
    """Record a lane's incident: its link, the Datadog monitor whose alert says it still hurts users, and the proof its fix is live, which ends its first rank; whether it hurts users is read from those facts each pass, never set here."""

    def setting(lane: dict) -> None:
        kept = lane.get("incident") or {}
        lane["incident"] = {"link": link or kept.get("link"), "monitor": monitor or kept.get("monitor"), "fix_live": fix_live or kept.get("fix_live"), "hotfix_only": hotfix_only or kept.get("hotfix_only"), "freeze": kept.get("freeze")}
        store.note(lane, f"incident: {lane['incident']}")

    owner_write(drive, lane_id, token, setting)
    store.kick(drive)


def pin(drive: str, lane_id: str, pinned: bool) -> None:
    store.update(drive, lane_id, lambda lane: lane.__setitem__("pinned", pinned))
    store.kick(drive)


def set_worktree(drive: str, lane_id: str, path: str) -> None:
    def setting(lane: dict) -> None:
        lane["worktree"] = path
        store.note(lane, f"worktree: {path}")

    store.update(drive, lane_id, setting)
    store.kick(drive)


OLD_PR_FACTS = {"merged": "pr_merged", "closed": "pr_merged", "checks_done": "check"}


def adopted(lane: dict) -> tuple[dict, list[str]]:
    """A lane written in the first lane-file shape, as the rebuild's files and the first shadow lanes are, in the agreed one: flat channel and thread, waits on the closed list, asks with replied, no PR state on rows, the log out of the file; and the log lines it held."""
    lane = json.loads(json.dumps(lane))
    if isinstance(lane.get("channel"), dict):
        lane["channel"], lane["thread"] = lane["channel"].get("id"), lane["channel"].get("thread")
    lane.setdefault("thread", None)
    for ask in lane.get("asks", []):
        ask.pop("seen", None)
        ask.setdefault("replied", None)
        ask.setdefault("done", None)
    lane.setdefault("rulings", [])
    for row in lane.get("prs", []):
        for field in ("head", "checks", "merged", "under"):
            row.pop(field, None)
        row.setdefault("words", None)
        row.setdefault("staging", None)
    logs = lane.pop("log", [])
    if isinstance(lane.get("parked"), str):
        lane["state"], lane["parked"], lane["wait"], lane["owner"] = store.PARKED, {"words": lane["parked"], "link": None}, None, None
    if lane.get("wait"):
        on = []
        for event in lane["wait"].get("on", []):
            match event["kind"]:
                case "pr":
                    on.append({"kind": OLD_PR_FACTS[event["fact"]], "ref": f"{event['repo']}#{event['number']}"} if event["fact"] in OLD_PR_FACTS else {"kind": "his_reply"})
                case "cut":
                    on.append({"kind": "staging_result", "ref": event["id"]})
                case "reply":
                    on.append({"kind": "his_reply"})
                case _:
                    on.append(event)
        lane["wait"]["on"] = on or [{"kind": "his_reply"}]
    if lane["state"] == store.RUNNING:
        moment = store.stamp()
        lane["state"], lane["owner"] = store.WAITING, None
        lane["wait"] = {"on": [{"kind": "his_reply"}], "until": moment, "why": "adopted", "set": moment, "fired": moment, "fired_why": "adopted into the lane drive"}
    return lane, [f"{row['at']} {row['text']}" for row in logs if isinstance(row, dict)]


def adopt(drive: str, paths: list[str], rulings_only: bool = False) -> list[str]:
    """Write lane files of the first shape into the drive in the agreed shape, each checked by the writer, with the drive's shared rulings beside them, refused whole when a ruling lacks words, at or binds; with rulings_only, replace only each lane's rulings and the shared ones, keeping every other field of a lane already adopted. A line per lane, naming each with no worktree, whose owner cannot start until `lane worktree` sets one."""
    from pathlib import Path

    said = []
    for given in paths:
        source = Path(given)
        if source.name == store.RULINGS:
            kept = json.loads(source.read_text())
            if why := store.unshaped(kept["shared"] if isinstance(kept, dict) else kept, store.RULING_FIELDS, "a shared ruling"):
                raise store.Refused(f"{source} holds {why}")
            with store.locked(drive):
                (store.drive_dir(drive) / store.RULINGS).write_text(source.read_text())
            said.append(f"shared rulings: {len(store.shared_rulings(drive))}")
            continue
        lane, lines = adopted(json.loads(source.read_text()))
        if not rulings_only and store.lane_path(drive, lane["id"]).is_file():
            raise store.Refused(f"lane {lane['id']} is already on drive {drive}; adopt adds new lanes only, so it never drops a running owner, a worktree or his newer asks: use --rulings-only, or the lane verbs")
        if rulings_only:
            store.update(drive, lane["id"], lambda kept, rulings=lane["rulings"]: kept.__setitem__("rulings", rulings))
            said.append(f"{lane['id']}: rulings replaced, {len(lane['rulings'])}")
            continue

        def placing(lanes: dict[str, dict], lane: dict = lane, lines: list[str] = lines) -> None:
            lanes[lane["id"]] = lane
            for line in lines:
                store.note(lane, line)

        store.transact(drive, placing)
        said.append(f"{lane['id']}: {lane['state']}, {len(lane['asks'])} asks ({len(store.unreplied(lane))} unanswered), {len(lane['rulings'])} rulings" + ("" if lane.get("worktree") else ", NO WORKTREE: set one with `lane worktree`"))
    store.kick(drive)
    return said


CLOCK = re.compile(r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s?([AaPp])\.?[Mm]\b")
PACIFIC = ZoneInfo("America/Los_Angeles")


def clocked(found: re.Match[str]) -> datetime:
    """A typed clock time, as today's in Pacific."""
    hour = int(found[1]) % 12 + (12 if found[3].lower() == "p" else 0)
    return store.now().astimezone(PACIFIC).replace(hour=hour, minute=int(found[2]), second=0, microsecond=0)


def handover(drive: str, lane_id: str, text: str | None, writer: str | None = None) -> None:
    """Add one entry, stamped and signed by its writer, to the note every owner of the lane reads at its start, such as the old jobs still running on its work, which is no ask of his and wants no reply; an owner running now is shown the entry at its next tool call. With no text, clear every entry, which is the owner's call once it has adopted them."""
    if text is not None and not writer:
        raise store.Refused("a handover entry names its writer: --from")

    def setting(lane: dict) -> None:
        if text is None:
            lane["handover"] = None
            store.note(lane, "handover cleared")
            return
        entry = f"[written {store.now().astimezone(PACIFIC).strftime('%-I:%M %p')} PT ({store.stamp()}) by {writer}]\n{text.strip()}"
        lane["handover"] = f"{lane['handover']}\n\n{entry}" if lane.get("handover") else entry
        store.note(lane, f"handover entry from {writer}")
        if lane["state"] == store.RUNNING:
            path = store.note_path(drive, lane_id)
            fresh = path.with_name(f".{lane_id}.note.tmp")
            fresh.write_text(f"{path.read_text()}\n\n{entry}" if path.is_file() else entry)
            fresh.replace(path)

    store.update(drive, lane_id, setting)


def wake(drive: str, lane_id: str, why: str, if_waiting: bool = False) -> None:
    """Fire a waiting lane's wait now, so the next pass starts its owner: for a wait the drive's operators find was set on a fact that cannot fire. if_waiting leaves a lane that is not waiting as it is, for a caller that already handed its owner the news, which a running owner reads at its next tool call."""

    def waking(lane: dict) -> None:
        if lane["state"] != store.WAITING and if_waiting:
            return
        if lane["state"] != store.WAITING:
            raise store.Refused(f"lane {lane_id} is {lane['state']}, not waiting")
        woken(lane, why)
        store.note(lane, f"woken: {why}")

    store.update(drive, lane_id, waking)
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)


PRESENT_SECONDS = 900


def later(ts: str | None, held_at: str | None) -> bool:
    """Whether a tap at Slack ts comes after the Hold it would lift: after held_at when the Hold kept its time, else within PRESENT_SECONDS of now, his live word; never for a tap with no ts."""
    if not ts:
        return False
    return float(ts) > float(held_at) if held_at else store.now().timestamp() - float(ts) < PRESENT_SECONDS
