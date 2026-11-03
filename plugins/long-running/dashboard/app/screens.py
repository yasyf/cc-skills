import datetime as dt
import re

SCREENS = ("now", "plan", "status", "work", "review", "history", "asks")
RECORDS_PER_ROW = 5
RECORD_TEXT = 280
HISTORY_ROWS = 200
PLAN_STATUS = {"done": "done", "live": "done", "doing": "doing", "you": "needs_you", "stuck": "stuck", "waiting": "waiting", "later": "later"}
ASK_STATUS = {"asked": ("waiting", "Asked"), "answered": ("doing", "Answered"), "applied": ("done", "Applied"), "live": ("doing", "Live")}
STORE_WORDS = re.compile(r"^Owner(?: pick)? .+?(?: on card [\w-]+)?: (.*)$", re.S)
ROW_DONE = re.compile(r"(done|live|landed)\b", re.I)
ROW_WAITS_ON_HIM = re.compile(r"waits on your?\b", re.I)
ROW_NAMES_HIM = re.compile(r"\b(your|you)\b", re.I)
ROW_STUCK = re.compile(r"\b(nobody|no one|stuck|fails?|failing|broken)\b", re.I)


def row(id, title, status, subtitle="", link="", facets=None, records=()):
    return {"id": id, "title": title, "status": status, "subtitle": subtitle, "link": link, "facets": facets or {}, "records": list(records)}


def by_lane(view):
    lanes = {}
    for record in view.get("records", []):
        lanes.setdefault(re.sub(r"-\d+$", "", record["lane"]), []).append(record)
    return lanes


def lane_records(lanes, names):
    found = [record for name in names for record in lanes.get(re.sub(r"-\d+$", "", name), [])[-RECORDS_PER_ROW:]]
    newest = sorted(found, key=lambda r: r["seq"])[-RECORDS_PER_ROW:]
    return [{**{key: record.get(key) for key in ("seq", "kind", "lane", "at")}, "text": record["text"][:RECORD_TEXT]} for record in newest]


def now_screen(view):
    rows = []
    for need in view["needs"]:
        if need["kind"] == "card":
            rows.append(row(need["card"]["id"], need["card"]["title"], "needs_you", need["why"], f"#/now?row={need['card']['id']}", {"kind": "question"}))
        else:
            rows.append(row(need["key"], need["group"], "needs_you", need["why"], f"#/review?row={need['key']}", {"kind": "merge"}))
    brief = view.get("brief") or {}
    return {"head": {"headline": brief.get("headline"), "because": brief.get("because", []), "progress": brief.get("progress")}, "rows": rows}


def plan_screen(view):
    plan = view["pages"].get("plan") or {}
    lanes = by_lane(view)
    said = view["step_records"]
    rows = [
        row(
            part["id"],
            part["name"],
            PLAN_STATUS[part["state"]],
            part["label"],
            f"#/plan?row={part['id']}",
            {"state": part["label"]},
            lane_records(lanes, said.get(part["id"], {}).get("lanes", [])),
        )
        for part in plan.get("parts", [])
    ]
    return {"head": {"summary": plan.get("summary"), "milestones": plan.get("milestones", [])}, "rows": rows}


def owner_row_status(entry):
    state, blocks = entry["state"], entry.get("blocks") or ""
    if ROW_DONE.match(state):
        return "done"
    if ROW_NAMES_HIM.search(blocks) or ROW_WAITS_ON_HIM.match(state):
        return "needs_you"
    if ROW_STUCK.search(f"{entry['step']} {state} {blocks}"):
        return "stuck"
    return "doing"


def status_screen(view):
    lanes = by_lane(view)
    said = view["step_records"]
    rows = [
        row(
            f"st-plan-{index}",
            part["name"],
            PLAN_STATUS[part["state"]],
            part["label"],
            f"#/status/plan?row=st-plan-{index}",
            {"feature": "plan"},
            lane_records(lanes, said.get(part["id"], {}).get("lanes", [])),
        )
        for index, part in enumerate((view["pages"].get("plan") or {}).get("parts", []))
    ]
    for table in view["status_tables"]:
        rows += [
            row(
                f"st-{table['id']}-{index}",
                entry["step"],
                owner_row_status(entry),
                entry["state"],
                f"#/status/{table['id']}?row=st-{table['id']}-{index}",
                {"feature": table["id"]},
                lane_records(lanes, entry.get("lanes", [])),
            )
            for index, entry in enumerate(table["rows"])
        ]
    return {"head": {"said": view.get("status_now")}, "rows": rows}


def work_screen(view):
    work = view["pages"].get("work") or {}
    lanes = by_lane(view)
    running = [
        row(item["item"], item["title"], "doing", item["latest"], f"#/work?row={item['item']}", {"state": item["status"]}, lane_records(lanes, item["workers"]))
        for item in work.get("running", [])
    ]
    finished = [
        row(item["item"], item["title"], "done", item["result"], f"#/work?row={item['item']}", {"state": item["outcome"]}, lane_records(lanes, item["workers"]))
        for item in work.get("finished", [])
    ]
    return {"head": {}, "rows": running + finished}


def review_screen(view):
    board = view["board"]
    merges = [
        row(str(entry["number"]), entry["title"], "needs_you" if not entry.get("verdict") else "done", entry["blocker"], f"#/review/{entry['number']}", {"group": entry["group"]})
        for entry in board["rows"]
    ]
    questions = [row(card["id"], card["title"], "needs_you", card.get("recommended", ""), f"#/review?row={card['id']}", {"group": "Questions"}) for card in board["cards"]]
    return {"head": {"url": board.get("url")}, "rows": merges + questions}


def history_screen(view):
    entries = (view["pages"].get("history") or {}).get("entries", [])
    rows = [row(f"h-{index}", entry["title"], "info", entry["detail"], f"#/history?row=h-{index}", {"kind": entry["kind"]}) for index, entry in enumerate(entries[:HISTORY_ROWS])]
    return {"head": {"summary": (view["pages"].get("history") or {}).get("summary")}, "rows": rows}


def clock(iso):
    return dt.datetime.fromisoformat(iso).strftime("%b %-d %-I:%M %p")


def ask_row(key, title, state, where, said="", records=()):
    status, word = ASK_STATUS[state]
    short = re.sub(r"^(.{96}\S*)\s.+$", r"\1…", title, flags=re.S)
    return row(key, short, status, f"{word}, {said}" if said else word, f"#/asks?row={key}", {"state": state, "where": where}, records)


def store_row(ask, lanes):
    words = STORE_WORDS.match(ask["text"])
    lost = ask["state"] == "LOST"
    return ask_row(f"ask-{ask['key']}", words[1] if words else ask["text"], "asked" if lost else "live", ask["lane"], "no progress recorded" if lost else "", lane_records(lanes, [ask["lane"]]))


def asks_screen(view):
    asks = view["owner_asks"]
    lanes = by_lane(view)
    dated = [
        (ask["iso"], ask_row(f"ask-m{ask['m']}", ask["text"], ask["state"], f"PR {ask['prs'][0]}" if ask["prs"] else ask["lanes"][0] if ask["lanes"] else "Chat", clock(ask["iso"]), lane_records(lanes, ask["lanes"])))
        for ask in asks["messages"]
    ]
    dated += [(tap["iso"], ask_row(f"ask-m{tap['m']}", tap["question"], tap["state"], "Board", clock(tap["iso"]))) for tap in asks["taps"] if not tap["pr"]]
    for state, words in (("applied", "landed"), ("answered", "not landed yet")):
        if approved := [tap for tap in asks["taps"] if tap["pr"] and tap["state"] == state]:
            latest = max(tap["iso"] for tap in approved)
            dated.append((latest, ask_row(f"ask-approved-{state}", f"{len(approved)} approved, {words}", state, "Board", clock(latest))))
    dated.sort(key=lambda entry: entry[0], reverse=True)
    stored = [store_row(ask, lanes) for ask in asks["store"]]
    live = [entry for entry in stored if entry["facets"]["state"] == "live"]
    lost = [entry for entry in stored if entry["facets"]["state"] == "asked"]
    return {"head": {}, "rows": [*live, *(entry for _, entry in dated), *lost]}


BUILDERS = {"now": now_screen, "plan": plan_screen, "status": status_screen, "work": work_screen, "review": review_screen, "history": history_screen, "asks": asks_screen}


def screen_view(name, view, version):
    built = BUILDERS[name](view)
    facets = {}
    for entry in built["rows"]:
        for facet, value in entry["facets"].items():
            facets.setdefault(facet, {})[value] = True
    filters = [{"facet": facet, "values": list(values)} for facet, values in facets.items()]
    return {"screen": name, "version": version, **built, "filters": filters}


def screen_kind(name):
    return f"screen-{name}"


def changed_screens(past, current):
    if past is None:
        return list(SCREENS)
    return [name for name in SCREENS if {k: d for k, d in past.get(screen_kind(name), {}).items() if k != "version"} != {k: d for k, d in current[screen_kind(name)].items() if k != "version"}]
