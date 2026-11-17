"""His Approve and Hold cards, drawn by the supervisor and never by an owner, so a PR waiting on his tap always gets live buttons. Each lane's ready stacks that no card or post asked about at their heads go to cc-slack in one draw, which runs the PR-group card command, `drive card`, and posts one card per stack in the lane's channel, each with its own Approve and Hold, as the old drive drew them. A PR some other post already asked about at its head, by its row's carded mark, which the voice writes too, gets no second card. A stack's card is titled by its owner's name for it, its bottom PR row's words, whole; a stack with no name, or one too long for the card's title, draws under its bottom PR's GitHub title, whole, and its owner is asked once per head to name it, its name replacing the title when the card next draws: a missing name never holds a card. Each stack draws in its own call, so one stack cc-slack refuses, such as one whose PR closed since the last read, never holds the lane's other cards. His tap comes back through that command's answer, which writes it at the heads the card asked about through lane's own verb and marks the lane's card tapped here."""

import secrets
from concurrent.futures import ThreadPoolExecutor

from lane import page, say, store, verbs

DRAW_SECONDS = 90
DRAWERS = 4
NAME_CHARS = 140
NAME_ASK = 'Name this stack for his Approve card in a few words: `lane pr add {key} --words "…"`. Its card drew under the PR\'s GitHub title meanwhile.'
NAME_TOO_LONG = 'The name of the stack under {key} is {length} characters, more than the {limit} his Approve card\'s title holds, so its card drew under the PR\'s GitHub title: name it again in a few words with `lane pr add {key} --words "…"`.'


def ready_stacks(lane: dict, seen: dict) -> list[dict[str, str]]:
    """Each ready stack of the lane his tap would land, as {PR key: head} bottom first: open, green and reviewed, by page.ready, never one GitHub reads merged, and never a PR the tooling class lands without him."""
    rows = [row for row in page.tapless(lane, seen) if not (row.get("guarded") or {}).get("tooling")]
    stacks, _ = page.ready(rows, seen, page.approved(lane))
    found = [{key: seen[key]["head"] for key in [f"{row['repo']}#{row['number']}", *row["above"]] if seen[key]["state"] == "OPEN"} for row in stacks]
    return [heads for heads in found if heads]


def stacks_waiting(lane: dict, seen: dict) -> list[dict[str, str]]:
    """The ready stacks no live PR-group card asked about at their heads yet, by each row's carded mark: a mark by a card since superseded asks nothing, so a stack whose card another draw replaced is drawn again, and a mark an older voice post wrote with its own Approve buttons asks nothing either, since the card is now the one place he taps Approve."""
    gone = {f"card {card['id']}" for card in lane.get("cards", []) if card.get("superseded")}
    carded = {f"{row['repo']}#{row['number']}": (row.get("carded") or {}).get("head") for row in lane["prs"] if (by := (row.get("carded") or {}).get("by") or "").startswith("card ") and by not in gone}
    return [heads for heads in ready_stacks(lane, seen) if any(carded.get(key) != head for key, head in heads.items())]


def pr_url(key: str) -> str:
    repo, _, number = key.rpartition("#")
    return f"https://github.com/{repo}/pull/{number}"


def stack_name(lane: dict, heads: dict[str, str]) -> str:
    """The owner's name for the stack, its bottom PR row's words, which `lane pr add <repo#N> --words` sets; empty when it has none."""
    key = next(iter(heads))
    return next((row.get("words") or "" for row in lane["prs"] if f"{row['repo']}#{row['number']}" == key), "")


def named(words: str) -> bool:
    return bool(words) and len(words) <= NAME_CHARS


def card_title(lane: dict, heads: dict[str, str], seen: dict) -> str:
    """The stack's card title: its owner's name for it when one fits, else its bottom PR's GitHub title, whole."""
    if named(words := stack_name(lane, heads)):
        return words
    key = next(iter(heads))
    return (seen.get(key) or {}).get("title") or key


def ask_name(drive: str, lane_id: str, lane: dict, heads: dict[str, str]) -> None:
    """Ask the lane's owner, once per head of the stack's bottom PR and name it carries, to name a stack with no name or one too long for the card's title, in a handover entry and a wake of the lane if it waits."""
    key = next(iter(heads))
    words = stack_name(lane, heads)
    if any(f"{row['repo']}#{row['number']}" == key and row.get("name_asked") == {"head": heads[key], "words": words} for row in lane["prs"]):
        return
    verbs.handover(drive, lane_id, NAME_TOO_LONG.format(key=key, length=len(words), limit=NAME_CHARS) if words else NAME_ASK.format(key=key), "supervisor")

    def asking(lane: dict) -> None:
        for row in lane["prs"]:
            if f"{row['repo']}#{row['number']}" == key:
                row["name_asked"] = {"head": heads[key], "words": words}

    store.update(drive, lane_id, asking)
    verbs.wake(drive, lane_id, f"the stack under {key} needs a name for his Approve card", if_waiting=True)


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
            if not older.get("tapped") and not older.get("superseded") and (reasked := {key for heads in older["stacks"] for key in heads} & open_keys) and reasked <= keys:
                older["superseded"] = next((card["id"] for card in drawn_cards if reasked & set(card["stacks"][0])), drawn_cards[0]["id"])
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
        if lane["state"] == store.PARKED:
            continue
        if (lane.get("channel") or "").startswith(say.DIRECT):
            refused.append(f"{lane_id}: no Approve card, since its home is his DM; move it with `lane home`")
            continue
        ready = ready_stacks(lane, seen)
        for heads in ready:
            if not named(stack_name(lane, heads)):
                ask_name(drive, lane_id, lane, heads)
        if waiting := stacks_waiting(lane, seen):
            asking.append((lane_id, lane, waiting, ready))
    if not asking:
        return refused
    with ThreadPoolExecutor(max_workers=DRAWERS) as pool:
        refused += [why for whys in pool.map(lambda each: [asked(drive, each[0], each[1], [heads], each[3]) for heads in each[2]], asking) for why in whys if why]
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


def settle(drive: str) -> list[str]:
    """Settle each open card from its rows, by his order that "messages should be tagged such that when the thing underneath them (such as an approval) changes they change state": a card whose every PR carries his Approve at the head it asked, or his Hold, or is no longer open, is redrawn from `drive card` as it stands, with no Approve, and marked settled, whoever wrote the rows, his tap, `lane approve` or a carry; why each card stayed as drawn."""
    from drive import slack

    seen = page.view(drive)["prs"]
    stayed = []
    for lane_id, lane in store.load_all(drive).items():
        rows = {f"{row['repo']}#{row['number']}": row for row in lane["prs"]}
        done = [card for card in lane.get("cards", []) if not card.get("tapped") and not card.get("superseded") and all(
            (row := rows.get(key) or {}).get("approved_head") == head or row.get("held") or (seen.get(key) or {}).get("state", "OPEN") != "OPEN"
            for heads in card["stacks"] for key, head in heads.items())]
        for card in done:
            try:
                for key in card["stacks"][0] if store.config(drive)["posting"] else []:
                    slack.redraw_pr_cards(pr_url(key))
            except slack.SlackError as failed:
                stayed.append(f"{lane_id}: card {card['id']} stays as drawn: {failed}")
                continue

            def settling(lane: dict, settled: str = card["id"]) -> None:
                for each in lane.get("cards", []):
                    if each["id"] == settled:
                        each["tapped"] = {"label": "settled from its rows", "ts": None}
                store.note(lane, f"card {settled} settled from its rows")

            store.update(drive, lane_id, settling)
    return stayed
