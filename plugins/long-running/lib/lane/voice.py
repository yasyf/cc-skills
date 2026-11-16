"""A lane owner's say: lane.say writes the promise, and this sender hands the post to the cc-slack daemon's voice sender and returns at once.

The owner never waits on Jev or Slack. The daemon formats the post, has Jev move or cut sentences against the role page, and posts it in the lane's channel and thread, end to end in under a second; its before and after land in cc-slack's voice.jsonl, which `cc-slack voice --last N` prints.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

from lane import say, store, verbs

LANE_ENV = "LONG_RUNNING_LANE"
TOKEN_ENV = "LANE_TOKEN"
VOICE_URL = "http://127.0.0.1:7378/voice"
ENQUEUE_SECONDS = 2
ROLE_PAGE = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference" / "voice.md"
PR_REF = re.compile(r"\{pr:(?:([\w.-]+/[\w.-]+)#)?#?(\d+)(?:\|([^{}]*))?\}")


def own_lane() -> tuple[str, str] | None:
    """The drive and lane of the owner session calling say, from LONG_RUNNING_LANE as `<drive>/<lane>`; None for every other session."""
    if not (named := os.environ.get(LANE_ENV)):
        return None
    drive, _, lane_id = named.partition("/")
    return drive, lane_id


def owner_say(drive: str, lane_id: str, arguments: dict) -> str:
    """The MCP say of a lane's owner: lane.say with its promise, and when the promise's shape is refused, the post alone, since the owner never sees a post refused."""
    token = os.environ.get(TOKEN_ENV) or None
    try:
        return say.say(drive, lane_id, arguments["text"], arguments.get("next"), arguments.get("by"), token, arguments.get("answers"))
    except store.Refused as refused:
        if not (arguments.get("next") or arguments.get("by")):
            raise
        return f"{say.say(drive, lane_id, arguments['text'], token=token, answers=arguments.get('answers'))}; its promise was not kept: {refused}"


def send(drive: str, lane: dict, text: str) -> str:
    """lane.say's SENDER: queue one post in the lane's channel and thread with the role page beside it, and return QUEUED and its id."""
    body = {
        "lane": lane["id"],
        "channel_id": lane["channel"],
        "thread_ts": lane.get("thread") or "",
        "text": linked(text, lane.get("prs") or []),
        "rules": str(ROLE_PAGE),
        "asked": "",
    }
    request = urllib.request.Request(VOICE_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getppid())})
    with urllib.request.urlopen(request, timeout=ENQUEUE_SECONDS) as answer:
        return f"QUEUED {json.load(answer)['id']}"


def linked(text: str, prs: list[dict]) -> str:
    """The text with each {pr:N} or {pr:owner/repo#N|words} the lane's prs rows hold drawn as `<url|#N words>`, the row's own words when the post gives none; one the lane does not hold stays as typed, which the sender drops as a dead link."""

    def drawn(match: re.Match[str]) -> str:
        repo, number = match[1], int(match[2])
        held = [row for row in prs if row["number"] == number and (repo is None or row["repo"].casefold() == repo.casefold())]
        if len(held) != 1:
            return match[0]
        words = (match[3] or held[0].get("words") or "").strip()
        return f"<https://github.com/{held[0]['repo']}/pull/{number}|{' '.join(filter(None, [f'#{number}', words]))}>"

    return PR_REF.sub(drawn, text)


say.SENDER = send


def heard(channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    """His message or tap in a lane's channel, as cc-slack's heard_command pushes it, appended to each lane it is for, which wakes its owner: in a thread, the lane whose thread it is; at the channel's root, the lane holding the channel with no thread; and when no lane or several lanes fit, every lane in the channel, so it reaches at least one and never none. Nothing for a channel no lane holds."""
    found = [(drive.name, lane_id, lane) for drive in sorted(store.root().iterdir()) if drive.is_dir() for lane_id, lane in store.load_all(drive.name).items() if lane["channel"] == channel] if store.root().is_dir() else []
    fitting = [each for each in found if (each[2].get("thread") or None) == (thread or None)]
    chosen = fitting if len(fitting) == 1 else found
    if not chosen:
        return f"no lane holds {channel}"
    return "; ".join(f"{lane_id}: {verbs.message(drive, lane_id, words, link, ts)} new" for drive, lane_id, _ in chosen)
