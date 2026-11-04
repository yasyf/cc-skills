import itertools
import re

TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
OPTION_HEAD = re.compile(r"^(?:options?|picks?|choices?|answers?)$", re.I)
GOOD_COLUMN = re.compile(r"\b(?:for|gains?|pros?|upsides?|benefits?)\b", re.I)
BAD_COLUMN = re.compile(r"\b(?:against|costs?|cons?|downsides?|risks?)\b", re.I)
BOLD_NAME = re.compile(r"^\*\*(.+?)\*\*")
RECOMMENDED_MARK = re.compile(r"\s*\(recommended\b[^)]*\)", re.I)
RECOMMENDED_SENTENCE = re.compile(r"\s*Recommended: .*?\.(?=\s|$)")
TAP_LEAD = re.compile(r"^Tap one below[.:]?\s*", re.I)
BULLET = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.*)$")
WORD = re.compile(r"[a-z0-9]+")
MOST_MATCHED_OPTIONS = 6
FROM_PARAGRAPH = re.compile(r"^\*\*From:\*\*\s*(.+?)(?:\n\s*\n|\Z)", re.M | re.S)
SOURCE_PATH = re.compile(r"[\w.@-]+(?:/[\w.@-]+)+")
SOURCE_CONTEXT = 3


def cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def words(text):
    return set(WORD.findall(text.lower()))


def overlap(name, label):
    named, said = words(name), words(label)
    return len(named & said) / max(len(named | said), 1)


def tone_of(column):
    return "good" if GOOD_COLUMN.search(column) else "bad" if BAD_COLUMN.search(column) else "default"


def row_name(cell):
    bold = BOLD_NAME.match(cell)
    return (bold[1], cell[bold.end():]) if bold else (cell, "")


def fold_row(option, columns, row):
    name, rest = row_name(row[0])
    if words(name) - words(option["label"]):
        option["title"] = name
    option["note"] = re.sub(r"\s*\(recommended\)", "", rest, flags=re.I).strip(" ,;:.")
    option["hint"] = ""
    option["facts"] = [*option["facts"], *({"label": column, "value": value, "tone": tone_of(column)} for column, value in zip(columns, row[1:]) if value)]


def fold_table(table, choices):
    head, _, *rows = [cells(line) for line in table]
    if not OPTION_HEAD.match(head[0].strip("* ")) or not rows or len(rows) > MOST_MATCHED_OPTIONS:
        return False
    choice = next((choice for choice in choices if len(choice["options"]) == len(rows)), None)
    if not choice:
        return False
    names = [row_name(row[0])[0] for row in rows]
    order = max(itertools.permutations(choice["options"]), key=lambda options: sum(overlap(name, option["label"]) for name, option in zip(names, options)))
    for option, row in zip(order, rows):
        fold_row(option, head[1:], row)
    choices.remove(choice)
    return True


def repeats_pick(line, choices):
    bullet = BULLET.match(line)
    if not bullet:
        return False
    said = words(RECOMMENDED_MARK.sub("", bullet[1]))
    return any(said == words(RECOMMENDED_MARK.sub("", option["label"])) for choice in choices for option in choice["options"])


def table_end(lines, start):
    if not lines[start].lstrip().startswith("|") or start + 1 == len(lines) or not TABLE_RULE.match(lines[start + 1]):
        return None
    return next((at for at in range(start + 2, len(lines)) if not lines[at].lstrip().startswith("|")), len(lines))


def context_only(md, choices):
    lines = md.split("\n")
    unfolded = list(choices)
    kept = []
    at = 0
    while at < len(lines):
        end = table_end(lines, at)
        if end and fold_table(lines[at:end], unfolded):
            at = end
            continue
        if end:
            kept += lines[at:end]
            at = end
            continue
        if not repeats_pick(lines[at], choices):
            kept.append(lines[at])
        at += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def is_from(fact):
    return fact["label"].rstrip(":") == "From"


def quiet_provenance(parts):
    said = []
    for part in parts:
        if part["kind"] == "page" and (found := FROM_PARAGRAPH.search(part["md"])):
            said.append(found[1].strip())
            part["md"] = (part["md"][:found.start()] + part["md"][found.end():]).strip()
        if part["kind"] == "record":
            said += [str(fact["value"]) for fact in part["facts"] if is_from(fact)]
    kept = [part for part in parts if part["kind"] != "record" or not part["facts"] or not all(map(is_from, part["facts"]))]
    for part in kept:
        if part["kind"] == "record":
            part["facts"] = [fact for fact in part["facts"] if not is_from(fact)]
    return [*kept, {"kind": "from", "id": "from", "text": " ".join(said)}] if said else kept


def fold_picks(parts):
    choices = [part for part in parts if part["kind"] == "choice"]
    for part in parts:
        if part["kind"] == "page":
            part["md"] = context_only(part["md"], choices)
    return quiet_provenance(parts)


def source_window(lines, line, to):
    first, last = max(1, line - SOURCE_CONTEXT), min(len(lines), to + SOURCE_CONTEXT)
    return [{"n": n, "text": lines[n - 1]} for n in range(first, last + 1)]


def context_title(title):
    return TAP_LEAD.sub("", title)


def context_summary(summary, options):
    summary = TAP_LEAD.sub("", summary)
    return RECOMMENDED_SENTENCE.sub("", summary).strip() if any(option["recommended"] for option in options) else summary
