from __future__ import annotations

import re

LIMITS = {"headline": 25, "stuck": 18, "unblock": 16, "how": 12, "node": 10, "title": 8, "latest": 16, "story": 30, "sentence": 40, "line": 20}
COINED = ("lane", "desk", "watchdog", "finder", "slice", "census", "fidelity", "register", "mirror", "forest", "tap row", "ci-grade", "rider", "carry", "old world", "new world", "new tree", "old tree")
GIT_VERBS = ("restack", "rebase", "enqueue", "cherry-pick")
VAGUE_FIX = re.compile(r"\b(?:names?|answers?|address(?:es)?|fix(?:es)?|clears?|resolves?|settles?|handles?|accepts?)(?: or [a-z]+)? (?:the|its|a|each|every)(?: [a-z]+){0,2} (?:hold|blocker|findings?|review|feedback|comments?|failing tests|failures?)\b(?!['’]s| (?:that|about|on|in|where|by)\b)", re.I)
CHECKS = (
    ("a release or plan code", re.compile(r"\b(?:R\d{1,2}|P\d{1,2}[a-z]?(?:\.\d+)?|[Ss]tep \d+[a-z]?)\b")),
    ("a test or item code", re.compile(r"\b(?!S3\b)[A-Z]\d{1,3}\b")),
    ("an id", re.compile(r"\b(?:team|org|user|run|wf|txid)[_:-](?:[0-9a-hjkmnp-tv-z]{26}\b|(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,})|\btxid\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b[0-9A-HJKMNP-TV-Z]{26}\b")),
    ("a record number", re.compile(r"#1\d{5}\b|\brecords? \d{3,}|\bseq\b", re.I)),
    ("a hash", re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")),
    ("a pull request number", re.compile(r"#\d{4,5}\b")),
    ("code", re.compile(r"`|\bhttps?://|(?:^|\s)(?:~|\.{0,2})/[\w.-]+/|\b[\w-]+/[\w-]+\.(?:py|go|ts|tsx|js|rs|md|json|sh)\b|\b\w+\.(?:py|go|ts|tsx|rs)\b")),
    ("a UTC or ISO time", re.compile(r"\bUTC\b|\d{4}-\d{2}-\d{2}T\d{2}|\b\d{1,2}:\d{2}Z\b")),
    ("a raw duration", re.compile(r"\b\d{2,} h \d+ min\b")),
    ("a truncation", re.compile(r"…|\.\.\.$")),
    ("an unnamed part", re.compile(r"\b(?:first|second|third|other) part\b|\bthe first's\b", re.I)),
    ("a code identifier", re.compile(r"\b[a-z]+[A-Z][A-Za-z]*\b|\bsandsql-(?!server\b)[a-z-]+|\bcc-[a-z]+")),
    ("a build or runtime mechanism", re.compile(r"\b(?:crates?|corpus|wasm|OPFS|typecheck|go vet|pods?|executor|devhost|re-?mint\w*|query[ -]plan|spill read|footers?|stacked change|parent copy|harness|compaction|protocol|token component|store pin|stand-in|sleep loops?|wait group|inbox rows?|admission)\b", re.I)),
    ("a patch size", re.compile(r"\b\d[\d,]* (?:changed )?lines\b", re.I)),
    ("a declined ask", re.compile(r"\b(?:asks?|requests?|suggestions?) (?:was |were )?declined\b", re.I)),
    ("a counted finding", re.compile(r"\b(?:first|second|third|fourth|fifth|one|two|three|four|five|six|\d+)\s+(?:\w+\s+)?(?:findings?|points?)\b|\bfound\s+(?:one|two|three|four|five|six|several|\d+)\s+(?:\w+\s+)?(?:problems?|issues?|bugs?|things)\b", re.I)),
)


def violations(text: str, slot: str = "line", lanes: tuple[str, ...] = ()) -> list[str]:
    found = [name for name, pattern in CHECKS if pattern.search(text)]
    lowered = text.lower()
    found += [f"the coined word {word!r}" for word in COINED if re.search(rf"\b{re.escape(word).replace(chr(92) + ' ', '[- ]')}s?\b", lowered)]
    found += [f"the git word {word!r}" for word in GIT_VERBS if re.search(rf"\b{re.escape(word)}", lowered)]
    found += [f"the worker name {lane!r}" for lane in lanes if lane and lane.lower() in lowered]
    if slot == "how" and VAGUE_FIX.search(text):
        found.append("a fix that does not say what it fixes")
    words = len(text.split())
    if words > LIMITS[slot]:
        found.append(f"{words} words where the limit is {LIMITS[slot]}")
    return found


def walk(value, slot_of, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, slot_of, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, slot_of, (*path, index))
    elif isinstance(value, str) and (slot := slot_of(path)):
        yield path, value, slot


def check(document, slot_of, lanes: tuple[str, ...] = ()) -> list[str]:
    return [f"{'.'.join(map(str, path))}: {text!r} has {', '.join(problems)}" for path, text, slot in walk(document, slot_of) if (problems := violations(text, slot, lanes))]
