import re

LAND, PICK, SLACK = "land", "pick", "slack"
KIND_ORDER = {LAND: 0, PICK: 1, SLACK: 2}
SLACK_POST = re.compile(r"\b(?:post|reply|respond)\b.*(?:\bthread\b|\bchannel\b|#)", re.I)
ON_HIS_TAP = re.compile(r"\b(?:his|the owner'?s?|owner)\s+(?:own\s+)?(?:taps?|picks?|answers?|approval)\b", re.I)
DESK = re.compile(r"^(?:board-refresher|board-watch|card-mirror|owner-relay|desk-runner|supervisor|fidelity|finder|grader|judge|go-sweep|progress-desk|meta-desk|root$)")
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
PR_NUMBER = re.compile(r"(?:#|/pull/)(\d{5})\b")
TRAILING_NUMBER = re.compile(r"(\d{5})$")
SENTENCE = re.compile(r"(?<=\.)\s+")
RAW_THREAD = re.compile(r"\bin C[0-9A-Z]{8,}, thread \d+\.\d+")
SLACK_LINK = re.compile(r"<https?://[^|>]+\|([^>]+)>")
SLACK_URL = re.compile(r"<(https?://[^>]+)>")
SLACK_PERSON = re.compile(r"<@[A-Z0-9]+>")
SLACK_GROUP = re.compile(r"<!(here|channel)>")
SETTLED = re.compile(r"^(?:Answered|Withdrawn) on ")
FIRST_SENTENCE = re.compile(r"^(.{20,}?[.?!])(?:\s|$)", re.S)
SCOPE = re.compile(r"^[\w./-]+(?:, [\w./-]+)*:\s*(?:[^\w\s]+\s*)?")
TITLES_NAMED = 2
TITLE_CHARS = 70


def plain_title(title):
    text = SCOPE.sub("", title)
    return text[:1].upper() + text[1:]


def first_sentence(text):
    text = " ".join(str(text or "").split())
    found = FIRST_SENTENCE.match(text)
    return found[1] if found else text


def slack_text(md):
    text = SLACK_LINK.sub(r"\1", md)
    text = SLACK_URL.sub(r"\1", text)
    text = SLACK_PERSON.sub("@someone", text)
    return SLACK_GROUP.sub(r"@\1", text)


def slack_cards(outcomes, subject):
    answers = outcomes["interactions"]
    retired = set(outcomes.get("retired") or [])
    cards = []
    for block in outcomes["doc"]["blocks"]:
        if block["type"] != "card" or "slack" not in block or block["id"] in retired:
            continue
        children = {child["id"]: child for child in block.get("children", [])}
        choice = children.get(f"{block['id']}-answer")
        thread = answers["replies"].get(block["id"], [])
        if not choice or answers["choices"].get(choice["id"]) or any(SETTLED.match(reply["md"]) for reply in thread):
            continue
        says = [child["md"] for child in children.values() if child["type"] == "markdown"]
        options = [{"id": option["id"], "label": option["label"], "recommended": bool(option.get("recommended"))} for option in choice["options"]]
        cards.append({
            "id": block["id"],
            "title": RAW_THREAD.sub("in a Slack thread", block.get("title", "")),
            "summary": says[0].removeprefix("Why: ") if says else "",
            "text": slack_text(says[-1]) if says else "",
            "choice": choice["id"],
            "options": options,
            "recommended": next((option["label"] for option in options if option["recommended"]), None),
            "subject": subject,
        })
    return cards


def open_blocks(records, lanes):
    replaced = {record.get("resolves") for record in records}
    done = {lane["lane"] for lane in lanes if lane.get("kind") == "done"}
    return [record for record in records if record["seq"] not in replaced and record["lane"] not in done and not DESK.match(record["lane"]) and record.get("status") != "closed"]


def card_threads(events):
    threads = {}
    for block, kind, at, text, md in events:
        said = text if kind == "feedback.created" else md
        if said and not SETTLED.match(said):
            threads.setdefault(block, []).append({"who": "you" if kind == "feedback.created" else "answer", "text": said, "at": at})
    return threads


def stacked_above(entries):
    children = {}
    for entry in entries:
        if entry.get("base") and entry["state"] != "landed":
            children.setdefault(entry["base"], []).append(entry["number"])
    above = {}
    for entry in entries:
        found, pending = [], list(children.get(entry["number"], []))
        while pending:
            number = pending.pop()
            found.append(number)
            pending += children.get(number, [])
        above[entry["number"]] = found
    return above


def ref_numbers(record):
    found = (TRAILING_NUMBER.search(str(pr)) for pr in (record.get("refs") or {}).get("prs", []))
    return {int(match[1]) for match in found if match}


def parked_on(blocks, card_ids):
    lanes, named = {}, {}
    for record in blocks:
        for sentence in SENTENCE.split(record["text"]):
            cards = card_ids.intersection(TOKEN.findall(sentence))
            numbers = {int(number) for number in PR_NUMBER.findall(sentence)}
            for card in cards:
                lanes.setdefault(card, set()).add(record["lane"])
                named.setdefault(card, set()).update(numbers)
            if not cards and ON_HIS_TAP.search(sentence):
                for number in numbers or ref_numbers(record):
                    lanes.setdefault(number, set()).add(record["lane"])
    return lanes, named


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def clipped(title):
    return title if len(title) <= TITLE_CHARS else f"{title[:TITLE_CHARS].rsplit(' ', 1)[0]}…"


def freed_words(prs, lanes, title_of):
    said = []
    if prs:
        titles = [clipped(title_of(number)) for number in sorted(prs)[:TITLES_NAMED]]
        rest = len(prs) - len(titles)
        said.append(f"Frees {plural(len(prs), 'change')}: {'; '.join(titles)}{f'; and {rest} more' if rest else ''}.")
    if lanes:
        said.append(f"{plural(len(lanes), 'worker')} stopped until you tap.")
    return " ".join(said)


def land_tap(row, above, lanes, title_of):
    number = row["number"]
    prs = set(above.get(number, []))
    waiting = set(lanes.get(number, set())).union(*(lanes.get(pr, set()) for pr in prs))
    words = freed_words(prs, waiting, title_of)
    return {"kind": LAND, "id": f"land-{number}", "number": number, "title": row.get("title") or title_of(number), "frees": len(prs) + len(waiting), "line": words or f"Nothing else waits on it. {first_sentence(row.get('change'))}".strip(), "recommended": "Approve"}


def card_tap(card, kind, above, lanes, named, entries, title_of):
    cited = {*named.get(card["id"], set()), *card.get("prs", [])}
    numbers = {number for number in cited if kind != SLACK and number in entries and entries[number]["state"] != "landed"}
    prs = numbers.union(*(above.get(number, []) for number in numbers))
    waiting = set(lanes.get(card["id"], set())).union(*(lanes.get(pr, set()) for pr in prs))
    words = freed_words(prs, waiting, title_of)
    summary = slack_text(first_sentence(card.get("summary")))
    line = f"{words or 'No worker is waiting on this post.'} {summary}" if kind == SLACK else words or f"No recorded work waits on it. {summary}"
    tap = {"kind": kind, "id": card["id"], "card": card["id"], "title": card["title"], "frees": len(prs) + len(waiting), "line": line.strip(), "recommended": card.get("recommended"), "choice": card.get("choice")}
    if "subject" in card:
        tap.update({key: card[key] for key in ("text", "options", "subject")})
    return tap


def waits_on_him(rows):
    return [row for row in rows if row["state"] == "you" and not row.get("awaiting")]


def tap_queue(rows, cards, slack, drive_prs, blocks, title_of, urgent=()):
    entries = {entry["number"]: entry for entry in drive_prs}
    above = stacked_above(drive_prs)
    lanes, named = parked_on(blocks, {card["id"] for card in [*cards, *slack]})
    taps = [
        *(land_tap(row, above, lanes, title_of) for row in waits_on_him(rows)),
        *(card_tap(card, SLACK if card["id"].startswith("slack-") or SLACK_POST.search(card["title"]) else PICK, above, lanes, named, entries, title_of) for card in cards if card["decides"]),
        *(card_tap(card, SLACK, above, lanes, named, entries, title_of) for card in slack),
    ]
    return sorted(({**tap, "urgent": tap["id"] in urgent} for tap in taps), key=lambda tap: (not tap["urgent"], -tap["frees"], KIND_ORDER[tap["kind"]]))


def in_queue_order(queue, items, key_of):
    rank = {tap["id"]: at for at, tap in enumerate(queue)}
    return sorted(items, key=lambda item: rank.get(key_of(item), len(rank)))
