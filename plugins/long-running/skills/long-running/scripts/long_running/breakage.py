from __future__ import annotations

import re

CUSTOMERS = re.compile(r"\b(?:tenants?|customers?|SoFi|AIG|HSBC|GSK|(?:tenant|their|customer|end|real) users|users (?:on|of|in) (?:the |those |these |three |\d+ )?(?:tenant|customer|cluster))\b", re.IGNORECASE)
HARM = re.compile(
    r"\b(?:breaks|breaking|broke|broken|crash(?:es|ed|ing)?|crash-?loop\w*|fail(?:s|ed|ing)? (?:for|to (?:open|load|start|send|sign))|cannot (?:open|load|send|start|sign)|can'?t (?:open|load|send|start)|unavailable|outage|down for|HTTP [45]\d\d|[45]\d\ds)\b"
    r"|\b(?:sees?|shows?)\s+(?:an?\s+|its\s+|the\s+)?['\"‘“]",
    re.IGNORECASE,
)
MOOT = re.compile(
    r"\?$|\b(?:would|could|might|if|unless|whether|incidents?|fix(?:ed)? (?:is |went |are )?live|working again|no longer|recovered)\b"
    r"|\b(?:no|not|nothing|zero|0)\s+(?:\S+\s+){0,2}?(?:customers?|tenants?|SoFi|AIG|HSBC|users?|failures?|crash\w*|errors?|breaks?)\b"
    r"|\b(?:crash\w*|break\w*|failures?|errors?|outage)\s+(?:is|are|was|were)\s+not\b",
    re.IGNORECASE,
)
INCIDENT_LANE = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-(?:fix|evidence)(?:-\d+|-fix|-opus)*\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def customer_break(text: str) -> str | None:
    """The first sentence of `text` that says customers are failing now or will fail from a change already moving, else None: a conditional, a negation, a question, a recovery, or a sentence about an incident says no new break, and text naming an incident lane is that incident's."""
    if INCIDENT_LANE.search(text):
        return None
    return next((said for part in SENTENCE.split(text) if CUSTOMERS.search(said := part.strip()) and HARM.search(said) and not MOOT.search(said)), None)
