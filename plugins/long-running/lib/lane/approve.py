"""His Approve and Hold cards, drawn by the supervisor and never by an owner, so a PR waiting on his tap always gets live buttons. Each lane gets one card listing every ready stack it holds, with Approve all, One at a time and Hold all; One at a time turns the lane to one card per stack, the next drawn once he taps the last. A PR some other post already asked about at its head, by its row's carded mark, which the voice writes too, gets no second card. A tap lands as his Approve or Hold at the heads the card asked about."""

import re
import secrets
import urllib.error

from lane import page, say, store, verbs, voice  # noqa: F401  voice sets say.SENDER

ONE = "One at a time"
HOLD_ALL = "Hold all"
STACK_LABELS = ["Approve", "Hold"]
TAP = re.compile(r'tapped "(.+?)" on "(.*)"', re.S)
CARD_ID = re.compile(r"\bcard ([0-9a-f]{6})\b")


def ready_stacks(lane: dict, seen: dict) -> list[dict[str, str]]:
    """Each ready stack of the lane his tap would land, as {PR key: head} bottom first: open, green and reviewed, by page.ready, never one GitHub reads merged, and never a PR the tooling class lands without him."""
    rows = [row for row in page.tapless(lane, seen) if not (row.get("guarded") or {}).get("tooling")]
    stacks, _ = page.ready(rows, seen)
    found = [{key: seen[key]["head"] for key in [f"{row['repo']}#{row['number']}", *row["above"]] if seen[key]["state"] == "OPEN"} for row in stacks]
    return [heads for heads in found if heads]


def stacks_waiting(lane: dict, seen: dict) -> list[dict[str, str]]:
    """The ready stacks no post or card asked about at their heads yet, by each row's carded mark."""
    carded = {f"{row['repo']}#{row['number']}": (row.get("carded") or {}).get("head") for row in lane["prs"]}
    return [heads for heads in ready_stacks(lane, seen) if any(carded.get(key) != head for key, head in heads.items())]


def lane_text(lane: dict, stacks: list[dict[str, str]], card: str) -> str:
    count = sum(len(heads) for heads in stacks)
    lines = [f"{lane['title']}: {count} PR{'s' * (count != 1)} wait on your Approve, all green and reviewed (card {card})."]
    for heads in stacks:
        keys = list(heads)
        lines.append(f"- {{pr:{keys[0]}}}" + (f" and {len(keys) - 1} stacked above it" if len(keys) > 1 else ""))
    lines.append("Approve all lands them as they are; One at a time asks about each stack on its own; Hold all keeps every one open.")
    return "\n".join(lines)


def stack_text(heads: dict[str, str], card: str) -> str:
    keys = list(heads)
    named = ", ".join(f"{{pr:{key}}}" for key in keys)
    detail = "It is green and reviewed and lands on your Approve." if len(keys) == 1 else f"These {len(keys)} PRs are one stack, bottom first; your Approve lands them all."
    return f"Approve {named}? (card {card})\n{detail} Hold keeps them open until you say otherwise."


def draw(drive: str) -> list[str]:
    """Post each lane's card the pass its stacks turn ready, for the stacks no card or post asked about at their heads yet: one listing them all, or, once he chose One at a time, one for its first stack while no card of its own is live. Why each card that did not post failed, which never stops the pass."""
    if not store.live(drive):
        return []
    seen = page.view(drive)["prs"]
    refused = []
    for lane_id, lane in store.load_all(drive).items():
        if (lane.get("channel") or "").startswith(say.DIRECT):
            refused.append(f"{lane_id}: no Approve card, since its home is his DM; move it with `lane home`")
            continue
        waiting = stacks_waiting(lane, seen)
        ready = ready_stacks(lane, seen)
        live = [card for card in lane.get("cards", []) if not card.get("tapped") and any(heads in ready for heads in card["stacks"])]
        if not waiting or live and lane.get("card_mode") == ONE:
            continue
        card = secrets.token_hex(3)
        if lane.get("card_mode") == ONE:
            stacks, words, labels = waiting[:1], stack_text(waiting[0], card), STACK_LABELS
        else:
            count = sum(len(heads) for heads in waiting)
            stacks, words, labels = waiting, lane_text(lane, waiting, card), [f"Approve all {count} (Recommended)", ONE, HOLD_ALL]
        try:
            sent = say.SENDER(drive, lane, words, None, labels) if store.config(drive)["posting"] else say.logged(drive, lane, words, None, labels)
        except (store.Refused, urllib.error.URLError) as failed:
            refused.append(f"{lane_id}: its Approve card was not posted: {failed}")
            continue

        def keeping(lane: dict, card: str = card, stacks: list[dict[str, str]] = stacks, sent: str = sent) -> None:
            lane["cards"] = [*lane.get("cards", []), {"id": card, "stacks": stacks, "at": store.stamp(), "sent": sent}][-50:]
            for row in lane["prs"]:
                for heads in stacks:
                    if (key := f"{row['repo']}#{row['number']}") in heads:
                        row["carded"] = {"head": heads[key], "by": f"card {card}", "at": store.stamp()}
            store.note(lane, f"his Approve card {card} asked about {', '.join(key for heads in stacks for key in heads)} ({sent})")

        store.update(drive, lane_id, keeping)
    return refused


def tapped(drive: str, lane: dict, words: str, ts: str, link: str | None) -> list[str] | None:
    """His tap on one of the lane's cards, found by the card id its first line carries: an Approve or Hold, all or one stack, written at the heads the card asked about, or One at a time, which turns the lane to a card per stack; the PRs it wrote, or None when the words are no tap on such a card."""
    if not (found := TAP.fullmatch(words.strip())) or not (named := CARD_ID.search(found[2])):
        return None
    if not (card := next((card for card in lane.get("cards", []) if card["id"] == named[1]), None)):
        return None
    label = found[1]
    if label == ONE:
        written = []
    else:
        tap = "Hold" if label.startswith("Hold") else "Approve"
        written = [key for heads in card["stacks"] for key in heads]
        for heads in card["stacks"]:
            for key, head in heads.items():
                repo, _, number = key.rpartition("#")
                verbs.tapped(drive, repo, int(number), tap, head, link or "his Slack card", ts, named=True)

    def marking(lane: dict) -> None:
        for kept in lane.get("cards", []):
            if kept["id"] == card["id"]:
                kept["tapped"] = {"label": label, "ts": ts}
        if label == ONE:
            lane["card_mode"] = ONE
            for row in lane["prs"]:
                if (row.get("carded") or {}).get("by") == f"card {card['id']}":
                    row["carded"] = None
        store.note(lane, f"his {label} on card {card['id']}")

    store.update(drive, lane["id"], marking)
    store.kick(drive)
    return written or [f"card {card['id']}: {label}"]
