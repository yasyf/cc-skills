#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Read live standing rules from cci and lint their handoff.

    standing.py live   --drive DRIVE
    standing.py lint   (--doc ID | --file PATH) --program SLUG [--previous-doc ID | --previous-file PATH] [--repo PATH]

A standing rule is a cci ``go`` record with topic ``standing``. The root addresses it
to a desk and cites its durable answer with ``--ccn <answer id>``. It is never done.
Only a later ``correction`` record with ``--topic standing --re <seq>`` and a later
answer replaces it. Rule ids are ``#<seq>``. ``live`` prints each live rule's id, text,
and source: ``ccn <answer id>`` when cited, otherwise ``cci #<seq>``.

``lint`` checks that ``## Standing owner rules`` exists and names the current
``standing-rules:<program>`` doc. It checks that rule ids from the previous handoff,
including ``#<seq>`` and old ``R<n>`` ids, are carried or superseded. Lines requiring
owner approval must cite a live answer id. The citation check skips quoted lines
prefixed with ``  >``.
Durable answer titles are not required in the handoff. With the register ledger read
through ``rulings.py rows --json``, a bullet that leads with a register row's answer id
or key must carry that row's words verbatim: an id alone, or a quotation that differs
from the words, is a finding. The sentence "no standing-rules register doc" is always a
finding. A failed register read skips the row checks and posts one ``blocker`` to the
root on cci. ``lint`` exits 3 on a finding.
Both commands use only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import cci
from long_running import ledger
from long_running import rulings

ID = r"(?:[A-Z]{1,2}\d+(?:\.\d+)?|#\d+)"
SECTION = re.compile(r"^##\s+standing owner rules\b.*$", re.IGNORECASE | re.MULTILINE)
OWN_RULES = re.compile(r"^#{2,3}\s+standing owner rules\b", re.IGNORECASE | re.MULTILINE)
NEXT_SECTION = re.compile(r"^#{1,2}\s", re.MULTILINE)
BULLET_ID = re.compile(rf"^\s*[-*]\s+`?({ID})`?\b")
OWNER_GATE = re.compile(
    r"\bowner(?:'|’)?s (?:word|approval|sign-?off|go|click)\b|\bowner (?:approval|sign-?off|go|click)\b|\breserved for the owner\b",
    re.IGNORECASE,
)
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
SHORT = 7
VIOLATIONS = 3
REGISTER_HEADING = "## Standing owner rules"
RULE_LINE = re.compile(r"^\d+\.\s", re.MULTILINE)
QUOTE = "  >"
TOPIC = "standing"
KINDS = ("go", "correction")
RULINGS = Path(__file__).with_name("rulings.py")
NO_REGISTER_DOC = re.compile(r"no `?standing-rules`? register doc")
ROW_BULLET = re.compile(r"^(\s*)(?:[-*]|\d+\.)\s+[*`\[]*([a-z]+/[\w.-]+|[0-9a-f]{7,40})\b")
QUOTED = re.compile(r"[“\"][^”\"]+[”\"]")
CONTINUATION = 4
LIVE_ROWS = frozenset({"in-force", "pending-owner"})
IN_FORCE = "in-force"
ROWS_HEADING = "### Scope rows, in the owner's words"
TOUCHED_WINDOW = timedelta(hours=24)


@dataclass
class Standing:
    rules: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    superseded: dict[str, str] = field(default_factory=dict)


@dataclass
class Rows:
    rows: dict[str, dict] = field(default_factory=dict)
    failure: str | None = None
    read_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def read(run: Callable[[list[str]], str], drive: str) -> Standing:
    tailed = cci.records(run, drive, f"--topic={TOPIC}", *(f"--kind={kind}" for kind in KINDS))
    records = [record for record in tailed if record["drive"] == drive and record.get("topic") == TOPIC and record["kind"] in KINDS]
    found = Standing(superseded={f"#{record['re']}": f"#{record['seq']}" for record in records if record["kind"] == "correction" and record.get("re")})
    for record in records:
        rid = f"#{record['seq']}"
        if rid not in found.superseded:
            found.rules[rid] = f"{rid} {record['text']}"
            found.sources[rid] = f"ccn {ccn_id}" if (ccn_id := record.get("refs", {}).get("ccn")) else f"cci {rid}"
    return found


def section(body: str) -> list[str] | None:
    if not (heading := SECTION.search(body)):
        return None
    following = NEXT_SECTION.search(body, heading.end())
    return body[heading.end() : following.start() if following else len(body)].strip("\n").splitlines()


def carried(lines: list[str]) -> dict[str, str]:
    return {match[1]: line for line in lines if (match := BULLET_ID.match(line))}


def cites(line: str, live: set[str]) -> bool:
    return any(any(answer.startswith(token) for answer in live) for token in HEX.findall(line))


def gated(line: str, live: set[str]) -> bool:
    return OWNER_GATE.search(line) is not None and not cites(line, live)


def rule_findings(body: str, previous: str | None, register: dict | None) -> list[str]:
    problems = []
    if (lines := section(body)) is None:
        problems.append("no `## Standing owner rules` section; regenerate the handoff with `handoff.py generate`")
        lines = []
    if register and pointer(register) not in lines:
        problems.append(
            f"the standing rules section does not name register `{register['id'][:SHORT]}`; regenerate the handoff with `handoff.py generate`"
        )
    if previous is not None:
        now = carried(lines)
        problems += [
            f"dropped `{rid}` since the previous handoff ({line.strip()}); carry it, or write `- {rid} superseded by <id>`"
            for rid, line in carried(section(previous) or []).items()
            if rid not in now and "superseded by" not in line
        ]
    return problems


def listed(fields: dict, name: str) -> list:
    return json.loads(fields.get(name) or "[]")


def flat(text: str) -> str:
    return " ".join(text.split())


def cited_rows(token: str, rows: dict[str, dict]) -> dict[str, dict]:
    if "/" in token:
        return {token: rows[token]} if rows.get(token, {}).get("words") else {}
    return {
        key: fields
        for key, fields in rows.items()
        if fields.get("words") and any(answer.startswith(token) or token.startswith(answer) for answer in listed(fields, "answer"))
    }


def bullet_block(lines: list[str], at: int, indent: int) -> str:
    following = []
    for line in lines[at + 1 :]:
        if indent_of(line) < indent + CONTINUATION:
            break
        following.append(line)
    return " ".join([lines[at], *following])


def row_findings(body: str, rows: Rows | None) -> list[str]:
    problems = [
        f"the handoff says \"{found[0]}\"; the register ledger holds the owner's rows, so regenerate the section with `handoff.py generate`"
        for found in NO_REGISTER_DOC.finditer(body)
    ][:1]
    if rows is None or rows.failure:
        return problems
    lines = body.splitlines()
    for at, line in enumerate(lines):
        if line.startswith(QUOTE) or not (match := ROW_BULLET.match(line)) or not (cited := cited_rows(match[2], rows.rows)):
            continue
        block = bullet_block(lines, at, len(match[1]))
        if any(flat(fields["words"]) in flat(block) for fields in cited.values()):
            continue
        key, fields = next(iter(cited.items()))
        shape = "paraphrases register row" if QUOTED.search(block) else "cites without its words register row"
        problems.append(f"`{match[2]}` {shape} `{key}`: {line.strip()[:200]}; quote the row's words verbatim: “{fields['words']}”")
    return problems


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def register_lines(lines: list[str], rows: Rows | None) -> set[int]:
    if rows is None or rows.failure:
        return set()
    unconfirmed = set(unconfirmed_lines(rows))
    quoted: set[int] = set()
    for at, line in enumerate(lines):
        if line in unconfirmed:
            quoted.add(at)
        if not (match := ROW_BULLET.match(line)) or not (cited := cited_rows(match[2], rows.rows)):
            continue
        quoted.add(at)
        for fields in cited.values():
            if lines[at + 1 : at + 1 + len(following := continuation_lines(fields))] == following:
                quoted.update(range(at + 1, at + 1 + len(following)))
        children = {child for fields in cited.values() for child in child_lines(fields)}
        for below in range(at + 1, len(lines)):
            if indent_of(lines[below]) <= len(match[1]):
                break
            if lines[below] in children:
                quoted.add(below)
    return quoted


def child_lines(fields: dict) -> list[str]:
    return [
        *(f"  - {implication_line(implication)}" for implication in listed(fields, "implications")),
        f"  - names: {' | '.join(listed(fields, 'names'))}",
        f"  - evidence: {fields.get('evidence') or 'none recorded'}",
    ]


def lint(body: str, previous: str | None, register: dict | None, live: set[str], rows: Rows | None = None) -> list[str]:
    lines = body.splitlines()
    quoted = register_lines(lines, rows)
    return (
        rule_findings(body, previous, register)
        + [
            f"owner-gate line cites no live answer id: {line.strip()[:200]}"
            for at, line in enumerate(lines)
            if at not in quoted and not line.startswith(QUOTE) and gated(line, live)
        ]
        + row_findings(body, rows)
    )


def failure_of(failed: Exception) -> str:
    if isinstance(failed, subprocess.CalledProcessError):
        return next(iter((failed.stderr or "").strip().splitlines()[-1:]), f"exit {failed.returncode}")
    if isinstance(failed, subprocess.TimeoutExpired):
        return f"timed out after {failed.timeout:g}s"
    return "printed no JSON"


def post_blocker(drive: str, text: str) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y%m%dT%H")
    argv = ["cci", "post", "--drive", drive, "--lane", "root", "--kind", "blocker", "--to", "root", "--id", f"register-rows:{drive}:{hour}", "--text", text]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return


def register_rows(run: Callable[[list[str]], str], repo: str, which: list[str], drive: str) -> Rows | None:
    try:
        found = json.loads(run([sys.executable, str(RULINGS), "rows", *which, "--repo", repo, "--json"]))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as failed:
        reason = failure_of(failed)
        post_blocker(
            drive,
            f"The register rows for {drive} did not load for the handoff: {reason}. "
            f"Read them with `rulings.py rows {' '.join(which)} --json` and carry the scope rows' words by hand.",
        )
        return Rows(failure=reason)
    return Rows(rows=found["rows"]) if found and found.get("ledger") else None


def continuation_lines(fields: dict) -> list[str]:
    following = [f"{' ' * CONTINUATION}{line}" for line in fields["words"].splitlines()[1:]]
    return [*following[:-1], f"{following[-1]}”"] if following else []


def quoted_words(key: str, fields: dict, detail: str) -> list[str]:
    first = next(iter(fields["words"].splitlines()), "")
    following = continuation_lines(fields)
    return [f"- `{key}` ({detail}): “{first}{'' if following else '”'}", *following]


def implication_line(implication: dict) -> str:
    return f"implication {implication['n']}, {implication['direction']}, {implication['state']}: {implication['text']}"


def current_rows(rows: Rows) -> dict[str, dict]:
    return {key: fields for key, fields in rows.rows.items() if fields.get("status") in LIVE_ROWS and fields.get("words")}


def rows_section(rows: Rows) -> list[str]:
    if rows.failure:
        return [f"- the register rows did not load ({rows.failure}); read them with `rulings.py rows --json`", ""]
    current = current_rows(rows)
    out = [ROWS_HEADING, ""]
    for key, fields in current.items():
        if fields["kind"] == "scope":
            out += quoted_words(key, fields, f"{fields['status']}, said {fields.get('said') or 1}")
            out += child_lines(fields)[:-1]
    since = rows.read_at - TOUCHED_WINDOW
    touched = {key: fields for key, fields in rows.rows.items() if (updated := fields.get("updated_at")) and datetime.fromisoformat(updated) >= since}
    out += ["", "### Rows touched in the last 24 hours", ""]
    out += [
        line
        for key, fields in touched.items()
        for line in (
            quoted_words(key, fields, f"{fields['kind']}, touched {fields['updated_at']}")
            if fields.get("words")
            else [f"- `{key}` ({fields['kind']}, touched {fields['updated_at']}): no words"]
        )
    ] or ["- none"]
    out += ["", "### Owed gates", ""]
    out += [line for key, fields in current.items() if fields["kind"] == "gate" and fields["status"] == IN_FORCE for line in quoted_words(key, fields, "owed")] or ["- none"]
    out += ["", "### Rows at risk or broken", ""]
    out += [
        line
        for key, fields in rows.rows.items()
        if fields.get("status") != IN_FORCE
        for line in [
            *(quoted_words(key, fields, fields.get("status") or "no status") if fields.get("words") else [f"- `{key}` ({fields.get('status') or 'no status'}): no words"]),
            child_lines(fields)[-1],
        ]
    ] or ["- none"]
    out += ["", "### Unconfirmed implications", ""]
    return out + (unconfirmed_lines(rows) or ["- none"]) + [""]


def unconfirmed_lines(rows: Rows) -> list[str]:
    return [
        f"- {implication_line(implication)} (of `{key}`)"
        for key, fields in current_rows(rows).items()
        for implication in listed(fields, "implications")
        if implication["state"] == "proposed"
    ]


def ccn(repo: str, *args: str) -> str:
    return subprocess.run(["ccn", "-R", repo, *args], capture_output=True, text=True, check=True).stdout


def answers(repo: str, *labels: str) -> list[dict]:
    flags = [arg for label in labels for arg in ("--label", label)]
    return json.loads(ccn(repo, "answer", "list", *flags, "--limit", "0", "--json") or "[]")


def doc_body(repo: str, doc_id: str) -> str:
    return json.loads(ccn(repo, "doc", "show", doc_id, "--json"))["body"]


def pointer(register: dict) -> str:
    return f"Register `ccn doc show {register['id'][:SHORT]}`: {rule_count(register)} owner-approved rules, delivered verbatim after every compaction."


def rule_count(register: dict | None) -> int:
    return len(RULE_LINE.findall(register["body"])) if register else 0


def section_of(register: dict | None, lines: list[str], narrative: str = "", rows: Rows | None = None) -> str:
    out = [REGISTER_HEADING, ""]
    if register:
        out += [pointer(register), ""]
    elif rows is None or rows.failure:
        own = f"; the root's own `{REGISTER_HEADING}` follows verbatim under `## Root narrative`" if OWN_RULES.search(narrative) else ""
        out += [f"- no `standing-rules` register doc{own}", ""]
    if rows is None:
        return "\n".join(out + lines) + "\n"
    return "\n".join(out + lines + ([""] if lines else []) + rows_section(rows))


def cmd_live(args: argparse.Namespace) -> int:
    found = read(ledger.Shell().run, args.drive)
    print(f"live standing: {', '.join(found.rules) or 'none'}")
    for rid, text in found.rules.items():
        print(f"{text} [{found.sources[rid]}]")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    body = Path(args.file).read_text() if args.file else doc_body(args.repo, args.doc)
    previous = (
        Path(args.previous_file).read_text()
        if args.previous_file
        else doc_body(args.repo, args.previous_doc) if args.previous_doc else None
    )
    live = {answer["id"] for answer in answers(args.repo)}
    shell = ledger.Shell()
    rows = register_rows(shell.run, args.repo, ["--program", args.program], args.program)
    problems = lint(body, previous, rulings.register(shell, args.repo, args.program), live, rows)
    for problem in problems:
        print(problem)
    return VIOLATIONS if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(required=True)

    live = subparsers.add_parser("live", help="print the live standing rules on a cci drive")
    live.add_argument("--drive", required=True)
    live.set_defaults(handler=cmd_live)

    check = subparsers.add_parser("lint", help="lint a progress doc or handoff file against the durable answers")
    body = check.add_mutually_exclusive_group(required=True)
    body.add_argument("--doc", metavar="ID")
    body.add_argument("--file", metavar="PATH")
    before = check.add_mutually_exclusive_group()
    before.add_argument("--previous-doc", metavar="ID")
    before.add_argument("--previous-file", metavar="PATH")
    check.add_argument("--program", required=True, metavar="SLUG")
    check.set_defaults(handler=cmd_lint)

    check.add_argument("--repo", default=".", metavar="PATH")
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    sys.exit(main())
