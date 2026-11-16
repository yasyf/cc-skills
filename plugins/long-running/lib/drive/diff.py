"""A pull request's diff as the dashboard's diff block draws it: each changed file with its hunks, line by line, and each review comment on it placed under the line it names, a bot's and his own told apart."""

import functools
import json
import re
import threading

from drive import github

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$")
KEPT = 64
FILES_SHOWN = 300


def parse(patch: str) -> list[dict]:
    """Each hunk of a file's unified patch, its header's section words and its lines, each line's kind and its number on the old and new side."""
    hunks: list[dict] = []
    old = new = 0
    for text in patch.splitlines():
        if found := HUNK.match(text):
            old, new = int(found[1]), int(found[2])
            hunks.append({"section": found[3].strip(), "old": old, "new": new, "lines": []})
        elif not hunks or text.startswith("\\"):
            continue
        elif text.startswith("+"):
            hunks[-1]["lines"].append({"kind": "add", "old": None, "new": new, "text": text[1:]})
            new += 1
        elif text.startswith("-"):
            hunks[-1]["lines"].append({"kind": "del", "old": old, "new": None, "text": text[1:]})
            old += 1
        else:
            hunks[-1]["lines"].append({"kind": "ctx", "old": old, "new": new, "text": text[1:]})
            old += 1
            new += 1
    return hunks


@functools.cache
def viewer() -> str:
    """The login of the GitHub account this Mac's gh reads as, his own."""
    return str(github.read("the signed-in GitHub account", ["gh", "api", "user", "--jq", ".login"], max_age="24h")).strip()


def comment(said: dict, his: str) -> dict:
    user = said.get("user") or {}
    login = user.get("login") or github.DELETED_AUTHOR
    return {
        "path": said["path"],
        "line": said.get("line"),
        "side": said.get("side") or "RIGHT",
        "author": login,
        "bot": user.get("type") == "Bot" or login.endswith("[bot]"),
        "his": login == his,
        "body": said.get("body") or "",
        "url": said.get("html_url"),
        "reply_to": said.get("in_reply_to_id"),
        "id": said["id"],
    }


def read(key: str) -> dict:
    """The PR's changed files, at most FILES_SHOWN, each with its hunks, or why GitHub gave none for it, and every review comment on it, from reads up to a minute old."""
    repo, _, number = key.partition("#")
    pages = json.loads(github.read(f"the changed files of {key}", ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/files?per_page=100"], max_age="60s"))
    said = json.loads(github.read(f"the review comments on {key}", ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{number}/comments?per_page=100"], max_age="60s"))
    files = [changed for page in pages for changed in page]
    his = viewer()
    return {
        "files": [
            {
                "path": changed["filename"],
                "was": changed.get("previous_filename"),
                "status": changed["status"],
                "added": changed["additions"],
                "removed": changed["deletions"],
                "hunks": parse(changed["patch"]) if changed.get("patch") else [],
                "whole": bool(changed.get("patch")),
                "url": changed.get("blob_url"),
            }
            for changed in files[:FILES_SHOWN]
        ],
        "more": max(len(files) - FILES_SHOWN, 0),
        "comments": [comment(each, his) for page in said for each in page],
    }


class Reader:
    """Each PR's diff at the head it was read at, kept for the KEPT heads read last, so a card reopened at the same head costs no GitHub read."""

    def __init__(self) -> None:
        self.kept: dict[tuple[str, str], dict] = {}
        self.lock = threading.Lock()

    def at(self, key: str, head: str) -> dict:
        with self.lock:
            if (key, head) in self.kept:
                return self.kept[(key, head)]
        read_now = read(key)
        with self.lock:
            self.kept[(key, head)] = read_now
            while len(self.kept) > KEPT:
                del self.kept[next(iter(self.kept))]
        return read_now
