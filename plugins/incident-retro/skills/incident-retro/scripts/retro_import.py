#!/usr/bin/env python3
"""Google Docs import for the incident-retro skill.

  retro_import.py import-gdoc <exported.md> [<docs.json>] --out <dir> [--tz ZONE] [--date YYYY-MM-DD]
  retro_import.py --selftest

import-gdoc reads the text/markdown export of a Google Docs postmortem (and
the Docs API JSON beside it when given) and writes a draft retro.json, the
document's images under evidence/images/, and an "Import report" section in
NOTES.md that names every heading and its destination, every block it could
not place, every link and its class, every time conversion and every kind
guess. --out may be a scaffolded retro directory or a fresh one. Handles and
plain twins are left empty for the Draft pass. --selftest converts the
fixture document and diffs the result against fixtures/expected/. Stdlib
only.
"""
import argparse, base64, datetime, difflib, json, re, shutil, sys, tempfile, zoneinfo
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = SKILL / "fixtures"
DEFAULT_TZ = "America/Los_Angeles"
SUBTITLE = "Incident retrospective"
CANONICAL = ("This file is the canonical retro. Snapshots of the evidence live under evidence/; prose that does not "
             "fit structure lives in NOTES.md; incident-retro.html renders from this file.")
REPORT_HEADING = "## Import report"
IMAGE_DIR = Path("evidence") / "images"
EVIDENCE_DIRS = ("datadog", "slack", "images")
EVIDENCE_KEYS = ("notebooks", "monitors", "slack", "sentry", "linear", "builds", "prs", "runs", "images", "docs")
LESSON_BUCKETS = ("well", "wrong", "lucky")
PR_ROLE_RANK = {"cause": 0, "fix": 1, "monitor": 2, "followup": 3}
SECTION_PR_ROLE = {"root": "cause", "contributing": "cause", "trigger": "cause", "resolution": "fix", "actions": "fix",
                   "detection": "monitor"}
LABEL_PR_ROLE = (("cause", r"^(root )?cause|^trigger|^culprit|^offending"), ("fix", r"^fix|^revert|^hotfix|^resolution"),
                 ("monitor", r"^monitor|^alert"))
HEADINGS = (
    ("summary", ("summary", "tldr", "tl dr", "overview", "what happened", "executive summary", "description")),
    ("timeline", ("timeline", "incident timeline", "sequence of events", "chronology")),
    ("impact", ("impact", "customer impact", "blast radius", "user impact")),
    ("root", ("root cause", "root causes", "confirmed root cause", "causes", "cause", "the bug", "root cause analysis", "rca")),
    ("contributing", ("contributing factors", "contributing factor", "contributing causes", "contributing cause")),
    ("trigger", ("trigger", "triggers", "what triggered it")),
    ("resolution", ("resolution", "remediation", "recovery", "fix", "the fix", "mitigation")),
    ("detection", ("detection", "how we found out", "how it was detected", "how we detected it")),
    ("actions", ("action items", "actions", "follow ups", "follow up", "next steps", "remediation items")),
    ("lessons", ("lessons learned", "lessons", "learnings", "takeaways")),
    ("well", ("what went well", "went well", "what worked")),
    ("wrong", ("what went wrong", "went wrong", "what didnt go well", "what did not go well", "what could have gone better")),
    ("lucky", ("where we got lucky", "lucky", "where we were lucky")),
    ("evidence", ("supporting information", "supporting info", "references", "links", "appendix", "evidence", "resources", "artifacts")),
)
HEADING_DEST = {name: dest for dest, names in HEADINGS for name in names}
DEST_LABEL = {"summary": "summary.text", "timeline": "timeline[] and windows[]", "impact": "impact.text",
              "root": "causes[] kind root", "contributing": "causes[] kind contributing", "trigger": "causes[] kind trigger",
              "resolution": "resolution.text", "detection": "detection.text and detection.monitors[]",
              "actions": "actions[]", "lessons": "lessons.* by bold lead-in", "well": "lessons.well", "wrong": "lessons.wrong",
              "lucky": "lessons.lucky", "evidence": "evidence.* by host, leftovers to notes[]", "notes": "notes[]"}
CAUSE_KIND = {"root": "root", "contributing": "contributing", "trigger": "trigger"}
HEADING = re.compile(r"^(#{1,6})\s*(.*?)\s*$")
FENCE = re.compile(r"^```\s*(\w*)\s*$")
IMAGE_DEF = re.compile(r"^\[(image\d+)\]:\s*<?(data:[^>\s]+)>?\s*$")
IMAGE_DEF_WRAPPED = re.compile(r"^\[(image\d+)\]:\s*<(data:[^>]*)$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
CHECKBOX = re.compile(r"^\[([ xX])\]\s*")
TABLE_RULE = re.compile(r"^[\s|:\-]+$")
CELL_SPLIT = re.compile(r"(?<!\\)\|")
ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>&~|])")
MD_LINK = re.compile(r"\[([^\]]*)\]\(((?:https?:|mailto:)[^)\s]*)\)")
MAILTO_LINK = re.compile(r"\[([^\]]+)\]\(mailto:[^)]*\)")
BARE_URL = re.compile(r"(?<![\w(\[])https?://[^\s<>()\[\]]+")
IMAGE_REF = re.compile(r"!\[([^\]]*)\]\[(image\d+)\]")
EMOJI_ALT = re.compile(r"^:[\w+\-]+(?:::[\w+\-]+)*:$")
STRIKE = re.compile(r"~~(.+?)~~")
BOLD = re.compile(r"\*\*(.+?)\*\*")
BOLD_LEAD = re.compile(r"^\*\*([^*]+?):?\*\*:?\s*(.*)$")
BRACKET_TAG = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
INCIDENT_TAG = re.compile(r"^\[(\d+)\]\s*(.*)$", re.S)
HEADER_FIELD = re.compile(r"(Date|Authors?|Status|Attendees|Severity|Incident ID / Severity|Note):", re.I)
TITLE_DATE = re.compile(r"^\s*\[?(\d{4}-\d{2}-\d{2})\]?\s*[-:–—]?\s*")
TITLE_INCIDENT = re.compile(r"\s*\(?(?:#|incident\s*#?\s*)(\d+)?\)?\s*$", re.I)
TITLE_RCA = re.compile(r"\s+RCA\s*$")
INCIDENT_NUMBER = re.compile(r"(?:\(#|\(incident\s*#?\s*|(?<![\w-])#|\bINC-)(\d+)\)?", re.I)
SEVERITY = re.compile(r"\bsev-?\s?(\d)\b", re.I)
STATUS_WORDS = (("resolved", r"resolved|closed"), ("reviewed", r"\breviewed\b"), ("in-review", r"in review|under review"),
                ("draft", r"in progress|investigating|draft|open|triage|monitoring"))
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
DATE_MON = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?\b", re.I)
DATE_US = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/:])")
DAY_PREFIX = re.compile(r"^(\d{1,2})(?:st|nd|rd|th),?\s+")
TIME = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2})(?::(\d{2}))?)?\s*(?:(a\.?m\.?|p\.?m\.?)(?![a-z]))?\s*"
                  r"(Z(?![a-z])|UTC\b|GMT\b|PDT\b|PST\b|PT\b|MDT\b|MST\b|CDT\b|CST\b|EDT\b|EST\b|ET\b)?", re.I)
TIME_LEAD = re.compile(r"^\**\s*~?\s*")
ZONES = {"z": "UTC", "utc": "UTC", "gmt": "UTC", "pst": "UTC-08:00", "pdt": "UTC-07:00", "mst": "UTC-07:00",
         "mdt": "UTC-06:00", "cst": "UTC-06:00", "cdt": "UTC-05:00", "est": "UTC-05:00", "edt": "UTC-04:00",
         "pt": "America/Los_Angeles", "et": "America/New_York"}
HINT_ZONES = {"utc": "UTC", "pacific": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pst": "America/Los_Angeles",
              "eastern": "America/New_York", "edt": "America/New_York", "est": "America/New_York"}
UTC_OFFSET = re.compile(r"^UTC([+-])(\d{2}):(\d{2})$")
ZONE_HINT = re.compile(r"\b(all times|times are|times in)\b.*?\b(pacific|eastern|utc|pdt|pst|edt|est)\b", re.I)
WINDOW_ZONE = r"(?:Z|UTC|GMT|PDT|PST|PT|MDT|MST|CDT|CST|EDT|EST|ET)?"
WINDOW_PAIR = re.compile(r"(?:\b(?:from|between)\s+)?(?P<a>\d{1,2}(?::\d{2})?(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\s*" + WINDOW_ZONE +
                         r")\s*(?:to|and|until|till|[-–—])\s*(?P<b>\d{1,2}(?::\d{2})?(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\s*" +
                         WINDOW_ZONE + r")(?![\d:])", re.I)
WINDOW_WORDS = re.compile(r"outage|window|between|\bfrom\b|\bdown\b|degrad|unavailable|impact|start|end", re.I)
KIND_RULES = (
    ("allclear", r"all[- ]?clear"),
    ("resolution", r"\bresolved\b|\brecover(s|ed|y)?\b|\bfull health\b|\bhealthy again\b|\bback to normal\b|\bstops?\b|\bstopped\b"),
    ("mitigation", r"roll(ed|ing)? ?back|rollback|revert|disabl|scal(ed|ing) (up|down|out)|\bpinn?ed\b|hotfix|restart|mitigat|freez|froze|unblock"),
    ("deploy", r"deploy|releas|rolled onto|rolls onto|\bbuild \d+\b|\bmerged\b|shipped"),
    ("alert", r"\balert|monitor .{0,40}(fire|alert|trigger)|\bpaged?\b|\bpage\b|escalat|\bfire[sd]?\b"),
    ("report", r"report|noticed|notices|customer|\bflag(s|ged)?\b|observ|complain|support ticket"),
    ("hypothesis", r"suspect|hypothes|likely|might|could be|theor|guess"),
    ("action", r"\back(nowledg\w*)?\b|approv|\bopened\b|investigat|identif|confirm|begins?|began|asks?|posts?|labell?ed|\bnotes\b|checking|working|picks up|kicks? off|kicked off"),
)
ACTOR = re.compile(r"^\**([A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+)?|[a-z][a-z0-9_.]{1,15})(?:'s)?\**[:,]?\s+"
                   r"(?:reports?|reported|confirms?|confirmed|asks?|asked|flags?|flagged|posts?|posted|notes?|noted|begins?|began|"
                   r"identifi(?:es|ed)|acknowledg(?:es|ed)|approv(?:es|ed)|states?|stated|picks up|picked up|noticed|notices|says|said|"
                   r"shared|applied|opened|proposed|calls?|called|declares?|declared|kicks? off|kicked off|joins?|joined|suspects?|"
                   r"suspected|pings?|pinged|escalat(?:es|ed)|investigat(?:es|ed)|verifi(?:es|ed)|rules? out|ruled out|deploys?|"
                   r"deployed|restarts?|restarted|announce[sd]?|realiz(?:es|ed)|spots?|spotted|restarting|is checking|:)\b")
ACTOR_BY = re.compile(r"\b(?:acknowledged|accepted|reported|shared|approved|opened|restarted|deployed) by ([A-Za-z][\w.'-]*)")
NOT_ACTORS = {"Incident", "Investigation", "Escalation", "Rollback", "Deploy", "Deployment", "Deployments", "Build", "Monitor",
              "Alert", "Team", "The", "Production", "Sentry", "Datadog", "Slack", "Node", "Full", "Final", "Some", "Status",
              "Update", "Custom", "Emergency", "Pods", "Runs", "Queue", "Checkout", "Service", "Customer", "Customers", "Retro",
              "Earliest", "First", "Last", "Initial", "rollback", "revert", "deploy", "build", "monitor", "alert", "api", "runs",
              "queue", "incident", "pods", "customer", "team", "on-call"}
STATE_RULES = (("dropped", r"dropped|won'?t (do|fix)|wontfix|cancell?ed|obsolete"),
               ("in-progress", r"in progress|in-progress|\bwip\b|ongoing|in review|\braised\b|started"),
               ("done", r"✅|:white_check_mark:|:heavy_check_mark:|:check_mark:|\bdone\b|\bcompleted?\b|\bmerged\b|\bcreated\b|\bshipped\b|\bresolved\b|\bfixed\b"))
OWNER_CHILD = re.compile(r"^(?:\*\*)?owner(?:\*\*)?:\s*@?(.+?)\s*$", re.I)
DUE_CHILD = re.compile(r"^(?:\*\*)?(?:deadline|due)(?:\*\*)?:\s*(.+?)\s*$", re.I)
OWNER_AT = re.compile(r"(?<!\w)@([A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+)?|[a-z][a-z0-9_.-]{1,15})")
OWNER_FIELD = re.compile(r"\bowner:\s*@?([A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+)?)", re.I)
OWNER_TRAIL_PAREN = re.compile(r"\s*\(([A-Z][a-z]+(?: [A-Z][a-z]+)?)\)\s*$")
OWNER_TRAIL_BRACKET = re.compile(r"\s*\[([A-Z][a-z]+(?: [A-Z][a-z]+)?)\]\s*$")
PRIORITY_TAG = re.compile(r"^\[?p\d\]?\s*", re.I)
TABLE_ITEM_HEADERS = ("item", "action", "task", "action item", "what", "description")
TABLE_STATE_HEADERS = ("state", "status", "progress")
TABLE_OWNER_HEADERS = ("owner", "who", "assignee", "dri")
TABLE_TIME_HEADERS = ("time", "when", "timestamp", "ts")
TABLE_EVENT_HEADERS = ("event", "what", "description", "note", "notes", "details")
GITHUB_PR = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
GITHUB_OTHER = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/(issues|commit)/(\w+)")
GRAPHITE_PR = re.compile(r"^https://app\.graphite\.com/github/pr/([\w.-]+)/([\w.-]+)/(\d+)")
DD_NOTEBOOK = re.compile(r"^https://app\.datadoghq\.com/notebook/(\d+)")
DD_MONITOR = re.compile(r"^https://app\.datadoghq\.com/monitors/(\d+)")
SLACK_PERMALINK = re.compile(r"^https://[\w-]+\.slack\.com/archives/[A-Z0-9]+/p\d+")
SENTRY = re.compile(r"^https://[\w.-]+\.sentry\.io/")
LINEAR = re.compile(r"^https://linear\.app/[\w-]+/issue/([A-Z][A-Z0-9]*-\d+)")
BUILDKITE = re.compile(r"^https://buildkite\.com/")
DATADOG = re.compile(r"^https://app\.datadoghq\.com/")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MOJIBAKE = {"‚Äî": "—", "‚Äì": "–", "‚Äô": "’", "‚Äò": "‘", "‚Äú": "“", "‚Äù": "”", "‚Ä¶": "…", "¬†": " "}
FIRST_SENTENCE_WORDS = 24


def repair_mojibake(text: str) -> tuple:
    hits = [k for k in MOJIBAKE if k in text]
    for k in hits:
        text = text.replace(k, MOJIBAKE[k])
    return text, hits


def unescape(s: str) -> str:
    return ESCAPE.sub(r"\1", s)


def strip_mailto(s: str) -> str:
    return re.sub(r"\[([^\]]+)\]\(mailto:[^)]*\)(?=[\w\[])", r"\1 ", MAILTO_LINK.sub(r"\1", s)) if "mailto:" in s else s


def mailto_names(s: str) -> list:
    names = []
    for name in MAILTO_LINK.findall(s):
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return names


def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-") or "retro"


def normalise_heading(text: str) -> tuple:
    text = unescape(text)
    parenthetical = " ".join(re.findall(r"\(([^)]*)\)", text)).strip().lower()
    bare = re.sub(r"\([^)]*\)", " ", text).replace("&", " and ")
    norm = re.sub(r"[^a-z0-9]+", " ", bare.lower()).strip()
    return norm, parenthetical


def parse_table(rows: list) -> list:
    out = []
    for row in rows:
        cells = [c.strip() for c in CELL_SPLIT.split(row.strip())]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if TABLE_RULE.match(row) and all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        out.append([c.replace("\\|", "|") for c in cells])
    return out


def nest_items(flat: list) -> list:
    top, stack = [], []
    for item in flat:
        while stack and stack[-1]["indent"] >= item["indent"]:
            stack.pop()
        (stack[-1]["children"] if stack else top).append(item)
        stack.append(item)
    return top


def parse_blocks(text: str) -> list:
    lines = text.replace("\r\n", "\n").split("\n")
    blocks, i = [], 0

    def next_nonblank(j):
        while j < len(lines) and not lines[j].strip():
            j += 1
        return j

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = HEADING.match(line)
        if m:
            blocks.append({"kind": "heading", "level": len(m[1]), "text": m[2], "line": i + 1})
            i += 1
            continue
        m = FENCE.match(line)
        if m:
            body, j = [], i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                body.append(lines[j])
                j += 1
            blocks.append({"kind": "fence", "lang": m[1], "source": "\n".join(body), "line": i + 1})
            i = j + 1
            continue
        m = IMAGE_DEF_WRAPPED.match(line)
        if m:
            parts, j = [m[2].strip()], i + 1
            while j < len(lines) and ">" not in lines[j] and lines[j].strip():
                parts.append(lines[j].strip())
                j += 1
            if j < len(lines) and ">" in lines[j]:
                parts.append(lines[j].partition(">")[0].strip())
                j += 1
            blocks.append({"kind": "imagedef", "name": m[1], "data": "".join(parts), "line": i + 1})
            i = j
            continue
        m = IMAGE_DEF.match(line)
        if m:
            blocks.append({"kind": "imagedef", "name": m[1], "data": m[2], "line": i + 1})
            i += 1
            continue
        if line.lstrip().startswith("|"):
            rows, j = [], i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append(lines[j])
                j += 1
            blocks.append({"kind": "table", "rows": parse_table(rows), "line": i + 1})
            i = j
            continue
        if line.startswith(">"):
            q, j = [], i
            while j < len(lines) and lines[j].startswith(">"):
                q.append(lines[j].rstrip())
                j += 1
            blocks.append({"kind": "quote", "md": "\n".join(q), "line": i + 1})
            i = j
            continue
        if LIST_ITEM.match(line):
            items, j = [], i
            while j < len(lines):
                if not lines[j].strip():
                    k = next_nonblank(j)
                    if k < len(lines) and LIST_ITEM.match(lines[k]) and len(LIST_ITEM.match(lines[k])[1].expandtabs(4)) >= 0:
                        j = k
                        continue
                    break
                mm = LIST_ITEM.match(lines[j])
                if mm:
                    text_ = mm[3].rstrip()
                    cb = CHECKBOX.match(text_)
                    items.append({"indent": len(mm[1].expandtabs(4)), "ordered": mm[2][0].isdigit(),
                                  "checked": None if not cb else cb[1] != " ", "text": text_[cb.end():] if cb else text_,
                                  "children": [], "line": j + 1})
                elif items and lines[j].startswith((" ", "\t")) and not HEADING.match(lines[j]):
                    items[-1]["text"] += "\n" + lines[j].strip()
                else:
                    break
                j += 1
            blocks.append({"kind": "list", "items": nest_items(items), "line": i + 1})
            i = j
            continue
        para, j = [], i
        while j < len(lines) and lines[j].strip() and not HEADING.match(lines[j]) and not FENCE.match(lines[j]) \
                and not lines[j].lstrip().startswith("|") and not LIST_ITEM.match(lines[j]) and not IMAGE_DEF.match(lines[j]) \
                and not IMAGE_DEF_WRAPPED.match(lines[j]) \
                and not lines[j].startswith(">"):
            para.append(lines[j].rstrip())
            j += 1
        blocks.append({"kind": "para", "lines": para, "line": i + 1})
        i = j
    return blocks


def items_flat(items: list, depth: int = 0):
    for item in items:
        yield depth, item
        yield from items_flat(item["children"], depth + 1)


def list_md(items: list, depth: int = 0) -> str:
    out = []
    for n, item in enumerate(items, 1):
        marker = f"{n}." if item["ordered"] else "-"
        box = "" if item["checked"] is None else ("[x] " if item["checked"] else "[ ] ")
        out.append("  " * depth + f"{marker} {box}{clean_inline(item['text'])}")
        if item["children"]:
            out.append(list_md(item["children"], depth + 1))
    return "\n".join(out)


def table_md(rows: list) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(clean_inline(c) for c in padded[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(clean_inline(c) for c in r) + " |" for r in padded[1:]]
    return "\n".join(lines)


def block_md(block: dict) -> str:
    kind = block["kind"]
    if kind == "para":
        return "\n".join(clean_inline(l) for l in block["lines"])
    if kind == "list":
        return list_md(block["items"])
    if kind == "table":
        return table_md(block["rows"])
    if kind == "fence":
        return f"```{block['lang']}\n{block['source']}\n```"
    if kind == "quote":
        return "\n".join(clean_inline(l) for l in block["md"].split("\n"))
    if kind == "heading":
        return "#" * block["level"] + " " + unescape(block["text"])
    return ""


def block_verbatim(block: dict) -> str:
    kind = block["kind"]
    if kind == "para":
        return "\n".join(block["lines"])
    if kind == "list":
        return "\n".join("  " * d + ("- " if item["checked"] is None else ("- [x] " if item["checked"] else "- [ ] ")) + item["text"]
                         for d, item in items_flat(block["items"]))
    if kind == "table":
        return "\n".join("| " + " | ".join(r) + " |" for r in block["rows"])
    if kind == "fence":
        return f"```{block['lang']}\n{block['source']}\n```"
    if kind == "quote":
        return block["md"]
    return json.dumps(block)


def clean_inline(s: str) -> str:
    s = IMAGE_REF.sub(lambda m: m[1] if EMOJI_ALT.match(m[1] or "") else "", strip_mailto(unescape(s)))
    return re.sub(r"[ \t]+", " ", s).strip()


def plain(s: str) -> str:
    s = clean_inline(s)
    s = MD_LINK.sub(lambda m: m[1] if m[1] and m[1] != m[2] else m[2], s)
    s = STRIKE.sub(r"\1", s)
    s = BOLD.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def without_links(s: str) -> str:
    s = MD_LINK.sub("", clean_inline(s))
    s = BARE_URL.sub("", s)
    s = STRIKE.sub(r"\1", s)
    s = BOLD.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip(" \t:-–—,;")


def links_in(s: str) -> list:
    s = unescape(s)
    found = []
    for text, url in MD_LINK.findall(s):
        if url.startswith("mailto:"):
            continue
        found.append((text.strip(), url.rstrip(".,;")))
    rest = MD_LINK.sub(" ", s)
    for url in BARE_URL.findall(rest):
        url = url.rstrip(".,;:\\")
        if url not in [u for _, u in found]:
            found.append(("", url))
    return found


def first_sentence(s: str) -> str:
    s = plain(s)
    sentence = SENTENCE_END.split(s, 1)[0].rstrip(".")
    words = sentence.split()
    return " ".join(words[:FIRST_SENTENCE_WORDS]) if len(words) > FIRST_SENTENCE_WORDS else sentence


def capitalise(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def parse_date(s: str, default_year=None):
    m = DATE_ISO.search(s)
    if m:
        return datetime.date(int(m[1]), int(m[2]), int(m[3]))
    m = DATE_MON.search(s)
    if m:
        year = int(m[3]) if m[3] else default_year
        if year:
            return datetime.date(year, MONTHS.index(m[1][:3].lower()) + 1, int(m[2]))
    m = DATE_US.search(s)
    if m:
        year = int(m[3]) if m[3] else default_year
        if year and year < 100:
            year += 2000
        if year and 1 <= int(m[1]) <= 12 and 1 <= int(m[2]) <= 31:
            return datetime.date(year, int(m[1]), int(m[2]))
    return None


def zone_of(name: str) -> datetime.tzinfo:
    m = UTC_OFFSET.match(name)
    if not m:
        return zoneinfo.ZoneInfo(name)
    offset = datetime.timedelta(hours=int(m[2]), minutes=int(m[3]))
    return datetime.timezone(-offset if m[1] == "-" else offset, name)


def parse_time(s: str):
    m = TIME.match(TIME_LEAD.sub("", s.strip()))
    if not m or (m[2] is None and m[4] is None):
        return None
    hour, minute, second = int(m[1]), int(m[2] or 0), int(m[3] or 0)
    ampm = (m[4] or "").replace(".", "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        return None
    zone = ZONES.get((m[5] or "").lower()) if m[5] else None
    return {"time": datetime.time(hour, minute, second), "explicit": bool(ampm), "zone": zone, "end": m.end(),
            "source": s.strip()[TIME_LEAD.match(s.strip()).end():TIME_LEAD.match(s.strip()).end() + m.end()].strip()}


class Report:
    def __init__(self, source: str, tz: str):
        self.source, self.tz = source, tz
        self.header, self.headings, self.times, self.windows, self.stamps = [], [], [], [], []
        self.actions, self.links, self.images, self.unplaced, self.notes = [], [], [], [], []

    def unplace(self, where: str, block: dict, why: str = ""):
        self.unplaced.append((where, block_verbatim(block), why))

    def markdown(self, doc_meta: dict) -> str:
        out = [REPORT_HEADING, ""]
        src = f"Imported from `{self.source}`"
        if doc_meta:
            src += (f" with the Docs API export (title `{doc_meta.get('title', '')}`, document `{doc_meta.get('documentId', '')}`,"
                    f" revision `{doc_meta.get('revisionId', '')}`)")
        out += [src + f", default zone `{self.tz}`.", ""]
        out += ["### Header", ""] + [f"- {line}" for line in self.header] + [
            f"- Every `h` handle and `p` plain twin is empty: the Draft pass writes them.",
            f"- Every time without an explicit zone was read in `{self.tz}`; confirm the zone before publishing.", ""]
        out += ["### Headings", "", "| # | heading | destination |", "| --- | --- | --- |"]
        out += [f"| {n} | `{'#' * lvl} {text}` | {dest} |" for n, (lvl, text, dest) in enumerate(self.headings, 1)]
        out += ["", "### Timeline", ""]
        if self.times:
            out += ["| source | parsed | zone | kind | actor |", "| --- | --- | --- | --- | --- |"]
            out += [f"| {s} | {p} | {z} | {k} | {a} |" for s, p, z, k, a in self.times]
        else:
            out.append("No timeline entries were parsed.")
        out += ["", "### Windows", ""]
        out += [f"- {w}" for w in self.windows] or ["No outage windows were found."]
        out += ["", "### Timestamps", ""]
        out += [f"- {s}" for s in self.stamps] or ["No timestamps were inferred."]
        out += ["", "### Actions", ""]
        if self.actions:
            out += ["| id | item | state | owner | links |", "| --- | --- | --- | --- | --- |"]
            out += [f"| {i} | {t} | {s} | {o} | {l} |" for i, t, s, o, l in self.actions]
        else:
            out.append("No action items were parsed.")
        out += ["", "### Links", ""]
        if self.links:
            out += ["| url | class | where | note |", "| --- | --- | --- | --- |"]
            out += [f"| <{u}> | {c} | {w} | {n} |" for u, c, w, n in self.links]
        else:
            out.append("No links were found.")
        out += ["", "### Images", ""]
        out += [f"- {i}" for i in self.images] or ["No images were found."]
        out += ["", "### Notes", ""]
        out += [f"- {n}" for n in self.notes] or ["Nothing was moved to notes[]."]
        out += ["", "### Unplaced", ""]
        if self.unplaced:
            out.append("Blocks the converter could not place, verbatim:")
            for where, text, why in self.unplaced:
                out += ["", f"**{where}**" + (f" ({why})" if why else "") + ":", ""]
                out += ["> " + l for l in text.split("\n")]
        else:
            out.append("Every block was placed.")
        out += ["", "### Finish by hand", "",
                "1. Write an `h` handle (2 to 5 words) on every cause, action, window and sub-incident.",
                "2. Write a `p` plain twin for summary, impact, every cause, resolution and detection.",
                "3. Confirm the timezone of every converted time above, and the inferred timestamps.",
                "4. Retitle every cause whose `t` is its first sentence, and check every kind and actor guess.",
                "5. Replace customer names with deployment codenames in prose, and fill `impact.teams` and `meta.teams`.",
                "6. Run `retro.py evidence fetch` for the notebooks and monitors, and `evidence slack new` for each Slack permalink.",
                "7. Run `retro.py check` and clear what it reports.", ""]
        return "\n".join(out)


class Importer:
    def __init__(self, md_text: str, doc_json: dict | None, source_name: str, tz: str, date_arg: str | None):
        self.tz, self.zone = tz, zoneinfo.ZoneInfo(tz)
        self.doc_json = doc_json or {}
        self.date_arg = datetime.date.fromisoformat(date_arg) if date_arg else None
        self.report = Report(source_name, tz)
        md_text, mojibake = repair_mojibake(md_text)
        if mojibake:
            self.report.header.append("Repaired double-encoded punctuation in the export: " + ", ".join(f"`{k}` to `{MOJIBAKE[k]}`" for k in mojibake))
        self.blocks = parse_blocks(md_text)
        self.image_defs = {b["name"]: b["data"] for b in self.blocks if b["kind"] == "imagedef"}
        self.images = {}
        self.retro = {
            "meta": {}, "summary": {"text": "", "p": ""},
            "timestamps": {"onset": None, "detected": None, "engaged": None, "mitigated": None, "resolved": None, "allClear": None},
            "windows": [], "timeline": [], "impact": {"text": "", "p": "", "teams": [], "metrics": []},
            "causes": [], "resolution": {"text": "", "p": "", "links": []}, "detection": {"text": "", "p": "", "monitors": []},
            "actions": [], "lessons": {"well": [], "wrong": [], "lucky": []},
            "evidence": {k: [] for k in EVIDENCE_KEYS}, "notes": [], "components": {}, "housekeeping": []}
        self.pr_roles = {}
        self.link_seen = set()
        self.sub_incidents = []
        self.timeline_rows = []
        self.running_date = None
        self.header_date = None
        self.current_heading = ""

    def run(self) -> dict:
        sections, preamble, h1 = self.split_sections()
        header_blocks = h1["blocks"] if h1 else preamble
        self.read_header(h1["text"] if h1 else None, header_blocks)
        dests = [s["dest"] for s in sections]
        if not h1 and preamble and "summary" not in dests:
            self.read_summary({"heading": "(preamble)", "blocks": preamble, "parenthetical": ""})
        for s in sections:
            self.report.headings.append((s["level"], unescape(s["text"]) or "(empty)", s["dest_label"]))
        for s in sections:
            self.current_heading = s["heading"]
            getattr(self, "read_" + s["dest"])(s)
        self.finish_timeline()
        self.finish_meta()
        self.retro["evidence"]["images"].sort(key=lambda i: i["file"])
        return self.retro

    def split_sections(self):
        sections, preamble, h1, current = [], [], None, None
        for b in self.blocks:
            if b["kind"] == "imagedef":
                continue
            if b["kind"] == "heading":
                if b["level"] == 1 and h1 is None and not sections:
                    h1 = {"text": b["text"], "blocks": []}
                    current = h1
                    continue
                norm, parenthetical = normalise_heading(b["text"])
                if not norm:
                    dest, label = "skip", "skipped (empty heading)"
                else:
                    dest = HEADING_DEST.get(norm, "notes")
                    label = DEST_LABEL[dest]
                current = {"text": b["text"], "level": b["level"], "norm": norm, "parenthetical": parenthetical,
                           "dest": dest, "dest_label": label, "blocks": [], "heading": unescape(b["text"])}
                sections.append(current)
            elif current is None:
                preamble.append(b)
            else:
                current["blocks"].append(b)
        return sections, preamble, h1

    def read_skip(self, s):
        for b in s["blocks"]:
            self.report.unplace("Empty heading", b, "content under a heading with no text")

    def read_header(self, h1_text, blocks):
        meta = self.retro["meta"]
        title = unescape(h1_text) if h1_text else self.doc_json.get("title", "")
        title_source = "the H1" if h1_text else ("the Docs API title" if title else "nowhere")
        title_date = TITLE_DATE.match(title)
        stripped = []
        if title_date:
            title = title[title_date.end():]
            stripped.append(f"`[{title_date[1]}]`")
        number = None
        m = TITLE_INCIDENT.search(title)
        if m and (m[1] or "#" in m[0]):
            if m[1]:
                number = int(m[1])
            stripped.append(f"`{m[0].strip()}`")
            title = title[:m.start()]
        if TITLE_RCA.search(title):
            stripped.append("`RCA`")
            title = TITLE_RCA.sub("", title)
        title = title.strip(" -:–—")
        meta["title"] = title
        self.report.header.append(f"Title: {title} (from {title_source}" + (", stripped " + ", ".join(stripped) if stripped else "") + ")")
        text = "\n".join(l for b in blocks if b["kind"] == "para" for l in b["lines"])
        raw = unescape(text)
        fields = {}
        for m in re.finditer(r"(Date|Authors?|Status|Attendees|Severity|Incident ID / Severity|Note)\s*:\s*\**\s*(.*?)(?=\s+\**(?:Date|Authors?|Status|Attendees|Severity|Incident ID / Severity|Note)\s*\**:|\n|$)",
                             raw.replace("**", ""), re.S):
            fields.setdefault(m[1].lower().rstrip("s") if m[1].lower() in ("authors", "author") else m[1].lower(), []).append(m[2].strip())
        header_status = None
        status_why = []
        for value in fields.get("status", []):
            found = next((name for name, rx in STATUS_WORDS if re.search(rx, value, re.I)), None)
            status_why.append(f"`{value}`")
            if found:
                header_status = found
        meta["status"] = header_status or "draft"
        self.report.header.append(f"Status: {meta['status']}" + (" (from " + ", then ".join(status_why) + ("; the last wins" if len(status_why) > 1 else "") + ")" if status_why else " (default: no `Status:` field)"))
        date, date_why = None, ""
        for value in fields.get("date", []):
            date = parse_date(value)
            if date:
                date_why = f"the `Date:` field `{value}`"
                break
            date_why = f"the `Date:` field read `{value}`"
        if not date and title_date:
            date = datetime.date.fromisoformat(title_date[1])
            date_why = f"the title bracket; the `Date:` field read `{fields['date'][0]}`" if fields.get("date") else "the title bracket"
        if not date and self.date_arg:
            date = self.date_arg
            date_why = "`--date`" + (f"; the `Date:` field read `{fields['date'][0]}`" if fields.get("date") else "")
        self.header_date = date
        self.date_why = date_why
        authors = mailto_names(" ".join(fields.get("author", [])))
        author_why = "`mailto:` link texts in the `Authors:` field"
        if not authors:
            for value in fields.get("author", []):
                names = [n.strip() for n in re.split(r",|\band\b|/", plain(value)) if n.strip() and n.strip().lower() != "person"]
                authors += [n for n in names if n not in authors]
            author_why = "the `Authors:` field"
        if not authors:
            authors = self.json_people()
            author_why = "the Docs API `person` mentions"
        meta["authors"] = authors
        self.report.header.append(f"Authors: {', '.join(authors) or '(none)'}" + (f" (from {author_why})" if authors else " (no `mailto:` links, `Authors:` names or Docs API mentions)"))
        attendee_text = "\n".join(fields.get("attendees", []))
        attendees_block = next((b for b in blocks if b["kind"] == "para" and any(l.strip().lower().startswith("attendees") for l in b["lines"])), None)
        attendees = mailto_names("\n".join(attendees_block["lines"])) if attendees_block else []
        if not attendees and attendee_text:
            attendees = [n.strip() for n in re.split(r",|\band\b", plain(attendee_text)) if n.strip()]
        if attendees:
            meta["attendees"] = attendees
            self.report.header.append(f"Attendees: {', '.join(attendees)}")
        incident = {}
        if number is None:
            m = INCIDENT_NUMBER.search(raw)
            if m:
                number = int(m[1])
        if number is not None:
            incident["number"] = number
        sev = SEVERITY.search(raw) or SEVERITY.search(" ".join(u for _, u in links_in(text)))
        if sev:
            incident["severity"] = f"sev-{sev[1]}"
            sev_link = next((u for _, u in links_in(text) if SEVERITY.search(u)), None)
            if sev_link:
                incident["severityLink"] = sev_link
        if incident:
            meta["incident"] = incident
        self.report.header.append("Incident: " + (f"number {number}" if number is not None else "no number") +
                                  (f", severity {incident['severity']}" if "severity" in incident else ", no severity") +
                                  (" (severity from a header link)" if "severityLink" in incident else ""))
        for note in fields.get("note", []):
            self.report.header.append(f"Header note: {note}")
        for text_, url in links_in(text):
            self.register_link(url, text_, "header", None)
        for b in blocks:
            if b["kind"] == "para":
                for l in b["lines"]:
                    if not HEADER_FIELD.search(unescape(l)) and not MAILTO_LINK.fullmatch(unescape(l).strip()) \
                            and not re.fullmatch(r"(?:\[[^\]]+\]\(mailto:[^)]*\)\s*)+", unescape(l).strip()) \
                            and not links_in(l) and l.strip():
                        self.report.unplace("Header", {"kind": "para", "lines": [l]})
            elif b["kind"] != "imagedef":
                self.report.unplace("Header", b)

    def json_people(self) -> list:
        names = []

        def walk(node):
            if isinstance(node, dict):
                person = node.get("person")
                if isinstance(person, dict):
                    name = (person.get("personProperties") or {}).get("name")
                    if name and name not in names:
                        names.append(name)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(self.doc_json)
        return names

    def register_link(self, url: str, text: str, where: str, section_dest, label_hint: str = ""):
        url = unescape(url)
        ev = self.retro["evidence"]
        if not url.startswith("https://"):
            if (url, where) not in self.link_seen:
                self.link_seen.add((url, where))
                self.report.links.append((url, "skipped", where, "not https"))
            return None
        label = plain(text) if text and text != url and not re.fullmatch(r"#?\d+", plain(text)) else ""
        label = label or plain(label_hint)
        m = GRAPHITE_PR.match(url)
        alt = None
        if m:
            alt, url = url, f"https://github.com/{m[1]}/{m[2]}/pull/{m[3]}"
        m = GITHUB_PR.match(url)
        if m:
            canon = m[0]
            role = SECTION_PR_ROLE.get(section_dest, "followup")
            for r, rx in LABEL_PR_ROLE:
                if re.search(rx, label, re.I):
                    role = r
            entry = self.pr_roles.get(canon)
            if entry is None:
                entry = {"url": canon, "role": role}
                if alt:
                    entry["alt"] = alt
                self.pr_roles[canon] = entry
                ev["prs"].append(entry)
                note = f"role {role}"
            else:
                if PR_ROLE_RANK[role] < PR_ROLE_RANK[entry["role"]]:
                    note = f"role {entry['role']} → {role}"
                    entry["role"] = role
                else:
                    note = f"role {entry['role']} kept (also seen as {role})" if role != entry["role"] else f"role {role}, seen again"
                if alt and "alt" not in entry:
                    entry["alt"] = alt
            if alt:
                note += f", rewritten from Graphite"
            self.report.links.append((alt or canon, "prs", where, note))
            return {"url": canon, "kind": "pr", "class": "pr", "label": label}
        key = (url, where)
        m = DD_NOTEBOOK.match(url)
        if m:
            cls, item = "notebooks", {"id": int(m[1]), "url": url}
        elif DD_MONITOR.match(url):
            cls, item = "monitors", {"id": int(DD_MONITOR.match(url)[1]), "url": url}
        elif SLACK_PERMALINK.match(url):
            cls, item = "slack", {"url": url}
        elif SENTRY.match(url):
            cls, item = "sentry", {"url": url, "label": label}
        elif LINEAR.match(url):
            cls, item = "linear", {"url": url, "key": LINEAR.match(url)[1], "label": label}
        elif BUILDKITE.match(url):
            cls, item = "builds", {"url": url, "label": label}
        else:
            cls, item = "docs", {"url": url, "label": label}
        ident = item.get("id", url)
        existing = next((e for e in ev[cls] if e.get("id", e.get("url")) == ident), None)
        if existing is None:
            if item.get("label") == "" or cls in ("notebooks", "monitors", "slack"):
                item.pop("label", None)
            ev[cls].append(item)
            note = ""
        else:
            if label and not existing.get("label") and cls not in ("notebooks", "monitors", "slack"):
                existing["label"] = label
            note = "seen again"
        if key not in self.link_seen:
            self.link_seen.add(key)
            notes = [n for n in (note, f"label `{label}`" if label and cls in ("sentry", "linear", "builds", "docs") else "") if n]
            self.report.links.append((url, cls, where, ", ".join(notes)))
        out = {"url": url, "kind": "doc", "class": cls, "label": label}
        if cls == "linear":
            out["label"] = item["key"]
        return out

    def link_entry(self, info: dict) -> dict:
        entry = {"url": info["url"], "kind": info["kind"]}
        if info.get("label"):
            entry["label"] = info["label"]
        return entry

    def register_links(self, text: str, where: str, section_dest) -> list:
        return [info for info in (self.register_link(url, t, where, section_dest) for t, url in links_in(text)) if info]

    def take_images(self, text: str, context: str, where: str) -> str:
        for alt, name in IMAGE_REF.findall(text):
            if EMOJI_ALT.match(alt or ""):
                if name not in self.images:
                    self.images[name] = None
                    self.report.images.append(f"`![{alt}][{name}]` is an emoji, kept as text and not extracted ({where})")
                continue
            if name in self.images:
                continue
            data = self.image_defs.get(name)
            if data is None:
                self.images[name] = None
                self.report.images.append(f"`![{alt}][{name}]` has no `[{name}]:` definition ({where})")
                continue
            m = re.match(r"data:image/(\w+);base64,(.*)$", data, re.S)
            if not m:
                self.images[name] = None
                self.report.images.append(f"`[{name}]` is not a base64 image data URI ({where})")
                continue
            try:
                payload = base64.b64decode(m[2], validate=True)
            except ValueError as e:
                self.images[name] = None
                self.report.images.append(f"`[{name}]` does not decode as base64 ({where}): {e}")
                continue
            ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(m[1], m[1])
            file = f"{IMAGE_DIR.as_posix()}/image-{name.removeprefix('image')}.{ext}"
            alt_text = plain(alt) if alt else without_links(IMAGE_REF.sub("", context)) or self.current_heading
            alt_why = "its own alt" if alt else ("the surrounding text" if without_links(IMAGE_REF.sub("", context)) else "the section heading")
            self.images[name] = {"file": file, "bytes": payload}
            self.retro["evidence"]["images"].append({"file": file, "alt": alt_text, "caption": "", "cites": []})
            self.report.images.append(f"`{file}` from `[{name}]`, alt from {alt_why}: {alt_text} ({where})")
        return IMAGE_REF.sub(lambda m: m[1] if EMOJI_ALT.match(m[1] or "") else "", text)

    def prose(self, s: dict, where: str, fences_to_notes: bool = True) -> str:
        parts = []
        for b in s["blocks"]:
            if b["kind"] == "fence" and fences_to_notes:
                self.add_note(f"{s['heading']} (code)", block_md(b), f"code fence under `{s['heading']}`")
                continue
            if b["kind"] == "para":
                b = {"kind": "para", "lines": [self.take_images(l, l, where) for l in b["lines"]]}
            elif b["kind"] == "list":
                for _, item in items_flat(b["items"]):
                    item["text"] = self.take_images(item["text"], item["text"], where)
            elif b["kind"] == "table":
                b = {"kind": "table", "rows": [[self.take_images(c, c, where) for c in r] for r in b["rows"]]}
            md = block_md(b)
            if md.strip():
                parts.append(md)
        text = "\n\n".join(parts)
        if self.sub_incidents:
            text = re.sub(r"(?m)^\[(\d+)\]\s*", lambda m: f"(I{m[1]}) " if int(m[1]) <= len(self.sub_incidents) else m[0], text)
        return text

    def add_note(self, title: str, md: str, why: str):
        if not md.strip():
            return
        existing = next((n for n in self.retro["notes"] if n["t"] == title), None)
        if existing:
            existing["md"] += "\n\n" + md
        else:
            self.retro["notes"].append({"t": title, "md": md})
            self.report.notes.append(f"`{title}`: {why}")

    def read_summary(self, s):
        multi = "multiple" in s.get("parenthetical", "")
        lists = [b for b in s["blocks"] if b["kind"] == "list"]
        if multi and lists and not self.sub_incidents:
            for _, item in items_flat(lists[0]["items"]):
                text = plain(item["text"])
                m = BRACKET_TAG.match(text)
                t = f"{m[1]}: {m[2]}" if m and m[2] else text
                self.sub_incidents.append({"id": f"I{len(self.sub_incidents) + 1}", "t": t, "h": ""})
            self.retro["meta"]["subIncidents"] = self.sub_incidents
            self.report.header.append(f"Sub-incidents: {len(self.sub_incidents)} from the `{s['heading']}` list, ids I1..I{len(self.sub_incidents)}; `[N]` prefixes elsewhere became `(IN)` citations")
        text = self.prose(s, "summary")
        for b in s["blocks"]:
            for _, item in items_flat(b["items"]) if b["kind"] == "list" else []:
                self.register_links(item["text"], "summary", "summary")
            if b["kind"] in ("para", "quote"):
                self.register_links("\n".join(b["lines"]) if b["kind"] == "para" else b["md"], "summary", "summary")
        self.scan_windows(s, "summary")
        self.retro["summary"]["text"] = (self.retro["summary"]["text"] + "\n\n" + text).strip()

    def read_impact(self, s):
        text = self.prose(s, "impact")
        for b in s["blocks"]:
            self.register_links(block_verbatim(b), "impact", "impact")
        self.scan_windows(s, "impact")
        self.retro["impact"]["text"] = (self.retro["impact"]["text"] + "\n\n" + text).strip()

    def read_resolution(self, s):
        text = self.prose(s, "resolution")
        for b in s["blocks"]:
            for info in self.register_links(block_verbatim(b), "resolution", "resolution"):
                if info["kind"] in ("pr", "issue", "doc") and info["class"] in ("pr", "linear", "docs", "builds"):
                    entry = self.link_entry(info)
                    if entry not in self.retro["resolution"]["links"]:
                        self.retro["resolution"]["links"].append(entry)
        self.retro["resolution"]["text"] = (self.retro["resolution"]["text"] + "\n\n" + text).strip()

    def read_detection(self, s):
        text = self.prose(s, "detection")
        for b in s["blocks"]:
            for info in self.register_links(block_verbatim(b), "detection", "detection"):
                if info["class"] == "monitors":
                    mid = int(DD_MONITOR.match(info["url"])[1])
                    if not any(m["id"] == mid for m in self.retro["detection"]["monitors"]):
                        self.retro["detection"]["monitors"].append({"id": mid, "role": "caught"})
                        self.report.header.append(f"Monitor {mid} under Detection registered with role `caught`; change it if the monitor missed the incident")
        self.retro["detection"]["text"] = (self.retro["detection"]["text"] + "\n\n" + text).strip()

    def read_root(self, s):
        self.read_causes(s, "root")

    def read_contributing(self, s):
        self.read_causes(s, "contributing")

    def read_trigger(self, s):
        self.read_causes(s, "trigger")

    def read_causes(self, s, kind):
        causes = self.retro["causes"]
        current = None
        where = f"causes ({s['heading']})"

        def new_cause(t, text, incident=None):
            cause = {"id": f"C{len(causes) + 1}", "kind": kind, "t": capitalise(t.rstrip(".")), "h": "", "text": text, "p": "",
                     "evidence": [], "links": []}
            if incident:
                cause["incident"] = incident
            causes.append(cause)
            self.attach_links(cause, text, where)
            return cause

        for b in s["blocks"]:
            if b["kind"] == "fence":
                if current is not None and "code" not in current:
                    current["code"] = {"lang": b["lang"] or "text", "source": b["source"], "caption": ""}
                else:
                    self.add_note(f"{s['heading']} (code)", block_md(b), f"a second code fence under `{s['heading']}`" if current else f"code fence under `{s['heading']}` before any cause")
                continue
            if b["kind"] == "list":
                for _, item in items_flat(b["items"]):
                    raw = self.take_images(item["text"], item["text"], where)
                    m = BOLD_LEAD.match(clean_inline(raw))
                    if m and m[2]:
                        current = new_cause(plain(m[1]), capitalise(clean_inline(m[2])))
                    else:
                        current = new_cause(first_sentence(raw), clean_inline(raw))
                continue
            if b["kind"] == "para":
                lines = [self.take_images(l, l, where) for l in b["lines"]]
                tagged = [INCIDENT_TAG.match(unescape(l).strip()) for l in lines]
                if self.sub_incidents and all(tagged):
                    for m in tagged:
                        if m[2].strip():
                            current = new_cause(first_sentence(m[2]), clean_inline(m[2]), f"I{m[1]}")
                    continue
                md = block_md({"kind": "para", "lines": lines})
                if not md.strip():
                    continue
                if current is None or (current["kind"] != kind):
                    current = new_cause(first_sentence(md), md)
                else:
                    current["text"] += "\n\n" + md
                    self.attach_links(current, md, where)
                continue
            md = block_md(b)
            if current is None:
                current = new_cause(first_sentence(md), md)
            else:
                current["text"] += "\n\n" + md
                self.attach_links(current, md, where)
        if self.sub_incidents:
            for c in causes:
                c["text"] = re.sub(r"(?m)^\[(\d+)\]\s*", lambda m: f"(I{m[1]}) ", c["text"])

    def attach_links(self, cause: dict, text: str, where: str):
        for info in self.register_links(text, where, cause["kind"] if cause["kind"] in CAUSE_KIND else "root"):
            if info["class"] in ("notebooks", "monitors", "slack", "sentry"):
                if info["url"] not in cause["evidence"]:
                    cause["evidence"].append(info["url"])
            else:
                entry = self.link_entry(info)
                if entry not in cause["links"]:
                    cause["links"].append(entry)

    def read_actions(self, s):
        where = "actions"
        for b in s["blocks"]:
            if b["kind"] == "table":
                self.actions_table(b, where)
            elif b["kind"] == "list":
                for item in b["items"]:
                    self.action_item(item, where)
            elif b["kind"] == "para" and self.sub_incidents and all(INCIDENT_TAG.match(unescape(l).strip()) and not INCIDENT_TAG.match(unescape(l).strip())[2].strip() for l in b["lines"]):
                continue
            else:
                self.report.unplace("Action items", b, "not a table, checklist or list")

    def actions_table(self, b, where):
        rows = b["rows"]
        if not rows:
            return
        header = [plain(c).lower() for c in rows[0]]

        def col(names, default):
            return next((i for i, h in enumerate(header) if h in names), default)
        item_col = col(TABLE_ITEM_HEADERS, 0)
        state_col = col(TABLE_STATE_HEADERS, None)
        owner_col = col(TABLE_OWNER_HEADERS, None)
        for row in rows[1:]:
            cells = row + [""] * (len(header) - len(row))
            state_text = cells[state_col] if state_col is not None else ""
            owner_text = cells[owner_col] if owner_col is not None else ""
            extra = " ".join(c for i, c in enumerate(cells) if i not in (item_col, state_col, owner_col) and i < len(header))
            self.add_action(cells[item_col], None, state_text, owner_text, where, f"table row {rows.index(row)}", extra_links=" ".join(cells))

    def action_item(self, item, where):
        owner, due = None, None
        rest = []
        for child in item["children"]:
            text = clean_inline(child["text"])
            m = OWNER_CHILD.match(text)
            d = DUE_CHILD.match(text)
            if m:
                owner = m[1].lstrip("@")
            elif d:
                due = parse_date(d[1], (self.header_date or datetime.date.today()).year)
                if not due:
                    rest.append(f"deadline {d[1]}")
            elif child["checked"] is not None:
                self.action_item(child, where)
            else:
                rest.append(text)
        self.add_action(item["text"], item["checked"], "", owner or "", where, f"list line {item['line']}", due=due, tail=rest)

    def add_action(self, raw, checked, state_text, owner_text, where, origin, due=None, tail=(), extra_links=""):
        text = self.take_images(unescape(raw), raw, where)
        marks = re.search(r"✅|:white_check_mark:|:heavy_check_mark:|:check_mark:", text)
        text = re.sub(r"✅|:[\w+\-]+(?:::[\w+\-]+)*:", "", text)
        if not plain(text).strip() and not links_in(text) and not tail:
            return
        state, state_why = ("done", "`[x]`") if checked else ("todo", "unchecked" if checked is False else "default")
        if STRIKE.search(text):
            state, state_why = "done", "struck through"
        for name, rx in STATE_RULES:
            if re.search(rx, without_links(state_text) or "", re.I):
                state, state_why = name, f"state cell `{plain(state_text)}`"
                break
        else:
            if marks and state != "done":
                state, state_why = "done", f"`{marks[0]}` in the text"
        owner, owner_why = None, ""
        names = mailto_names(text) or mailto_names(owner_text) or mailto_names(state_text)
        if owner_text.strip() and not mailto_names(text):
            owner, owner_why = (mailto_names(owner_text) or [plain(owner_text)])[0], "owner cell"
        if names and not owner:
            owner, owner_why = names[0], "`mailto:` link"
        body = without_links(STRIKE.sub(r"\1", text))
        priority = PRIORITY_TAG.match(body)
        if priority:
            body = body[priority.end():].strip()
        if names and body.startswith(names[0]):
            body = capitalise(body[len(names[0]):].strip())
        if names and body.endswith(names[0]):
            body = body[:-len(names[0])].strip()
        if not owner:
            for rx, why in ((OWNER_FIELD, "`Owner:`"), (OWNER_AT, "`@name`"), (OWNER_TRAIL_PAREN, "trailing `(Name)`"), (OWNER_TRAIL_BRACKET, "trailing `[Name]`")):
                m = rx.search(body)
                if m:
                    owner, owner_why = m[1], why
                    body = (body[:m.start()] + body[m.end():]).strip()
                    break
        else:
            for rx in (OWNER_FIELD, OWNER_AT):
                m = rx.search(body)
                if m and m[1] == owner:
                    body = (body[:m.start()] + body[m.end():]).strip()
        if owner_text.strip() and owner is None:
            owner, owner_why = plain(owner_text), "owner cell"
        links, seen = [], set()
        for t, url in links_in(" ".join((text, state_text, owner_text, extra_links))):
            info = self.register_link(url, t, where, "actions") if url not in seen else None
            seen.add(url)
            if info:
                links.append(info)
        title = body
        for info in links:
            if info["class"] == "linear" and title.startswith(info["label"]):
                title = title[len(info["label"]):].strip(" -–—:")
        title = re.sub(r"\s+", " ", title).strip(" -–—:")
        if not title and links:
            info = links[0]
            m = LINEAR.match(info["url"])
            slug = info["url"].rstrip("/").rsplit("/", 1)[-1] if m else ""
            title = capitalise(slug.replace("-", " ")) if slug and not slug.isdigit() else (info["label"] or info["url"])
        title = capitalise(title)
        action = {"id": f"AI{len(self.retro['actions']) + 1}", "t": title, "h": "", "source": "review", "state": state}
        if owner:
            action["owner"] = owner
        action["links"] = [self.link_entry(i) for i in links if i["class"] in ("pr", "linear", "docs", "builds")]
        if due:
            action["due"] = due.isoformat()
        if tail:
            action["t"] = title + " (" + "; ".join(tail) + ")"
        self.retro["actions"].append(action)
        self.report.actions.append((action["id"], action["t"].replace("|", "\\|") + (f" (priority `{priority[0].strip()}` dropped)" if priority else ""), f"{state} ({state_why})",
                                    f"{owner} ({owner_why})" if owner else "none", ", ".join(l["url"] for l in action["links"]) or "none"))

    def read_lessons(self, s):
        self.lessons_into(s, None)

    def read_well(self, s):
        self.lessons_into(s, "well")

    def read_wrong(self, s):
        self.lessons_into(s, "wrong")

    def read_lucky(self, s):
        self.lessons_into(s, "lucky")

    def lessons_into(self, s, bucket):
        general = []
        for b in s["blocks"]:
            lines = []
            if b["kind"] == "list":
                lines = [item["text"] for _, item in items_flat(b["items"])]
            elif b["kind"] == "para":
                lines = b["lines"]
            else:
                self.report.unplace(f"Lessons ({s['heading']})", b, "not a list or paragraph")
                continue
            for line in lines:
                text = self.take_images(clean_inline(line), line, f"lessons ({s['heading']})")
                if not text or (self.sub_incidents and INCIDENT_TAG.fullmatch(text) and not INCIDENT_TAG.fullmatch(text)[2].strip()):
                    continue
                m = BOLD_LEAD.match(text)
                if m:
                    norm, _ = normalise_heading(m[1])
                    dest = HEADING_DEST.get(norm)
                    if dest in LESSON_BUCKETS:
                        bucket = dest
                        text = clean_inline(m[2])
                        if not text:
                            continue
                self.register_links(text, f"lessons ({s['heading']})", "lessons")
                if bucket:
                    self.retro["lessons"][bucket].append({"text": text})
                else:
                    general.append(text)
        if general:
            self.add_note(s["heading"], "\n".join(f"- {g}" for g in general), "lessons under no well/wrong/lucky heading or lead-in")

    def read_timeline(self, s):
        self.timeline_rows.append(s)

    def read_evidence(self, s):
        leftovers = []
        where = f"evidence ({s['heading']})"
        for b in s["blocks"]:
            if b["kind"] in ("list", "para"):
                lines = [item["text"] for _, item in items_flat(b["items"])] if b["kind"] == "list" else b["lines"]
                kept = []
                for line in lines:
                    text = self.take_images(unescape(line), line, where)
                    found = links_in(text)
                    label = without_links(text)
                    for t, url in found:
                        self.register_link(url, t, where, "evidence", label_hint=label if len(found) == 1 else "")
                    if found and not any(u.startswith("https://") for _, u in found) and clean_inline(text):
                        kept.append(clean_inline(text))
                    if not found and clean_inline(text):
                        kept.append(clean_inline(text))
                if kept:
                    leftovers.append("\n".join(kept) if b["kind"] == "para" else "\n".join(f"- {k}" for k in kept))
            else:
                self.register_links(block_verbatim(b), where, "evidence")
                leftovers.append(block_md(b))
        if leftovers:
            self.add_note(s["heading"], "\n\n".join(leftovers), "text under a supporting-information heading that is not a link")

    def read_notes(self, s):
        where = f"notes ({s['heading']})"
        parts = []
        for b in s["blocks"]:
            if b["kind"] == "para":
                b = {"kind": "para", "lines": [self.take_images(l, l, where) for l in b["lines"]]}
            self.register_links(block_verbatim(b), where, None)
            parts.append(block_md(b))
        self.add_note(s["heading"], "\n\n".join(p for p in parts if p.strip()), "heading outside the mapping")

    def scan_windows(self, s, where):
        for b in s["blocks"]:
            lines = b["lines"] if b["kind"] == "para" else ([item["text"] for _, item in items_flat(b["items"])] if b["kind"] == "list" else [])
            for line in lines:
                self.window_from_line(unescape(line), where, require_words=True)

    def window_from_line(self, line: str, where: str, require_words: bool, date=None, incident=None):
        text = plain(line)
        m = WINDOW_PAIR.search(text)
        if not m or (require_words and not WINDOW_WORDS.search(text)):
            return False
        a, b = parse_time(m["a"]), parse_time(m["b"])
        if not a or not b:
            return False
        line_date = parse_date(text, (self.header_date or datetime.date.today()).year)
        day = DAY_PREFIX.match(text)
        if line_date is None and day and self.header_date:
            line_date = self.header_date.replace(day=int(day[1]))
        date = line_date or date or self.running_date or self.header_date or self.date_arg
        if date is None:
            return False
        if not a["explicit"] and not b["explicit"] and b["time"] < a["time"] and b["time"].hour + 12 < 24:
            b["time"] = b["time"].replace(hour=b["time"].hour + 12)
        start, end = self.stamp(date, a), self.stamp(date, b)
        if end <= start:
            end = self.stamp(date + datetime.timedelta(days=1), b)
        tag = INCIDENT_TAG.match(text)
        if tag and self.sub_incidents:
            incident = f"I{tag[1]}"
        if any(w["start"] == start.isoformat() and w["end"] == end.isoformat() for w in self.retro["windows"]):
            self.report.windows.append(f"duplicate of an existing window skipped ({where}): `{text}`")
            return True
        window = {"id": f"W{len(self.retro['windows']) + 1}", "h": "", "start": start.isoformat(), "end": end.isoformat(),
                  "kind": "outage", "text": re.sub(r"^\[\d+\]\s*", "", text), "teams": []}
        if incident:
            window["incident"] = incident
        self.retro["windows"].append(window)
        self.report.windows.append(f"{window['id']}: {window['start']} to {window['end']} ({where}) from `{text}`")
        return True

    def stamp(self, date, t: dict) -> datetime.datetime:
        zone = zone_of(t["zone"]) if t["zone"] else self.zone
        return datetime.datetime.combine(date, t["time"], tzinfo=zone).astimezone(self.zone)

    def finish_timeline(self):
        entries = []
        self.running_date = self.header_date or self.date_arg
        prev = None
        for s in self.timeline_rows:
            self.current_heading = s["heading"]
            where = f"timeline ({s['heading']})"
            for b in s["blocks"]:
                if b["kind"] == "table":
                    rows = b["rows"]
                    if not rows:
                        continue
                    header = [plain(c).lower() for c in rows[0]]
                    if any(h in ("start",) for h in header) and any(h in ("end",) for h in header):
                        si, ei = header.index("start"), header.index("end")
                        for row in rows[1:]:
                            cells = row + [""] * (len(header) - len(row))
                            self.window_from_line(f"from {cells[si]} to {cells[ei]} " + " ".join(c for i, c in enumerate(cells) if i not in (si, ei)), where, False)
                        continue
                    ti = next((i for i, h in enumerate(header) if h in TABLE_TIME_HEADERS), 0)
                    ei = next((i for i, h in enumerate(header) if h in TABLE_EVENT_HEADERS and i != ti), 1 if len(header) > 1 else 0)
                    body = rows[1:] if header and (header[0] in TABLE_TIME_HEADERS or (len(header) > 1 and header[1] in TABLE_EVENT_HEADERS) or not parse_time(rows[0][0])) else rows
                    for n, row in enumerate(body, 1):
                        cells = row + [""] * (max(ti, ei) + 1 - len(row))
                        prev = self.timeline_line(cells[ti], cells[ei], where, f"table line {b['line']} row {n}", entries, prev)
                elif b["kind"] == "list":
                    for _, item in items_flat(b["items"]):
                        prev = self.timeline_bullet(item["text"], where, f"list line {item['line']}", entries, prev)
                elif b["kind"] == "para":
                    for line in b["lines"]:
                        prev = self.timeline_bullet(line, where, f"paragraph line {b['line']}", entries, prev)
                else:
                    self.register_links(block_verbatim(b), where, "timeline")
                    self.report.unplace("Timeline", b, "not a table, list or paragraph")
        entries.sort(key=lambda e: e["_dt"])
        self.infer_timestamps(entries)

    def timeline_bullet(self, line, where, origin, entries, prev):
        text = unescape(line)
        stripped = TIME_LEAD.sub("", text)
        t = parse_time(stripped)
        if t:
            end = t["end"]
            tail = re.match(r"\s*[-–—]\s*", stripped[end:])
            second = parse_time(stripped[end + tail.end():]) if tail else None
            if second:
                end += tail.end() + second["end"]
            rest = re.sub(r"^\**\s*(?:[-–—:]\s*)?", "", stripped[end:]).strip()
            return self.timeline_line(stripped[:end], rest, where, origin, entries, prev, raw_text=text)
        return self.timeline_line("", text, where, origin, entries, prev, raw_text=text)

    def timeline_line(self, time_cell, event_cell, where, origin, entries, prev, raw_text=None):
        raw_text = raw_text if raw_text is not None else f"{time_cell} {event_cell}".strip()
        time_cell, event_cell = unescape(time_cell).strip(), unescape(event_cell)
        event_cell = self.take_images(event_cell, event_cell, where)
        plain_time = plain(time_cell)
        plain_event = plain(event_cell)
        whole = f"{plain_time} {plain_event}".strip()
        hint = ZONE_HINT.search(whole)
        if hint:
            zone = HINT_ZONES[hint[2].lower()]
            if zone != self.tz:
                self.report.header.append(f"Zone hint `{whole}` names `{zone}`, which differs from `--tz {self.tz}`; times without a suffix stay in `{self.tz}`")
            else:
                self.report.header.append(f"Zone hint `{whole}` agrees with `--tz {self.tz}`")
        year = (self.header_date or self.date_arg or datetime.date.today()).year
        t = parse_time(plain_time) if plain_time else None
        range_note = ""
        if t:
            tail = re.match(r"\s*[-–—]\s*", plain_time[t["end"]:])
            second = parse_time(plain_time[t["end"] + tail.end():]) if tail else None
            if second:
                range_note = f", start of the range `{plain_time}`"
                if second["explicit"] and not t["explicit"]:
                    hour = t["time"].hour
                    if second["time"].hour >= 12 and hour < 12 and hour + 12 <= second["time"].hour:
                        hour += 12
                    t["time"], t["explicit"] = t["time"].replace(hour=hour), True
        if t is None:
            line_date = parse_date(whole, year)
            if self.window_from_line(whole, where, False):
                if line_date:
                    self.running_date = line_date
                return prev
            remainder = re.sub(r"[\W_]+", "", DATE_ISO.sub("", DATE_MON.sub("", DATE_US.sub("", whole))))
            if line_date and (not remainder or hint):
                self.running_date = line_date
                self.report.times.append((f"`{whole}`", f"running date {line_date.isoformat()}", "", "day header", ""))
                return prev
            if hint or (self.sub_incidents and INCIDENT_TAG.fullmatch(whole) and not INCIDENT_TAG.fullmatch(whole)[2].strip()):
                return prev
            self.register_links(raw_text, where, "timeline")
            raw_text = self.take_images(raw_text, raw_text, where)
            if plain(raw_text).strip():
                self.report.unplace("Timeline", {"kind": "para", "lines": [raw_text]}, "no leading time" if not plain_time else f"`{plain_time}` is not a time")
            return prev
        line_date = parse_date(plain_time, year)
        if line_date:
            self.running_date = line_date
        date = self.running_date or self.date_arg
        if date is None:
            self.report.unplace("Timeline", {"kind": "para", "lines": [raw_text]}, "no date anywhere: pass --date")
            return prev
        dt = self.stamp(date, t)
        adjust = ""
        if prev is not None and not t["explicit"] and t["time"].hour < 12 and dt < prev and t["zone"] is None:
            pm = dt + datetime.timedelta(hours=12)
            if pm >= prev:
                dt, adjust = pm, ", read as PM to keep order"
        kind, why = "hypothesis", "fallback"
        for name, rx in KIND_RULES:
            m = re.search(rx, plain_event, re.I)
            if m:
                kind, why = name, f"`{m[0]}`"
                break
        actor = None
        m = ACTOR.match(plain_event)
        if m and m[1] not in NOT_ACTORS:
            actor = m[1]
        else:
            m = ACTOR_BY.search(plain_event)
            if m:
                actor = m[1]
        refs = []
        for info in self.register_links(f"{time_cell} {event_cell}", where, "timeline"):
            ref = {"url": info["url"], "kind": "pr"} if info["class"] == "pr" else info["url"]
            if ref not in refs:
                refs.append(ref)
        entry = {"ts": dt.isoformat(), "kind": kind, "text": clean_inline(event_cell).strip() or plain_event}
        if actor:
            entry["actor"] = actor
        if refs:
            entry["refs"] = refs
        entry["_dt"] = dt
        entries.append(entry)
        zone_note = f"explicit {t['zone']}, shown in {self.tz}" if t["zone"] else "assumed"
        self.report.times.append((f"`{plain_time}` ({origin})", dt.isoformat() + adjust + range_note, zone_note, f"{kind} ({why})", actor or ""))
        return dt

    def infer_timestamps(self, entries):
        ts = self.retro["timestamps"]
        windows = self.retro["windows"]
        if windows:
            first = min(windows, key=lambda w: w["start"])
            ts["onset"] = first["start"]
            self.report.stamps.append(f"onset {ts['onset']} from {first['id']} start")
        elif entries:
            deploys = [e for e in entries if e["kind"] == "deploy"]
            first_signal = next((e for e in entries if e["kind"] in ("alert", "report")), None)
            if deploys and (first_signal is None or deploys[0]["_dt"] <= first_signal["_dt"]):
                ts["onset"] = deploys[0]["ts"]
                self.report.stamps.append(f"onset {ts['onset']} from the first deploy entry")

        def first_of(kinds):
            return next((e for e in entries if e["kind"] in kinds), None)
        for field, kinds, label in (("detected", ("alert", "report"), "alert or report"), ("resolved", ("resolution",), "resolution"),
                                    ("allClear", ("allclear",), "all-clear")):
            e = first_of(kinds)
            if e:
                ts[field] = e["ts"]
                self.report.stamps.append(f"{field} {e['ts']} from the first {label} entry")
        last = None
        for field in ("onset", "detected", "engaged", "mitigated", "resolved", "allClear"):
            if ts[field] is None:
                continue
            if last and ts[field] < ts[last]:
                self.report.stamps.append(f"{field} {ts[field]} precedes {last}; dropped")
                ts[field] = None
            else:
                last = field
        if not any(ts.values()):
            self.report.stamps.append("nothing inferred: no windows and no alert, report or resolution entries")
        else:
            self.report.stamps.append("engaged and mitigated are never inferred; set them from the timeline")
        lo, hi = ts["onset"], ts["allClear"] or ts["resolved"]
        for e in entries:
            if lo and e["ts"] < lo:
                e["phase"] = "before"
            elif hi and e["ts"] > hi:
                e["phase"] = "after"
        self.retro["timeline"] = [{k: v for k, v in e.items() if k != "_dt"} for e in entries]

    def finish_meta(self):
        meta = self.retro["meta"]
        date = self.header_date
        why = self.date_why
        if not date:
            stamps = [e["ts"] for e in self.retro["timeline"]] + [w["start"] for w in self.retro["windows"]]
            if stamps:
                date = datetime.date.fromisoformat(min(stamps)[:10])
                why = "the earliest timeline entry or window" + (f"; {why}" if why else "")
        self.report.header.insert(1, f"Date: {date.isoformat() if date else '(none)'}" + (f" (from {why})" if date else " (no `Date:` field, title bracket, `--date` or dated timeline row)"))
        owners = {GITHUB_PR.match(p["url"]) and f"{GITHUB_PR.match(p['url'])[1]}/{GITHUB_PR.match(p['url'])[2]}" for p in self.retro["evidence"]["prs"]}
        ordered = {"title": meta["title"], "slug": slugify(meta["title"]), "date": date.isoformat() if date else "", "subtitle": SUBTITLE,
                   "status": meta["status"]}
        if "incident" in meta:
            ordered["incident"] = meta["incident"]
        ordered["authors"] = meta["authors"]
        if "attendees" in meta:
            ordered["attendees"] = meta["attendees"]
        ordered["teams"] = []
        if len(owners) == 1 and None not in owners:
            ordered["repo"] = next(iter(owners))
            self.report.header.append(f"Repo: {ordered['repo']} (every pull request link names it)")
        ordered["timezone"] = self.tz
        if "subIncidents" in meta:
            ordered["subIncidents"] = meta["subIncidents"]
        ordered["canonical"] = CANONICAL
        self.retro["meta"] = ordered


def write_notes(path: Path, report: str):
    if path.exists():
        text = path.read_text(encoding="utf-8")
        start = text.find(REPORT_HEADING)
        if start >= 0:
            m = re.search(r"^## (?!Import report)", text[start + len(REPORT_HEADING):], re.M)
            end = start + len(REPORT_HEADING) + m.start() if m else len(text)
            text = text[:start] + report + text[end:]
        else:
            text = text.rstrip("\n") + "\n\n" + report
    else:
        text = "# Working notes\n\n" + report
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


def convert(md_path: Path, docs_json_path: Path | None, out_dir: Path, tz: str, date: str | None) -> dict:
    doc_json = json.loads(docs_json_path.read_text(encoding="utf-8")) if docs_json_path else None
    importer = Importer(md_path.read_text(encoding="utf-8"), doc_json, md_path.name, tz, date)
    retro = importer.run()
    out_dir.mkdir(parents=True, exist_ok=True)
    for sub in EVIDENCE_DIRS:
        (out_dir / "evidence" / sub).mkdir(parents=True, exist_ok=True)
    for image in importer.images.values():
        if image:
            (out_dir / image["file"]).write_bytes(image["bytes"])
    (out_dir / "retro.json").write_text(json.dumps(retro, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = importer.report.markdown({k: doc_json.get(k, "") for k in ("title", "documentId", "revisionId")} if doc_json else {})
    write_notes(out_dir / "NOTES.md", report)
    return {"retro": retro, "report": report}


def import_gdoc(md_path: Path, docs_json_path: Path | None, out_dir: Path, tz: str, date: str | None) -> int:
    if not md_path.is_file():
        print(f"import-gdoc: {md_path} is not a file", file=sys.stderr)
        return 2
    if docs_json_path and not docs_json_path.is_file():
        print(f"import-gdoc: {docs_json_path} is not a file", file=sys.stderr)
        return 2
    try:
        zoneinfo.ZoneInfo(tz)
    except zoneinfo.ZoneInfoNotFoundError:
        print(f"import-gdoc: unknown timezone {tz}", file=sys.stderr)
        return 2
    retro = convert(md_path, docs_json_path, out_dir, tz, date)["retro"]
    print(f"wrote {out_dir / 'retro.json'}: {len(retro['timeline'])} timeline entries, {len(retro['windows'])} windows, "
          f"{len(retro['causes'])} causes, {len(retro['actions'])} actions, "
          f"{sum(len(v) for v in retro['lessons'].values())} lessons, {len(retro['evidence']['images'])} images, "
          f"{len(retro['notes'])} notes")
    print(f"read the import report in {out_dir / 'NOTES.md'}")
    return 0


def run_import(args) -> int:
    return import_gdoc(Path(args.exported_md), Path(args.docs_json) if args.docs_json else None, Path(args.out), args.tz, args.date)


def add_import_parser(sub):
    p = sub.add_parser("import-gdoc", help="convert a Google Docs Markdown export into a draft retro.json")
    p.add_argument("exported_md", metavar="exported.md")
    p.add_argument("docs_json", metavar="docs.json", nargs="?")
    p.add_argument("--out", required=True, help="retro directory to write (scaffolded or fresh)")
    p.add_argument("--tz", default=DEFAULT_TZ, help=f"zone for times without a suffix (default {DEFAULT_TZ})")
    p.add_argument("--date", help="YYYY-MM-DD used when the document carries no date")
    p.set_defaults(func=run_import)
    return p


def selftest() -> int:
    expected = FIXTURES / "expected"
    tmp = Path(tempfile.mkdtemp(prefix="retro-import-"))
    try:
        result = convert(FIXTURES / "gdoc-export.md", FIXTURES / "gdoc-export.docs.json", tmp, DEFAULT_TZ, None)
        failures = 0
        for name in ("retro.json", "NOTES.md"):
            got, want = (tmp / name).read_text(encoding="utf-8"), (expected / name).read_text(encoding="utf-8")
            if got != want:
                failures += 1
                sys.stdout.writelines(difflib.unified_diff(want.splitlines(True), got.splitlines(True), f"expected/{name}", f"actual/{name}"))
        for image in result["retro"]["evidence"]["images"]:
            if not (tmp / image["file"]).is_file():
                failures += 1
                print(f"missing {image['file']}")
        print("selftest ok" if not failures else f"selftest failed: {failures} mismatch(es)")
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="convert the fixture and diff against fixtures/expected/")
    sub = ap.add_subparsers(dest="cmd")
    add_import_parser(sub)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.cmd:
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
