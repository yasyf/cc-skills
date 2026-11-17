"""A PR's few words, the one label Slack and the dashboard both draw beside its #N."""

import json
import os
import re
import subprocess

from lane import store

LEAD_EMOJI = re.compile(r"^(?::[a-z0-9_+-]+:\s*|[^\w\s(\[\x27\x22`]+\s*)+")
KIND = re.compile(r"^(?:[\w-]+(?:\([^)]*\))?!?:\s*)+")
MARKUP = re.compile(r"^[#>*\-\s]+|[*_`]")
GENERIC = {"cleanup", "clean up", "fix", "fixes", "refactor", "wip", "update", "updates", "chore", "misc", "tweak", "tweaks", "changes", "new"}
BODY_WORDS_FILE = "pr-title-words.json"
SLACK_LINK = re.compile(r"<[^<>|]*\|([^<>]*)>")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def label(number: int, words: str) -> str:
    """A PR link's label, `#N words`, safe inside Slack's `<url|label>`: a Slack or markdown link in the words reads as its own text, a leading `#N` they repeat is said once, a title's kind prefix such as `New:` is dropped as `short` drops it, and `&`, `<` and `>` are escaped, so words such as `prove <box>` or `[#56570](url)` never break the link or show its markup."""
    plain = MARKDOWN_LINK.sub(r"\1", SLACK_LINK.sub(r"\1", words)).strip().removeprefix(f"#{number}").strip()
    plain = short(plain) or plain
    return " ".join(filter(None, [f"#{number}", plain.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")]))


def short(title: str) -> str:
    """A PR title as its link's words, whole: only its leading emoji, a gitmoji ✨ or its `:recycle:` shortcode, and its kind prefix, such as `Fix:` or `feat(cc-slack):`, dropped, and nothing cut, since a label is the words the owner or Jev chose and never one code shortened (his 6:57 PM ruling: model judgement, no hard-coded rules); "" when what is left is one generic word, such as Cleanup or WIP, that says nothing for him."""
    words = LEAD_EMOJI.sub("", KIND.sub("", LEAD_EMOJI.sub("", title))).strip()
    return "" if words.strip(".!").lower() in GENERIC else words


def pr_words(repo: str, number: int, title: str) -> str:
    """A PR's few words from its title, or, when its title says nothing (one generic word), from its body's first line that is no heading, whole, read once and kept in the lanes' pr-words file."""
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
