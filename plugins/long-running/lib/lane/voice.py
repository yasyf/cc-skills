"""A lane owner's say: hand the post to the cc-slack daemon's voice sender and return at once.

The owner never waits on Jev or Slack and never sees a refusal. The sender formats the post, has Jev move or cut sentences against the role page, and posts it in the lane's thread, end to end in under a second; its before and after land in cc-slack's voice.jsonl, which `cc-slack voice --last N` prints.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

LANE_ENV = "LONG_RUNNING_LANE"
VOICE_URL = "http://127.0.0.1:7378/voice"
ENQUEUE_SECONDS = 2
ROLE_PAGE = Path("skills") / "long-running" / "reference" / "voice.md"
PR_REF = re.compile(r"\{pr:(?:([\w.-]+/[\w.-]+)#)?#?(\d+)(?:\|([^{}]*))?\}")


def lane_file(drive: str, lane_id: str) -> Path:
    from drive import state

    return state.root() / "lanes" / drive / f"{lane_id}.json"


def own_lane() -> tuple[str, str] | None:
    """The drive and lane of the owner session calling say, from LONG_RUNNING_LANE as `<drive>/<lane>`; None for every other session."""
    if not (named := os.environ.get(LANE_ENV)):
        return None
    drive, _, lane_id = named.partition("/")
    return drive, lane_id


def say(plugin_root: Path, drive: str, lane_id: str, arguments: dict) -> str:
    """Queue one post in the lane's own channel and thread, read from its lane file, against the role page of the newest install, and return QUEUED and its id."""
    lane = json.loads(lane_file(drive, lane_id).read_text())
    body = {
        "lane": lane_id,
        "channel_id": lane["channel"],
        "thread_ts": arguments.get("thread_ts") or lane.get("thread_ts") or "",
        "text": linked(arguments["text"], lane.get("prs") or []),
        "rules": str(plugin_root / ROLE_PAGE),
        "asked": arguments.get("asked") or "",
    }
    request = urllib.request.Request(VOICE_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getppid())})
    with urllib.request.urlopen(request, timeout=ENQUEUE_SECONDS) as answer:
        return f"QUEUED {json.load(answer)['id']}"


def linked(text: str, prs: list[dict]) -> str:
    """The text with each {pr:N} or {pr:owner/repo#N|words} the lane's prs rows hold drawn as `<url|#N words>`; one the lane does not hold stays as typed, which the sender drops as a dead link."""

    def drawn(match: re.Match[str]) -> str:
        repo, number, words = match[1], int(match[2]), (match[3] or "").strip()
        held = [row for row in prs if row["number"] == number and (repo is None or row["repo"].casefold() == repo.casefold())]
        if len(held) != 1:
            return match[0]
        label = " ".join(filter(None, [f"#{number}", words]))
        return f"<https://github.com/{held[0]['repo']}/pull/{number}|{label}>"

    return PR_REF.sub(drawn, text)
