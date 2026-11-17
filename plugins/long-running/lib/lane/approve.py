"""His Approve and Hold cards, drawn by the supervisor and never by an owner: a card per ready stack per head, in the lane's channel, so a PR waiting on his tap always gets live buttons, and a tap on one lands as his Approve or Hold on each PR it names. A lane has one live card at a time, its first ready stack, so his channel never floods: the next one is drawn once he taps it, or once what it asked about merges or moves."""

import re

from lane import page, say, store, verbs, voice  # noqa: F401  voice sets say.SENDER

LABELS = ["Approve", "Hold"]
TAP = re.compile(r'tapped "(Approve|Hold)" on "(.*)"', re.S)
NUMBER = re.compile(r"#(\d+)")
HEAD = re.compile(r"\bat ([0-9a-f]{7})\b")


def stacks_waiting(lane: dict, seen: dict) -> list[dict[str, str]]:
    """Each ready stack of the lane his tap would land, as {PR key: head} bottom first: open, green and reviewed, by page.ready, never one GitHub reads merged, and never a PR the tooling class lands without him."""
    rows = [row for row in page.tapless(lane, seen) if not (row.get("guarded") or {}).get("tooling")]
    stacks, _ = page.ready(rows, seen)
    return [{key: seen[key]["head"] for key in [f"{row['repo']}#{row['number']}", *row["above"]] if seen[key]["state"] == "OPEN"} for row in stacks]


def text(lane: dict, heads: dict[str, str]) -> str:
    keys = list(heads)
    bottom = keys[0]
    named = ", ".join(f"{{pr:{key}}}" for key in keys)
    lead = f"Approve {named} at {heads[bottom][:7]}?"
    detail = "It is green and reviewed, and lands on your Approve." if len(keys) == 1 else f"These {len(keys)} PRs are one stack, green and reviewed, bottom first; your Approve lands them all."
    return f"{lead}\n{detail} Hold keeps them open until you say otherwise."


def draw(drive: str) -> list[str]:
    """Post a card for each lane whose ready stacks wait on his tap with no live card among them: for the first such stack at heads no card of the lane asked about; the keys of the bottom PRs it posted."""
    if not store.live(drive):
        return []
    seen = page.view(drive)["prs"]
    posted = []
    for lane_id, lane in store.load_all(drive).items():
        asked = {tuple(sorted(card["heads"].items())): card for card in lane.get("cards", [])}
        waiting = [heads for heads in stacks_waiting(lane, seen) if heads]
        if any(not asked[shape].get("tapped") for heads in waiting if (shape := tuple(sorted(heads.items()))) in asked):
            continue
        for heads in [heads for heads in waiting if tuple(sorted(heads.items())) not in asked][:1]:
            words = text(lane, heads)
            sent = say.SENDER(drive, lane, words, None, LABELS) if store.config(drive)["posting"] else say.logged(drive, lane, words, None, LABELS)

            def keeping(lane: dict, heads: dict[str, str] = heads, sent: str = sent) -> None:
                lane["cards"] = [*lane.get("cards", []), {"heads": heads, "at": store.stamp(), "sent": sent}][-50:]
                store.note(lane, f"his Approve card asked about {', '.join(heads)} ({sent})")

            store.update(drive, lane_id, keeping)
            posted.append(next(iter(heads)))
    return posted


def tapped(drive: str, lane: dict, words: str, ts: str, link: str | None) -> list[str] | None:
    """His tap on one of the lane's Approve cards, by the PR numbers and bottom head its first line names, written on each PR the card asked about at the head it asked about; None when the words are no tap on such a card."""
    if not (found := TAP.fullmatch(words.strip())):
        return None
    label, line = found[1], found[2]
    numbers, head = set(map(int, NUMBER.findall(line))), (HEAD.search(line) or [None, None])[1]
    card = next((card for card in reversed(lane.get("cards", [])) if {int(key.rpartition("#")[2]) for key in card["heads"]} == numbers and next(iter(card["heads"].values()))[:7] == head), None)
    if card is None:
        return None
    for key, at in card["heads"].items():
        repo, _, number = key.rpartition("#")
        verbs.tapped(drive, repo, int(number), label, at, link or "his Slack card", ts, named=True)

    def marking(lane: dict) -> None:
        for kept in lane.get("cards", []):
            if kept["heads"] == card["heads"]:
                kept["tapped"] = {"label": label, "ts": ts}

    store.update(drive, lane["id"], marking)
    return list(card["heads"])

