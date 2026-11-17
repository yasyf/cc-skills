"""His words that no lane holds, in his DM or in a channel no lane has as its home, reach one intake session started in the call that hears them: it answers him, hands his words to the lane they are about, or opens a lane, by intake.md, the old communicator's turn for one message. Its reply goes through the voice like any lane's post."""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from lane import session, store, supervisor, voice

INTAKES = "intake"
PAGE = Path(__file__).with_name("intake.md")
COMMUNICATOR = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference" / "communicator.md"
WORKSPACE = re.compile(r"https://([\w-]+\.slack\.com)/")
LANE = "intake"


def folder(drive: str) -> Path:
    return store.drive_dir(drive) / INTAKES


def workspace(drive: str, link: str | None) -> str:
    """The Slack workspace host his links name: his message's, else any lane ask's."""
    links = [link or "", *(ask.get("link") or "" for lane in store.load_all(drive).values() for ask in lane.get("asks", []))]
    return next((found[1] for each in links if (found := WORKSPACE.match(each))), "slack.com")


def prompt(drive: str, channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    config = store.config(drive)
    return PAGE.read_text().format(
        drive=drive,
        channel=channel,
        ts=ts,
        thread_note=f", in the thread of {thread}" if thread else "",
        reply_thread=thread or ts,
        words=words.replace("\n", "\n> "),
        link=link or "none",
        lanes="\n".join(supervisor.check(drive)[1]),
        lanes_dir=store.drive_dir(drive),
        lane_bin=store.launcher(drive),
        checkout=config.get("checkout") or "the drive config names none",
        active=config["active"],
        workspace=workspace(drive, link),
        communicator=COMMUNICATOR,
        voice=voice.ROLE_PAGE,
    )


def start(drive: str, channel: str, thread: str | None, words: str, link: str | None, ts: str) -> str:
    """Start one intake session for his message, detached so the call that heard it returns at once; once per message, so a second hearing of it starts none."""
    folder(drive).mkdir(parents=True, exist_ok=True)
    log = folder(drive) / f"{ts}.log"
    try:
        out = log.open("x")
    except FileExistsError:
        return f"intake of {ts}: already started"
    with out:
        subprocess.Popen(session.argv(store.config(drive), "intake", store.drive_dir(drive), {}, ["-p"], ["--permission-mode", "bypassPermissions", "--", prompt(drive, channel, thread, words, link, ts)]), cwd=store.drive_dir(drive), stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return f"intake of {ts}: started"


def reply(drive: str, channel: str, thread: str, text: str, options: list[str] | None = None) -> str:
    """The intake's one reply in his thread, through the voice with the role page beside it; a refusal comes back with its reason."""
    body = {"lane": LANE, "drive": drive, "channel_id": channel, "thread_ts": thread, "text": text, "words": text, "prs": [], "about": [], "rules": str(voice.ROLE_PAGE), "asked": "", **({"options": options} if options else {})}
    request = urllib.request.Request(voice.VOICE_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())})
    try:
        with urllib.request.urlopen(request, timeout=voice.ENQUEUE_SECONDS) as answer:
            queued = json.load(answer)
    except urllib.error.HTTPError as refused:
        if refused.code == 422:
            raise store.Refused(refused.read().decode().strip()) from refused
        raise
    return f"QUEUED {queued['id']}" + (f"; {queued['warning']}" if queued.get("warning") else "")
