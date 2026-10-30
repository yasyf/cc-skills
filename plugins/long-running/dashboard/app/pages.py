from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

import milestones
import plainwords
from brief import COORDINATORS, PACIFIC, RULES, SCHEMA, Page, at, unchecked, change_records, clip, clock, listing, mentioned, open_changes, owner_asks, pr_stages, rulings, said, strict, string, trouble, unasked

WORK_WINDOW = dt.timedelta(hours=12)
MIN_DAY = dt.timedelta(hours=8)
HISTORY_KINDS = {"owner", "release", "incident", "mechanism", "fix-live", "hold", "lift", "blocker"}
WORK_KINDS = {"state", "report", "done", "opened", "landed", "blocker", "handoff", "stopped", "stand-down"}
GO = re.compile(r"^go #(\d+)")
STATUS = {"enum": [*SCHEMA["properties"]["work"]["items"]["properties"]["status"]["enum"], "Landing", "Waiting for release"]}
OWNER_STATE = {"you": "waits on Yasyf's approval", "queued": "Yasyf approved it; it waits to land", "held": "Yasyf held it", "failing": "its tests fail", "doing": "being worked on", "waiting": "waits on another change", "landed": "landed"}
PAGE_PROMPT = """You write {what} The user message is the drive's facts as JSON. Return one JSON object matching the schema.

""" + RULES + """

Fields:
{fields}"""

ROW_SLOTS = {"change": "latest", "plan": "latest", "risk": "latest", "review": "headline", "blocker": "latest"}
GROUP_SLOTS = {"title": "title", "relation": "latest"}
CARD_SLOTS = {"question": "latest", "means": "headline", "answers": "title"}

PLAN_SCHEMA = strict({
    "summary": string(),
    "milestones": listing(strict({
        "state": {"enum": ["live", "progress", "next", "paused", "broken", "not"]},
        "label": string(),
        "ships": string(),
        "waits": string(),
        "since": string("Pacific clock time the wait started, or empty"),
        "card": string("id of the open_questions entry it waits on, or empty"),
        "note": string(),
        "unblock": string(),
    })),
    "parts": listing(strict({
        "id": string("the part's id from the facts"),
        "name": string(),
        "state": {"enum": ["done", "doing", "you", "stuck", "waiting", "later"]},
        "label": string(),
        "blocks": string(),
        "since": string("Pacific clock time the blocker started, or empty"),
        "card": string("id of the open_questions entry it waits on, or empty"),
        "doing": string(),
        "delivers": string(),
        "went_wrong": string(),
        "changes": listing(strict({"number": {"type": "integer"}, "name": string(), "state": string()}), maxItems=8),
        "landed": listing(string(), maxItems=4),
    })),
})

PLAN_PROMPT = PAGE_PROMPT.format(what="the Plan page of a live dashboard that a busy engineering lead reads. For each milestone and each part of the plan it says where it stands, what blocks it, and what is being done about it.", fields="""- summary: one sentence of at most 25 words: how far the plan is and the one thing most in the way.
- milestones: exactly one entry per milestone, in order. milestone_state is computed from the records and wins: copy its state, label and since, and make waits and unblock say its waits and next in plain words without adding facts. Otherwise state and label as on the main page; copy them from main_page.stops when the facts agree, except never "Half live": a partly live milestone's label names what is live, such as "Old app live". ships: at most 16 words, what users get when it is live. waits: at most 30 words, exactly what it waits on now and why, or empty when it is live or nothing is open. note is what went wrong so far: at most 40 words, a short retro of what went wrong on the way to this milestone and its cause, such as unplanned logic bundled into fixes, a revert, branches drifting apart, or conflicts from changes that landed midway; never the current blocker restated; for a milestone not started, what went wrong so far in the parts it builds on, or that nothing has gone wrong because no work on it has started. unblock: at most 30 words, what is being done to unblock it now: who does it, named by their job, the exact action, and when it is due from the records; when no record gives a time, say who owes one, such as "the cloud runs worker has not given a time". For a milestone not started, the planned path to start it: which milestone or part must land first, and who starts it then. Empty only when it is live.
- parts: one entry per plan part, in the facts' order. id is the part's id. name: at most 6 words, what the part makes for users. state: done, doing (being built or tested), you (waits on the owner), stuck, waiting (on tests, the merge process or another part), or later. label: two to four words such as Done, Being built, Waits on you, Stuck in testing, Not started. name it by what a person can do once it is done, never by its mechanism (protocol, credentials, token, follower, conversion). state and label come from the earliest thing still unresolved in it: a change whose tests fail makes it stuck, not waiting on merging. blocks: at most 30 words, exactly what blocks it now and why, or empty; a decision it waits on is named, such as "your pick on the browser live-update question", never "your word". doing: at most 30 words, what is being done to unblock it: who acts, named by their job such as "the cloud runs worker", "codex" or "the merge process", never just "a worker", the exact action, and when it is due from the records, or who owes a time when no record gives one; empty for a part that is done or not started; never "not known": when no one is on it, say who assigns it. delivers: at most 16 words. went_wrong: at most 40 words, a short retro of what went wrong in this part and its cause, as for a milestone's note; empty for a done part whose problems are settled.
- changes: each open change of the part, at most 8, the ones nearest to landing first: number from the facts, name at most 8 words for what it does for users, state at most 4 words such as Waits on you, Tests failing, Being built, Approved.
- landed: up to four landed changes of the part, newest first, each at most 8 words for what it lets a person do, never its title.
- A milestone's waits names the decision or change it waits on, never "your word"; its note and the part rows agree on one current failure.
- A milestone or part waits on the owner only when an entry of open_questions asks it; then card is that entry's id and waits or blocks names the question. With no such entry, never say it waits on him or his pick; card is empty.
- since, for a milestone that waits and a part that is blocked: the Pacific clock time of the record that started the wait (the push, the hold, the failing check, the question going up), or empty when nothing blocks it. The page shows how long from since, so waits and blocks never repeat the time.
- Exactly means the thing a reader could act on: which code fails and why, such as "the cloud runs layer and nine packages above it import the deleted blob store until the change that splits it lands", or "70 command tests fail in two clusters"; never a generic line such as "new code still fails to build" or "tests fail".""")

WORK_ITEM = {"item": string("stable id, lowercase words joined by dashes"), "workers": listing(string()), "changes": listing({"type": "integer"}), "title": string()}
WORK_SCHEMA = strict({
    "running": listing(strict({**WORK_ITEM, "status": STATUS, "latest": string(), "started": string("Pacific clock time, such as 1:05 PM"), "blocked_since": string("Pacific clock time it started waiting, or empty"), "asks": listing(string("the ask of a waiting_on_owner entry"))})),
    "finished": listing(strict({**WORK_ITEM, "outcome": {"enum": ["delivered", "stopped"]}, "result": string(), "at": string("Pacific clock time")})),
    "helpers": listing(strict({"workers": listing(string()), "name": string(), "latest": string()})),
    "pull_requests": listing(strict({"number": {"type": "integer"}, "stage": string(), "next": string(), "since": string("Pacific clock time")})),
})

WORK_PROMPT = PAGE_PROMPT.format(what="the Work page of a live dashboard that a busy engineering lead reads. It lists every piece of work in the drive today: what runs now, what finished and its result, and the helpers that keep the drive running.", fields="""- A piece of work is what is being made, never a worker. Several workers on one thing, and a worker's successor (a name ending in -2 or -3), are one piece.
- running: every piece of work with a worker that reported in the last two hours, or whose result still waits on a release, an approval, tests or another change, most important first: fixing a blocker, then the next milestone, then the rest. item: reuse the id and title of the same work from main_page_work or previous. title: at most 8 words, what it makes for users. status: one of the listed words; "Ready for you" only when one of its open_pull_requests waits on Yasyf's approval, "Landing" when he approved them and they wait to land. latest: at most 16 words, the newest real progress, said as what works or what is still broken for users. started: the Pacific clock time its first worker started today. blocked_since: when its status is Stuck, Ready for you, Waiting on tests or Under review, the Pacific clock time of the record that started that wait (the push, the hold, the failing check, the review request); else empty. For work waiting on one of its open_pull_requests, blocked_since is that request's waiting_since, so Work and Review agree. The page shows how long from blocked_since, so latest never repeats when the wait began. workers: the worker names (facts only, never shown), the one whose own change it is first; a worker that also does other work comes after it. changes: the pull request numbers this work makes (facts only, never shown).
- Say a piece of work waits on the owner (status Ready for you) only when an entry of waiting_on_owner asks it, naming that question or change; otherwise name who it waits on. asks: for Ready for you, the ask of each waiting_on_owner entry it waits on; else empty.
- finished: only work that delivered its result, or stopped for good with nothing left to do, newest first. Work stopped to wait for a decision is running with status Stuck or Ready for you. Work that still waits on a release, an approval, tests, a merge or another worker is running, never finished; so is work whose merge is not known. outcome: delivered or stopped. title as above. result: at most 16 words, what it delivered for users, or for stopped work why it stopped and who took it over. at: the Pacific clock time of its last report.
- Leave out any piece of work whose purpose the facts do not say. Rolling out means a deploy is running now; a change that ships with the next release is Waiting for release.
- Name whoever acts by their job, such as "the cloud runs worker", "codex" or "the merge process", never just "a worker".
- helpers: the workers that keep the drive running rather than build something (the merge process, board upkeep, review graders, monitors, the dashboard's own workers, release checks). One entry per job, at most eight, jobs whose latest report is a failure or waits on someone first. name: at most 6 words for the job, such as "Merge process" or "Board upkeep". latest: at most 16 words, its state now with the time, such as "Landed the cloud runs fix at 4:40 PM"; never an old failure that a later report settled.
- pull_requests: one entry per entry of ledger_pull_requests, same number. When its evidence is present, that is the progress desk's record of this change's state: stage, next and since come from evidence.state, evidence.next and evidence.since, in plain words, and nothing older overrides them. stage: at most 25 words, what holds the change now, naming every prerequisite still open (each approval, hold, restack or change that must land first) by what it does; next: at most 12 words, who acts next and what they do. Take both from the newest of its newest_records and coordinator_rulings when they say something its ledger_stage or ledger_next does not, else put the ledger's words in plain terms. A hold names what must happen for it to lift, in the ruling's own terms, such as which release or which change landing; never "the hold", "a dependency" or "a later release" alone. A wait on another change landing first stays in stage until a record says that change landed. A worker a ruling stood down is never the one who acts next. Never say a change waits on Yasyf's approval unless its number is in waiting_on_owner; when a worker says it awaits approval and it is not there, its approval row is not up yet, so say who puts it up. Name the change below by what it does, never by number. since: the Pacific clock time of the record that started this stage, or ledger_since when no newer record changed it.""")

HISTORY_SCHEMA = strict({
    "since": string("copy day_starts"),
    "summary": listing(string(), maxItems=3),
    "entries": listing(strict({
        "at": string("Pacific clock time"),
        "kind": {"enum": ["decision", "release", "incident", "fixed", "landed", "approved", "blocked", "unblocked"]},
        "title": string(),
        "detail": string(),
        "changes": listing({"type": "integer"}),
    }), maxItems=40),
})

HISTORY_PROMPT = PAGE_PROMPT.format(what="the History page of a live dashboard that a busy engineering lead reads to catch up on the day. It is the day as a short curated timeline: his decisions, releases, incidents, and what landed, grouped by feature.", fields="""- since: copy day_starts.
- summary: up to two sentences of at most 30 words each: what people can do now that they could not this morning, and what is still in the way. Name behaviors, never the mechanisms behind them. Never say how many changes or questions wait on Yasyf: the Review tab's live count says that, and a written count goes stale.
- entries: newest first, at most 40. Never a heartbeat, a status post, a command receipt, a worker handover, or a coordinator starting a worker. Each release in releases that went live since day_starts is a release entry.
- An incident or stuck entry says whether it is settled now and how, from later events, or who is on it; never "not known" when a later event answers it. One that affected Yasyf's own decisions or approvals names which ones and whether they were kept. An entry still waits on one of his board questions only while that question is in open_questions; once it has left them, the entry says he answered it.
- kind: decision (the owner's own choice or approval of a design or plan), release (something went live to users), incident (something broke for users), fixed (an incident's fix went live), landed (changes merged), approved (he approved changes to merge), blocked (something got stuck), unblocked (it moved again).
- His decisions are entries of their own, said in his terms, such as "You chose five tabs for the dashboard".
- Group landings and approvals by feature: one entry per feature per hour at most, naming each change in words in the detail. changes holds the pull request numbers the entry covers (facts only, never shown).
- title: at most 10 words, the outcome itself, readable alone. detail: at most 25 words, what it changes for users or what is left.""")

REVIEW_SCHEMA = strict({
    "rows": listing(strict({"key": string(), **{field: string() for field in ROW_SLOTS}})),
    "groups": listing(strict({"key": string(), **{field: string() for field in GROUP_SLOTS}})),
    "cards": listing(strict({"key": string(), "question": string(), "means": string(), "answers": listing(string())})),
})

REVIEW_PROMPT = PAGE_PROMPT.format(what="the Review page of a live dashboard: the pull requests, groups of pull requests and questions waiting on the owner, each said in a few short plain lines.", fields="""- Every entry copies its key. An entry with a previous_rewrite keeps it word for word.
- rows: one entry per row in the facts. Rewrite only the fields listed in its problems; copy every other field word for word. change: what is different for a user once it lands. plan: "Matches the plan." or the one difference. risk: who could be hurt and how, or "Low: " and why. blocker: "Nothing; lands on your approval." or what it waits for, named by what that does. review: name each reviewer (codex, the reviewer bot, Yasyf) and say each finding in a few words as what could have gone wrong for users and what changed, never a count and never the code mechanism; leave out a request that was declined unless Yasyf must decide on it; read review_records to find what each finding was and leave out one the records never name. change, plan, risk and blocker at most 15 words; review at most 25.
- groups: one entry per group. title: at most 8 words, the feature its changes deliver for users. relation: at most 16 words, how its changes relate, such as which lands first, naming each change by what it does; never a number of changes.
- cards: one entry per question. question: at most 16 words, the one decision still open, as a plain question. When the card says Yasyf already approved its plan, ask only the open step, such as which release to start now. means: at most 25 words: what the recommended answer changes for users and what the other answer would change, naming the changes it touches. answers: one label per entry of the question's answers, in the same order, each at most 8 words for what that answer does, with no tool, test or file names.""")


def worker_names(model):
    return sorted({r["lane"] for r in [*model["records"], *model["lanes"]]})


def pr_title(model, number):
    return model["prs"].get(str(number), {}).get("title")


def previous_of(previous, schema):
    return previous and {k: previous[k] for k in schema["required"] if k in previous}


def plan_slot(path):
    key = path[-1]
    if path == ("summary",):
        return "headline"
    if path[:1] == ("milestones",):
        return {"label": "title", "ships": "latest", "waits": "story", "note": "sentence", "unblock": "story"}.get(key)
    if "changes" in path:
        return {"name": "title", "state": "title"}.get(key)
    if "landed" in path:
        return "title"
    if path[:1] == ("parts",):
        return {"name": "title", "label": "title", "blocks": "story", "doing": "story", "delivers": "latest", "went_wrong": "sentence"}.get(key)
    return None


def open_questions(model):
    return [{"id": card["id"], "question": card["title"], "recommended": card.get("recommended")} for card in model["board"]["cards"] if card["decides"]]


def plan_digest(model, config, previous, now):
    entries = {e["number"]: e for e in model["drive"]["prs"]}
    releases = {r.get("id"): r for r in model["now"].get("releases", [])}
    brief = model.get("brief") or {}

    def change(number):
        entry = entries.get(number, {})
        return {"number": number, "title": pr_title(model, number), "state": entry.get("state"), "next": entry.get("next")}

    return {
        "now": clock(now),
        "milestones": [{"name": m["name"], **{k: v for k, v in releases.get(m.get("release"), {}).items() if k != "parts"}} for m in config["milestones"]],
        "milestone_state": model["milestone_state"],
        "main_page": {"stops": brief.get("progress", {}).get("stops"), "blockers": [node for path in brief.get("paths", []) for node in path["nodes"]]},
        "parts": [
            {
                "id": s["id"],
                **{k: s.get(k) for k in ("label", "title", "state", "tone", "blocker", "next", "delivers", "went_wrong")},
                "merged": sum(p["state"] == "landed" for p in s["prs"]),
                "total": len(s["prs"]),
                "open_changes": [change(p["number"]) for p in s["prs"] if p["state"] != "landed"][:12],
                "landed_changes": [pr_title(model, p["number"]) for p in s["prs"] if p["state"] == "landed"][-6:],
            }
            for s in model["steps"]
        ],
        "open_questions": open_questions(model),
        "pull_request_stages": pr_stages(model),
        "recent_trouble": trouble(model["records"], now),
        "coordinator_rulings": rulings(model["records"], now),
        "previous": previous_of(previous, PLAN_SCHEMA),
        "worker_names": worker_names(model),
    }


def plan_settle(brief, facts):
    for milestone, computed in zip(brief["milestones"], facts["milestone_state"]):
        milestone.update(state=computed["state"], label=computed["label"], waits=computed["waits"], unblock=computed["next"], since=clock(milestones.when(computed["since"])) if computed["since"] else "")


def work_slot(path):
    if path[:1] in (("running",), ("finished",)):
        return {"title": "title", "latest": "latest", "result": "latest"}.get(path[-1])
    if path[:1] == ("helpers",):
        return {"name": "title", "latest": "latest"}.get(path[-1])
    if path[:1] == ("pull_requests",):
        return {"stage": "story", "next": "latest"}.get(path[-1])
    return None


def work_checks(brief, facts):
    return [f"running.{i}.status: no entry of waiting_on_owner it names in asks is open, so it may not wait on him" for i, row in enumerate(brief["running"]) if unasked(row, facts["waiting_on_owner"])]


def work_settle(brief, facts):
    for row in brief["running"]:
        if unasked(row, facts["waiting_on_owner"]):
            row["status"] = "Stuck"


def ledger_rows(model, records):
    rulings, mentions = mentioned(records)
    return [
        {
            "number": int(row["pr"]),
            "title": row["title"],
            "worker": row["owner"],
            "ledger_stage": row["state"],
            "ledger_next": row["next"],
            "ledger_since": clock(at(row["since"])),
            "coordinator_rulings": [said(r, 300) for r in rulings.get(int(row["pr"]), [])[-5:]],
            "newest_records": [said(r, 220) for r in mentions.get(int(row["pr"]), [])[-4:]],
            "evidence": model["evidence"].get(row["pr"]),
        }
        for row in model["stack"]
    ]


def waiting_since(records, number):
    pushed = [r for r in records if r["kind"] in ("head", "opened") and number in ((r.get("refs") or {}).get("prs") or [])]
    return clock(at(pushed[-1]["at"])) if pushed else ""


def work_digest(model, config, previous, now):
    lanes = {}
    for record in model["fetch"](WORK_KINDS, now - WORK_WINDOW):
        lanes.setdefault(record["lane"], []).append(record)
    entries = {str(e["number"]): e for e in model["drive"]["prs"]}
    holds, landed = {}, {}
    for number, pr in model["prs"].items():
        if pr.get("state") == "landed" and pr.get("lane") and pr.get("landed_at") and at(pr["landed_at"]) >= now - WORK_WINDOW:
            landed.setdefault(pr["lane"], []).append({"number": int(number), "title": pr.get("title"), "landed_at": clock(at(pr["landed_at"]))})
        if pr.get("state") == "open" and pr.get("lane"):
            holds.setdefault(pr["lane"], []).append({"number": int(number), "title": pr.get("title"), "state": OWNER_STATE.get(entries.get(number, {}).get("state"), "not known"), "waiting_since": waiting_since(model["records"], int(number))})
    workers = [
        {
            "worker": lane,
            "looks_like_helper": bool(COORDINATORS.search(lane)),
            "first_record": clock(at(mine[0]["at"])),
            "last_record": clock(at(mine[-1]["at"])),
            "open_pull_requests": holds.get(lane, [])[:6],
            "landed_pull_requests": landed.get(lane, [])[-6:],
            "records": [said(r, 220) for r in mine[-(1 if COORDINATORS.search(lane) else 3):]],
        }
        for lane, mine in sorted(lanes.items(), key=lambda item: item[1][-1]["at"], reverse=True)
    ][:90]
    brief = model.get("brief") or {}
    changes = change_records(model, now)
    return {
        "now": clock(now),
        "window": f"since {clock(now - WORK_WINDOW)}",
        "main_page_work": [{k: w[k] for k in ("item", "title", "workers", "changes")} for w in brief.get("work", [])],
        "waiting_on_owner": owner_asks(model["needs"]),
        "coordinator_rulings": rulings(model["records"], now),
        "open_changes": open_changes(model, changes),
        "workers": workers,
        "ledger_pull_requests": ledger_rows(model, changes),
        "previous": previous_of(previous, WORK_SCHEMA),
        "worker_names": worker_names(model),
    }


def history_slot(path):
    if path[:1] == ("summary",):
        return "sentence"
    if path[:1] == ("entries",):
        return {"title": "node", "detail": "headline"}.get(path[-1])
    return None


def day_start(now):
    midnight = now.astimezone(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    return min(midnight, now - MIN_DAY)


def history_digest(model, config, previous, now):
    start = day_start(now)
    records = model["fetch"]({*HISTORY_KINDS, "go"}, start)
    entries = {e["number"]: e for e in model["drive"]["prs"]}
    steps = {s["id"]: s["label"] for s in model["steps"]}
    return {
        "now": clock(now),
        "day_starts": clock(start),
        "owner_words": [f"{clock(at(w['at']))}: {clip(w['title'] + ' ' + w['text'], 300)}" for w in model["words"] if at(w["at"]) >= start],
        "events": [said(r, 160 if r["kind"] == "blocker" else 240) for r in records if r["kind"] in HISTORY_KINDS and r["lane"] != "desk-runner"][-300:],
        "approved": list({m[1]: {"at": clock(at(r["at"])), "number": int(m[1]), "title": pr_title(model, m[1])} for r in records if r["kind"] == "go" and (m := GO.match(r["text"]))}.values()),
        "releases": [{k: r.get(k) for k in ("name", "state", "went_wrong")} for r in model["now"].get("releases", [])],
        "coordinator_rulings": rulings(model["records"], now),
        "open_changes": open_changes(model, change_records(model, now)),
        "open_questions": [item["card"]["title"] for item in model["needs"] if item["kind"] == "card"],
        "landed": [
            {"at": clock(at(pr["landed_at"])), "number": int(n), "title": pr.get("title"), "part": steps.get(entries.get(int(n), {}).get("step"))}
            for n, pr in sorted(model["prs"].items(), key=lambda item: item[1].get("landed_at") or "")
            if entries.get(int(n), {}).get("state") == "landed" and pr.get("landed_at") and at(pr["landed_at"]) >= start
        ],
        "previous": previous_of(previous, HISTORY_SCHEMA),
        "worker_names": worker_names(model),
    }


def review_slot(path):
    slots = {"rows": ROW_SLOTS, "groups": GROUP_SLOTS, "cards": CARD_SLOTS}.get(path[0], {})
    return slots.get(path[2]) if len(path) > 2 else None


def source_key(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def stamp(board):
    for row in board["rows"]:
        row["key"] = source_key(*(row.get(k) for k in ("number", "head", *ROW_SLOTS)))
    for card in board["cards"]:
        card["key"] = source_key(card["id"], card["title"], card["summary"], card["recommended"], [o["label"] for o in card["options"]], open_question(card))


def open_question(card):
    return next((part["prompt"] for part in card["parts"] if part["kind"] in ("choice", "approval") and not part["answer"]), None)


def review_digest(model, config, previous, now):
    lanes = tuple(worker_names(model))
    kept = {entry["key"]: entry for kind in ("rows", "groups", "cards") for entry in (previous or {}).get(kind, [])}
    rows = []
    for row in model["board"]["rows"]:
        problems = {field: found for field, slot in ROW_SLOTS.items() if (found := plainwords.violations(row.get(field) or "", slot, lanes))}
        if not problems:
            continue
        mention = re.compile(rf"#{row['number']}\b")
        rows.append({
            **{k: row.get(k) for k in ("key", *ROW_SLOTS)},
            "problems": problems,
            "previous_rewrite": kept.get(row["key"]),
            "review_records": [said(r, 500) for r in model["records"] if r["kind"] in ("review", "report", "head") and mention.search(r["text"])][-5:],
        })
    titles = {row["number"]: row.get("title") for row in model["board"]["rows"]}
    groups = [
        {"key": item["key"], "title": item["group"], "summary": item["relation"], "changes": [titles.get(row["number"]) for row in item["prs"]], "previous_rewrite": kept.get(item["key"])}
        for item in model["needs"] if item["kind"] == "prs"
    ]
    cards = [
        {"key": card["key"], "title": card["title"], "summary": clip(card["summary"], 900), "open_question": open_question(card), "answers": [o["label"] for o in card["options"]], "recommended": card["recommended"], "previous_rewrite": kept.get(card["key"])}
        for card in (item["card"] for item in model["needs"] if item["kind"] == "card")
    ]
    if all(entry["previous_rewrite"] for entry in (*rows, *groups, *cards)):
        return None
    return {"now": clock(now), "rows": rows, "groups": groups, "cards": cards, "open_changes": open_changes(model, change_records(model, now)), "worker_names": list(lanes)}

PAGES = (
    Page("plan", PLAN_PROMPT, PLAN_SCHEMA, plan_digest, plan_slot, frozenset({"blocker", "ask", "answer", "decide", "go", "landed", "release", "incident", "mechanism", "fix-live", "hold", "lift", "defect"}), 180, unchecked, plan_settle),
    Page("work", WORK_PROMPT, WORK_SCHEMA, work_digest, work_slot, frozenset({*WORK_KINDS, "head", "review", "defect", "fix-live", "go", "decide", "hold", "lift", "answer", "evidence"}), 180, work_checks, work_settle),
    Page("history", HISTORY_PROMPT, HISTORY_SCHEMA, history_digest, history_slot, frozenset({*HISTORY_KINDS, "go", "landed"}), 300),
    Page("review", REVIEW_PROMPT, REVIEW_SCHEMA, review_digest, review_slot, frozenset({"review", "head", "go", "report"}), 60),
)
