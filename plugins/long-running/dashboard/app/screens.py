import re

SCREENS = ("now", "plan", "status", "work", "review", "history", "asks")
RECORDS_PER_ROW = 5
HISTORY_ROWS = 200
PLAN_STATUS = {"done": "done", "live": "done", "doing": "doing", "you": "needs_you", "stuck": "stuck", "waiting": "waiting", "later": "later"}
TONE_STATUS = {"ok": "done", "acc": "doing", "you": "needs_you", "bad": "stuck", "waiting": "waiting", "": "later"}
ASK_STATE = {"open": ("asked", "waiting", "Asked"), "responded": ("answered", "doing", "Answered"), "addressed": ("applied", "done", "Applied")}
LEDGER_ROW = re.compile(r"^\| (m\d+) \| ([^|]*) \| (.*) \| ([^|]*) \| (open|responded|addressed) \|$", re.MULTILINE)


def row(id, title, status, subtitle="", link="", facets=None, records=()):
    return {"id": id, "title": title, "status": status, "subtitle": subtitle, "link": link, "facets": facets or {}, "records": list(records)}


def by_lane(view):
    lanes = {}
    for record in view.get("records", []):
        lanes.setdefault(re.sub(r"-\d+$", "", record["lane"]), []).append(record)
    return lanes


def lane_records(lanes, names):
    found = [record for name in names for record in lanes.get(re.sub(r"-\d+$", "", name), [])[-RECORDS_PER_ROW:]]
    return [{key: record.get(key) for key in ("seq", "kind", "lane", "text", "at")} for record in sorted(found, key=lambda r: r["seq"])[-RECORDS_PER_ROW:]]


def now_screen(view, _ledger):
    rows = []
    for need in view["needs"]:
        if need["kind"] == "card":
            rows.append(row(need["card"]["id"], need["card"]["title"], "needs_you", need["why"], f"#/now?row={need['card']['id']}", {"kind": "question"}))
        else:
            rows.append(row(need["key"], need["group"], "needs_you", need["why"], f"#/review?row={need['key']}", {"kind": "merge"}))
    brief = view.get("brief") or {}
    return {"head": {"headline": brief.get("headline"), "because": brief.get("because", []), "progress": brief.get("progress")}, "rows": rows}


def plan_screen(view, _ledger):
    plan = view["pages"].get("plan") or {}
    lanes = by_lane(view)
    said = view.get("step_records", {})
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


def status_screen(view, _ledger):
    lanes = by_lane(view)
    rows = [
        row(
            f"st-plan-{index}",
            step["title"],
            TONE_STATUS.get(step.get("tone", ""), "info"),
            step["state"],
            f"#/status/plan?row=st-plan-{index}",
            {"feature": "plan"},
            lane_records(lanes, step.get("lanes", [])),
        )
        for index, step in enumerate(view["steps"])
    ]
    for table in view.get("status_tables", []):
        rows += [
            row(
                f"st-{table['id']}-{index}",
                entry["step"],
                TONE_STATUS.get(entry.get("tone", ""), "info"),
                entry["state"],
                f"#/status/{table['id']}?row=st-{table['id']}-{index}",
                {"feature": table["id"]},
                lane_records(lanes, entry.get("lanes", [])),
            )
            for index, entry in enumerate(table["rows"])
        ]
    return {"head": {"said": view.get("status_now")}, "rows": rows}


def work_screen(view, _ledger):
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


def review_screen(view, _ledger):
    board = view["board"]
    merges = [
        row(str(entry["number"]), entry["title"], "needs_you" if not entry.get("verdict") else "done", entry["blocker"], f"#/review/{entry['number']}", {"group": entry["group"]})
        for entry in board["rows"]
    ]
    questions = [row(card["id"], card["title"], "needs_you", card.get("recommended", ""), f"#/review?row={card['id']}", {"group": "Questions"}) for card in board["cards"]]
    return {"head": {"url": board.get("url")}, "rows": merges + questions}


def history_screen(view, _ledger):
    entries = (view["pages"].get("history") or {}).get("entries", [])
    rows = [row(f"h-{index}", entry["title"], "info", entry["detail"], f"#/history?row=h-{index}", {"kind": entry["kind"]}) for index, entry in enumerate(entries[:HISTORY_ROWS])]
    return {"head": {"summary": (view["pages"].get("history") or {}).get("summary")}, "rows": rows}


def asks_screen(_view, ledger):
    rows = []
    for key, at, message, routed, state in LEDGER_ROW.findall(ledger):
        word, status, label = ASK_STATE[state]
        first = message.replace("<br>", " ")
        rows.append(row(f"ask-{key}", first, status, f"{label}, {at.strip()}", f"#/asks?row=ask-{key}", {"state": word, "record": routed.strip()}))
    return {"head": {}, "rows": rows[::-1]}


BUILDERS = {"now": now_screen, "plan": plan_screen, "status": status_screen, "work": work_screen, "review": review_screen, "history": history_screen, "asks": asks_screen}


def screen_view(name, view, ledger, version):
    built = BUILDERS[name](view, ledger)
    facets = {}
    for entry in built["rows"]:
        for facet, value in entry["facets"].items():
            facets.setdefault(facet, {})[value] = True
    filters = [{"facet": facet, "values": list(values)} for facet, values in facets.items()]
    return {"screen": name, "version": version, **built, "filters": filters}


def changed_screens(past, current):
    if past is None:
        return list(SCREENS)
    return [name for name in SCREENS if {k: d for k, d in past.get(name, {}).items() if k != "version"} != {k: d for k, d in current[name].items() if k != "version"}]
