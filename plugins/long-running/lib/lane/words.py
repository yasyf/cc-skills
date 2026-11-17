"""A PR's few words, the one label Slack and the dashboard both draw beside its #N."""

import json
import os
import re
import subprocess

from lane import store

LEAD_EMOJI = re.compile(r"^(?::[a-z0-9_+-]+:\s*|[^\w\s(\[\x27\x22`]+\s*)+")
KIND = re.compile(r"^(?:[\w-]+(?:\([^)]*\))?!?:\s*)+")
CLAUSE = re.compile(r"[,;:](?=\s)| - | — ")
MARKUP = re.compile(r"^[#>*\-\s]+|[*_`]")
TITLE_WORDS = 8
GENERIC = {"cleanup", "clean up", "fix", "fixes", "refactor", "wip", "update", "updates", "chore", "misc", "tweak", "tweaks", "changes", "new"}
BODY_WORDS_FILE = "pr-words.json"


def short(title: str) -> str:
    """A PR title as a link's few words: its leading emoji, a gitmoji ✨ or its `:recycle:` shortcode, and its kind prefix, such as `Fix:` or `feat(cc-slack):`, dropped, cut at its first clause, at most TITLE_WORDS words; "" when what is left is one generic word, such as Cleanup or WIP, that says nothing for him."""
    words = CLAUSE.split(LEAD_EMOJI.sub("", KIND.sub("", LEAD_EMOJI.sub("", title))).strip(), maxsplit=1)[0].split()
    if " ".join(words).strip(".!").lower() in GENERIC:
        return ""
    return " ".join(words[:TITLE_WORDS]) + ("…" if len(words) > TITLE_WORDS else "")


def pr_words(repo: str, number: int, title: str) -> str:
    """A PR's few words from its title, or, when its title says nothing (one generic word), from its body's first line that is no heading, read once and kept in the lanes' pr-words file."""
    if words := short(title):
        return words
    path = store.root() / BODY_WORDS_FILE
    kept = json.loads(path.read_text()) if path.is_file() else {}
    key = f"{repo}#{number}"
    if key not in kept:
        done = subprocess.run(["gh", "pr", "view", str(number), "-R", repo, "--json", "body", "-q", ".body"], capture_output=True, text=True, check=False, timeout=30)
        first = next((MARKUP.sub("", line).strip() for line in done.stdout.splitlines() if not line.lstrip().startswith("#") and MARKUP.sub("", line).strip()), "") if not done.returncode else ""
        kept[key] = short(first)
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fresh.write_text(json.dumps(kept))
        fresh.replace(path)
    return kept[key]
