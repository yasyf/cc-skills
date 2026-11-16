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
RUNNING = "running"
RANK = (BUILDING, None, DONE)
MOVES_PER_PASS = 20


def wanted(data: dict, order_id: str) -> str | None | bool:
    """The reaction his order's message carries now: construction while a job of the order runs, a check once it is done, none once it was dropped or answered with nothing running; False while no answer has gone out yet and nothing runs, so the eyes a session put on it stay."""
    order = data["orders"][order_id]
    if order["state"] in state.CLOSED_ORDER_STATES:
        return DONE if order["state"] == "done" else None
    if any(job["order"] == order_id and job["state"] == RUNNING for job in data["jobs"].values()):
        return BUILDING
    return None if order["last_line"] else False


def moved(name: str, moment: datetime) -> list[str]:
    """Move the reaction on each message of his an open or recently closed order answers to the one its orders want, construction while any runs work, else none while any is open, else a check, once each time that changes, removing every other step's, the newest messages first and at most MOVES_PER_PASS a pass so a first sweep never floods Slack; a line for each it moved."""
    data = state.load(name, ("orders", "jobs"))
    path = state.drive_dir(name) / FILE
    kept = json.loads(path.read_text()) if path.exists() else {}
    steps: dict[str, list[str | None]] = {}
    for order_id, order in data["orders"].items():
        if not order.get("thread") or order["state"] in state.CLOSED_ORDER_STATES and moment - state.parse_time(order["updated"]) > RECENT:
            continue
        if (step := wanted(data, order_id)) is not False:
            steps.setdefault("/".join(order["thread"]), []).append(step)
    lines = []
    for key, wants in sorted(steps.items(), key=lambda item: item[0].split("/")[1], reverse=True):
        step = min(wants, key=RANK.index)
        if kept.get(key, "unset") == step:
            continue
        if len(lines) == MOVES_PER_PASS:
            break
        react(*key.split("/"), step)
        kept[key] = step
        lines.append(f"his message {key} now carries {step or 'no reaction'}")
    path.write_text(json.dumps(kept))
    return lines


def react(channel: str, ts: str, step: str | None) -> None:
    """Leave the one reaction step on his message, taking every other step's away; a reaction already gone or already there is no failure."""
    for other in STEPS:
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
