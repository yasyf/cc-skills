"""His Approve and Hold cards, drawn by the supervisor and never by an owner, so a PR waiting on his tap always gets live buttons. Each lane's ready stacks that no card or post asked about at their heads go to cc-slack in one draw, which runs the PR-group card command, `drive card`, and posts one card per stack in the lane's channel, each with its own Approve and Hold, as the old drive drew them. A PR some other post already asked about at its head, by its row's carded mark, which the voice writes too, gets no second card. His tap comes back through that command's answer, which writes it at the heads the card asked about through lane's own verb and marks the lane's card tapped here."""

import secrets
from concurrent.futures import ThreadPoolExecutor

from lane import page, say, store

DRAW_SECONDS = 90
DRAWERS = 4


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


def pr_url(key: str) -> str:
    repo, _, number = key.rpartition("#")
    return f"https://github.com/{repo}/pull/{number}"


def drawn(drive: str, lane: dict, stacks: list[dict[str, str]]) -> str:
    """cc-slack's draw of the stacks' PR-group cards in the lane's channel, under the lane's thread when it has one, with no words of its own, so only the cards post: the permalinks of the cards it drew."""
    from drive import slack

    argv = ["dm-status", "--agent", "claude", "--drive", drive, "--as-holder", "--channel", lane["channel"], *(["--thread", lane["thread"]] if lane.get("thread") else [])]
    argv += [flag for heads in stacks for key in heads for flag in ("--tap-pr", pr_url(key))]
    return slack.cc_slack(*argv, timeout=DRAW_SECONDS).get("card_permalink") or "drawn"


def asked(drive: str, lane_id: str, lane: dict, stacks: list[dict[str, str]], ready: list[dict[str, str]]) -> str | None:
    """Draw one lane's cards for its waiting stacks and keep one card on the lane per stack, as cc-slack posts them, each older card whose open PRs they all ask about again marked superseded by the one asking about its PRs, and each PR row carded at the head asked; why the draw failed, or None."""
    from drive import slack

    try:
        sent = drawn(drive, lane, stacks) if store.config(drive)["posting"] else say.logged(drive, lane, "")
    except (store.Refused, slack.SlackError) as failed:
        return f"{lane_id}: its Approve cards were not drawn: {failed}"
    drawn_cards = [{"id": secrets.token_hex(3), "stacks": [heads], "at": store.stamp(), "sent": sent} for heads in stacks]
    keys = {key for heads in stacks for key in heads}
    open_keys = {key for heads in ready for key in heads}

    def keeping(lane: dict) -> None:
        for older in lane.get("cards", []):
            if not older.get("tapped") and not older.get("superseded") and (held := {key for heads in older["stacks"] for key in heads}) & open_keys <= keys:
                older["superseded"] = next((card["id"] for card in drawn_cards if held & set(card["stacks"][0])), drawn_cards[0]["id"])
        lane["cards"] = [*lane.get("cards", []), *drawn_cards][-50:]
        for row in lane["prs"]:
            for card in drawn_cards:
                if (key := f"{row['repo']}#{row['number']}") in card["stacks"][0]:
                    row["carded"] = {"head": card["stacks"][0][key], "by": f"card {card['id']}", "at": store.stamp()}
        store.note(lane, f"his Approve cards asked about {', '.join(sorted(keys))} ({sent})")

    store.update(drive, lane_id, keeping)
    return None


def draw(drive: str) -> list[str]:
    """Draw each lane's cards the pass its stacks turn ready, for the stacks no card or post asked about at their heads yet, the lanes' draws side by side so one slow cc-slack draw never holds another lane's cards; why each lane's cards were not drawn, which never stops the pass."""
    if not store.live(drive) or not store.config(drive).get("approve_cards", True):
        return []
    seen = page.view(drive)["prs"]
    refused, asking = [], []
    for lane_id, lane in store.load_all(drive).items():
        if (lane.get("channel") or "").startswith(say.DIRECT):
            refused.append(f"{lane_id}: no Approve card, since its home is his DM; move it with `lane home`")
            continue
        if waiting := stacks_waiting(lane, seen):
            asking.append((lane_id, lane, waiting, ready_stacks(lane, seen)))
    if not asking:
        return refused
    with ThreadPoolExecutor(max_workers=DRAWERS) as pool:
        refused += [why for why in pool.map(lambda each: asked(drive, *each), asking) if why]
    return refused


def answered(drive: str, key: str, head: str, label: str, ts: str | None) -> None:
    """Mark each open card of the drive's lanes asking about the PR at that head tapped with his label, so no lane reads it as still asking him."""

    def marking(lane: dict) -> None:
        for card in lane.get("cards", []):
            if not card.get("tapped") and not card.get("superseded") and any(heads.get(key) == head for heads in card["stacks"]):
                card["tapped"] = {"label": label, "ts": ts}
                store.note(lane, f"his {label} on card {card['id']}")

    for lane_id, lane in store.load_all(drive).items():
        if any(heads.get(key) == head for card in lane.get("cards", []) for heads in card["stacks"]):
            store.update(drive, lane_id, marking)
