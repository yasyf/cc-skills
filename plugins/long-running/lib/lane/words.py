"""A PR's few words, the one label Slack and the dashboard both draw beside its #N."""

import re

LEAD_EMOJI = re.compile(r"^(?:[^\w\s(\[\x27\x22`]+\s*|:[a-z0-9_+-]+:\s*)+")
KIND = re.compile(r"^(?:[\w-]+(?:\([^)]*\))?!?:\s*)+")
CLAUSE = re.compile(r"[,;:](?=\s)| - | — ")
TITLE_WORDS = 8


def short(title: str) -> str:
    """A PR title as a link's few words: its leading emoji, such as a gitmoji ✨, and its kind prefix, such as `Fix:` or `feat(cc-slack):`, dropped, cut at its first clause, at most TITLE_WORDS words."""
    words = CLAUSE.split(KIND.sub("", LEAD_EMOJI.sub("", title)).strip(), maxsplit=1)[0].split()
    return " ".join(words[:TITLE_WORDS]) + ("…" if len(words) > TITLE_WORDS else "")
