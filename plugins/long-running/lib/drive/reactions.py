"""The one reaction on each message of his an order answers, moved by the drive's state rather than by whoever last reacted, by his rule of Nov 16: eyes while a session reads or answers it, construction only while work it started runs, a check once it was handled, and none otherwise, each step taking the last one away, so a construction never outlives its work."""

import json
from datetime import datetime, timedelta

from drive import slack, state

EYES = "eyes"
BUILDING = "construction"
DONE = "white_check_mark"
STEPS = (EYES, BUILDING, DONE)
FILE = "reactions.json"
RECENT = timedelta(days=1)
RANK = (BUILDING, None, DONE)
MOVES_PER_PASS = 20


def wanted(order: dict, running: bool) -> str | None | bool:
    """The reaction his order's message carries now: construction while a job of the order runs, a check once it is done, none once it was dropped or answered with nothing running; False while no answer has gone out yet and nothing runs, so the eyes a session put on it stay."""
    if order["state"] in state.CLOSED_ORDER_STATES:
        return DONE if order["state"] == "done" else None
    if running:
        return BUILDING
    return None if order["last_line"] else False


def moved(name: str, moment: datetime) -> list[str]:
    """Move the reaction on each message of his an open or recently closed order answers to the one its orders want, construction while any runs work, else none while any is open, else a check, once each time that changes, removing every other step's, the newest messages first and at most MOVES_PER_PASS a pass so a first sweep never floods Slack; a line for each it moved."""
    orders, running = state.reaction_snapshot(name, moment - RECENT)
    path = state.drive_dir(name) / FILE
    kept = json.loads(path.read_text()) if path.exists() else {}
    steps: dict[str, list[str | None]] = {}
    for order_id, order in orders.items():
        if (step := wanted(order, order_id in running)) is not False and order["thread"][1]:
            steps.setdefault("/".join(order["thread"]), []).append(step)
    lines = []
    for key, wants in sorted(steps.items(), key=lambda item: item[0].split("/")[1], reverse=True):
        step = min(wants, key=RANK.index)
        if kept.get(key, "unset") == step:
            continue
        if len(lines) == MOVES_PER_PASS:
            break
        react(*key.split("/"), step, kept.get(key, True))
        kept[key] = step
        lines.append(f"his message {key} now carries {step or 'no reaction'}")
    path.write_text(json.dumps(kept))
    return lines


def react(channel: str, ts: str, step: str | None, was: str | None | bool = True) -> None:
    """Leave the one reaction step on his message, taking away the step it carried before, was, or every other step's when that is not known, True, as on a message the drive never moved, whose eyes a pickup put; a reaction already gone or already there is no failure. Taking away only the step it left keeps a move to one or two Slack calls, since taking away every step each move made 185 unreacts in ten minutes and hit Slack's rate limit."""
    for other in STEPS if was is True else (was,) if was else ():
        if other != step:
            try:
                slack.cc_slack("unreact", "--channel", channel, "--ts", ts, "--name", other)
            except slack.SlackError:
                pass
    if step:
        try:
            slack.cc_slack("react", "--channel", channel, "--ts", ts, "--name", step)
        except slack.SlackError:
            pass


def answered(name: str, channel: str, ts: str, working: bool = False) -> str:
    """Move his message's reaction once a post answers it, to the one its orders want now, as the watch's pass would, in the same call, so a post closing his order with something handled leaves the check at once, never a bare message for a pass: construction while the post says work it started keeps running or an order's work on it runs, a check once its order is done, else none, so the construction a pickup put on it never outlives its work, as one stayed on his status question at C0CJ378LSBT/1794857634.488009 after its answer; the step it left."""
    orders, running = state.reaction_thread(name, channel, ts)
    wants = [want for order_id, order in orders.items() if (want := wanted(order, order_id in running)) is not False]
    step = BUILDING if working else min(wants, key=RANK.index) if wants else None
    path = state.drive_dir(name) / FILE
    kept = json.loads(path.read_text()) if path.exists() else {}
    react(channel, ts, step, kept.get(f"{channel}/{ts}", True))
    path.write_text(json.dumps(kept | {f"{channel}/{ts}": step}))
    return step or "no reaction"
