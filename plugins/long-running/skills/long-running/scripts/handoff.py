#!/usr/bin/env python3
"""The generated handoff: a drive root's restart state, built from its sources at every compaction.

    handoff.py generate --program SLUG --plan PATH [--inbox-dir DIR] [--ledger ID] [--session FILE|-]
                        [--narrative-doc ID | --narrative-file PATH] [--generated-doc ID] [--fresh-since ISO]
                        [--strict] [--folder] [--repo PATH]
    handoff.py lint     (--doc ID | --file PATH) --program SLUG [--plan PATH] [--previous-doc ID | --previous-file PATH] [--repo PATH]

STDLIB ONLY. ``generate`` reads the sources a hand-written handoff used to retell: every
``scope:durable`` answer labelled with the program, every live ``(standing)`` inbox rule, the
ledger's open owner asks, the root's open tasks, its lanes and monitors, each inbox's head,
cursor, and newest rulings, and the drive registry. It writes them as a ``(generated)`` progress
doc under ``progress:<program>``, plus the same markdown at ``<plan-stem>-progress/<UTC>-generated.md``.
``--generated-doc`` names the session's generated doc, edited in place while it is active, so a
session keeps one. The active doc is the hand-written record when there is one: ``--narrative-doc``,
else the newest hand-written progress doc this session created since ``--fresh-since`` or within
:data:`FRESH_MINUTES`. Every other active progress doc is superseded by the active doc. The root's
narrative is the last section: the record's body or ``--narrative-file``, else the narrative the
newest progress doc carries. ``--folder`` skips cc-notes and writes the file alone. ``--session``
is the hook's JSON, ``{"session_id", "tasks": [...], "background": [...]}``.

A rule the previous handoff carried and the sources no longer hold is written once as
``<id> superseded by ...``, so :func:`standing.lint` passes on every generated doc. Its other
findings, owner-gate lines in the narrative or a live inbox rule that cite no live answer, each
named by its file and line, and the plan's own uncited owner-gate lines go under
``## Lint findings``; with ``--strict`` a narrative or inbox finding writes nothing and exits
:data:`standing.VIOLATIONS`.

It prints ``{"id", "generated", "file", "digest"}``: ``id`` is the active doc. ``digest`` is the post-compaction restore, at most
:data:`DIGEST_BUDGET` UTF-8 bytes: Claude Code injects SessionStart context past 10,000
characters as a 2 KB preview, and the cc-notes restores in the same output take 7,500.

``lint`` runs the same checks over any handoff and exits :data:`standing.VIOLATIONS` on a finding.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import drive
import ledger
import standing

DIGEST_BUDGET = 2000
RULING = re.compile(rf"^\s*(?:[-*]\s+)?\**`?({standing.ID})\b")
RULINGS_PER_INBOX = 5
RULING_CHARS = 400
TASK_CHARS = 200
DIGEST_RULE_CHARS = 160
DIGEST_TITLE_CHARS = 100
SHORT = 7
FRESH_MINUTES = 30
NARRATIVE = "## Root narrative"
FINDINGS = "## Lint findings"
GENERATED_MARK = "(generated)"
PROVENANCE = re.compile(r"^_From (.+?)(?:, carried forward)?\._\n\n")
CLOSED_ASKS = (ledger.ASK_DROPPED, ledger.ASK_ANSWERED, ledger.ASK_LIVE)
LANE_TYPES = ("subagent", "teammate", "workflow", "cloud session")


@dataclass
class Inbox:
    name: str
    head: str | None
    cursor: str | None
    rulings: list[str]


@dataclass
class Handoff:
    program: str
    plan: str
    at: datetime
    registry: dict | None = None
    durable: list[dict] = field(default_factory=list)
    standing: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    superseded: dict[str, str] = field(default_factory=dict)
    retired: list[str] = field(default_factory=list)
    asks: list[str] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    lanes: list[dict] = field(default_factory=list)
    monitors: list[dict] = field(default_factory=list)
    inboxes: list[Inbox] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    plan_findings: list[str] = field(default_factory=list)
    narrative: str = ""
    narrative_from: str = ""
    narrative_edit: str = "in your next progress record"
    record: str | None = None
    generated: str | None = None
    stale: list[str] = field(default_factory=list)

    @property
    def stamp(self) -> str:
        return self.at.strftime("%Y-%m-%dT%H%MZ")

    @property
    def title(self) -> str:
        return f"{self.program}: progress {self.stamp} {GENERATED_MARK}"


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ccn_json(shell: ledger.Shell, repo: str, *args: str) -> object:
    return json.loads(shell.run(["ccn", "-R", repo, *args, "--json"]) or "null")


def durable_answers(shell: ledger.Shell, repo: str, program: str) -> list[dict]:
    found: dict[str, dict] = {}
    for label in (program, f"progress:{program}"):
        for answer in ccn_json(shell, repo, "answer", "list", "--label", "scope:durable", "--label", label, "--limit", "0") or []:
            found.setdefault(answer["id"], answer)
    return sorted(found.values(), key=lambda answer: answer.get("updated_at", ""), reverse=True)


def live_answer_ids(shell: ledger.Shell, repo: str) -> set[str]:
    return {answer["id"] for answer in ccn_json(shell, repo, "answer", "list", "--limit", "0") or []}


def open_asks(shell: ledger.Shell, ledger_id: str) -> list[str]:
    rows = ledger.Notes(shell, ledger_id).rows()
    prs = {key: fields for key, fields in rows.items() if key.isdigit()}
    live = ledger.live_since(rows)
    moment = ledger.now()
    lines = []
    for key, fields in rows.items():
        if key.startswith(ledger.ASK_PREFIX) and (state := ledger.ask_state(fields, prs, moment, live)) not in CLOSED_ASKS:
            lines.append(f"{ledger.ask_line(key, fields)} — {state or 'fresh'}")
    return lines


def read_inboxes(handoff: Handoff, directory: Path) -> None:
    for path in sorted(directory.glob("*.md")):
        lines = path.read_text(errors="replace").splitlines()
        inbox = standing.read_inbox(lines, path.name)
        handoff.standing |= {rid: f"{text.lstrip('-* ')} [{path.name}]" for rid, text in inbox.live().items()}
        handoff.sources |= {rid: f"{path}:{inbox.at[rid]}" for rid in inbox.live()}
        handoff.superseded |= inbox.superseded
        rulings = [line.strip() for line in lines if RULING.match(line)]
        cursor = path.with_name(f"{path.name}.cursor")
        handoff.inboxes.append(
            Inbox(
                name=path.name,
                head=RULING.match(rulings[-1])[1] if rulings else None,
                cursor=cursor.read_text().strip() if cursor.is_file() else None,
                rulings=[clip(line, RULING_CHARS) for line in rulings[-RULINGS_PER_INBOX:]],
            )
        )


def uncited_plan_lines(plan: Path, live: set[str]) -> list[str]:
    if not plan.is_file():
        return []
    return [
        f"plan owner-gate line cites no live answer: {plan.name}:{number}: {clip(line, RULING_CHARS)}"
        for number, line in enumerate(plan.read_text().splitlines(), 1)
        if standing.OWNER_GATE.search(line) and not standing.cites(line, live)
    ]


def lint_view(body: str) -> str:
    if f"\n{NARRATIVE}\n" not in body:
        return body
    rules = "\n".join(standing.section(body) or [])
    return f"## Standing owner rules\n{rules}\n\n{NARRATIVE}\n\n{narrative_of(body)}\n"


def narrative_of(body: str) -> str:
    _, marker, rest = body.partition(f"\n{NARRATIVE}\n")
    return PROVENANCE.sub("", rest.strip()) if marker else body.strip()


def carried_from(body: str, record: str) -> str:
    _, marker, rest = body.partition(f"\n{NARRATIVE}\n")
    return f"{match[1] if marker and (match := PROVENANCE.match(rest.strip())) else record}, carried forward"


def active_progress(shell: ledger.Shell, repo: str, program: str) -> list[dict]:
    return ccn_json(shell, repo, "doc", "list", "--label", f"progress:{program}") or []


def doc_body(shell: ledger.Shell, repo: str, doc_id: str) -> str:
    return ccn_json(shell, repo, "doc", "show", doc_id)["body"]


def creation(shell: ledger.Shell, repo: str, doc_id: str) -> dict:
    return next(entry for entry in ccn_json(shell, repo, "doc", "history", doc_id) if entry["kind"] == "create")


def fresh_cutoff(at: datetime, since: str | None) -> datetime:
    window = at - timedelta(minutes=FRESH_MINUTES)
    return min(window, datetime.fromisoformat(since)) if since else window


def fresh_record(shell: ledger.Shell, repo: str, docs: list[dict], session: str | None, cutoff: datetime) -> str | None:
    if not session:
        return None
    fresh = []
    for doc in docs:
        if doc["title"].endswith(GENERATED_MARK):
            continue
        created = creation(shell, repo, doc["id"])
        if created.get("session") == session and datetime.fromisoformat(created["time"]) >= cutoff:
            fresh.append(doc)
    return max(fresh, key=lambda doc: doc["updated_at"])["id"] if fresh else None


def rule_lines(handoff: Handoff) -> list[str]:
    lines = [f"- {answer['id'][:SHORT]} {answer['title']}" for answer in handoff.durable]
    lines += [f"- {text}" for text in handoff.standing.values()]
    return lines + [f"- {line}" for line in handoff.retired]


def render(handoff: Handoff) -> str:
    registry = handoff.registry or {}
    out = [
        f"# {handoff.title}",
        "",
        f"Generated from sources by the long-running compaction hook at {handoff.at:%Y-%m-%dT%H:%M:%SZ}. "
        f"Every section above `{NARRATIVE}` is rebuilt at each handoff; change the sources, never this doc.",
        "",
        "## Read first",
        f"- Plan: `{handoff.plan}`",
    ]
    if registry:
        out.append(
            f"- Drive `{registry['drive']}`: ledger `{registry['ledger'][:SHORT]}`, Orca run `{registry.get('orca_run') or '-'}`, "
            f"checkout `{registry['checkout']}`, root sessions {', '.join(session[:8] for session in registry['sessions'])}"
        )
    out += ["", "## Standing owner rules"] + (rule_lines(handoff) or ["- none recorded"])
    out += ["", "## Open owner asks"] + ([f"- {line}" for line in handoff.asks] or ["- none"])
    out += ["", "## Open tasks"]
    out += [f"- #{task['id']} [{task['status']}] {clip(task['subject'], TASK_CHARS)}" for task in handoff.tasks] or ["- none"]
    out += ["", "## Lanes and monitors"]
    out += [f"- {task['type']}: {clip(task['description'], TASK_CHARS)} ({task['status']})" for task in handoff.lanes + handoff.monitors]
    if not (handoff.lanes or handoff.monitors):
        out.append("- none running at the last stop")
    out += ["", "## Inboxes"]
    for inbox in handoff.inboxes:
        out += ["", f"### {inbox.name}: head {inbox.head or '-'}, cursor {inbox.cursor or '-'}"]
        out += [f"- {line.lstrip('-* ')}" for line in inbox.rulings]
    if not handoff.inboxes:
        out.append("- no inbox directory")
    out += ["", FINDINGS] + ([f"- {line}" for line in handoff.findings + handoff.plan_findings] or ["- none"])
    out += ["", NARRATIVE, ""]
    out.append(
        f"_From {handoff.narrative_from}._\n\n{handoff.narrative}" if handoff.narrative else "_The root has written no narrative yet._"
    )
    return "\n".join(out) + "\n"


def retire(handoff: Handoff, previous: str | None) -> None:
    now = standing.carried(rule_lines(handoff))
    for rid, line in standing.carried(standing.section(previous or "") or []).items():
        if rid not in now and "superseded by" not in line:
            successor = handoff.superseded.get(rid, f"nothing: the sources dropped it after the previous handoff (was: {clip(line.lstrip('-* '), 200)})")
            handoff.retired.append(f"{rid} superseded by {successor}")


def uncited_inbox_rules(handoff: Handoff, live: set[str]) -> list[str]:
    return [
        f"{handoff.sources[rid]}: standing rule {rid} is an owner-gate line that cites no live answer id: "
        f"{clip(text, RULING_CHARS)}; end that line with `(answer <id>)`, or append `- <new id> (standing) supersedes {rid} "
        "…, answer <id>` to the inbox"
        for rid, text in handoff.standing.items()
        if standing.gated(text, live)
    ]


def uncited_narrative_lines(handoff: Handoff, live: set[str]) -> list[str]:
    return [
        f"narrative ({handoff.narrative_from}) line {number}: owner-gate line cites no live answer id: "
        f"{clip(line, RULING_CHARS)}; end that line with `(answer <id>)` {handoff.narrative_edit}"
        for number, line in enumerate(handoff.narrative.splitlines(), 1)
        if standing.gated(line, live)
    ]


def check(handoff: Handoff, previous: str | None, live: set[str]) -> None:
    retire(handoff, previous)
    handoff.findings = standing.rule_findings(render(handoff), previous, handoff.durable)
    handoff.findings += uncited_inbox_rules(handoff, live) + uncited_narrative_lines(handoff, live)
    handoff.plan_findings = uncited_plan_lines(Path(handoff.plan), live)


def fits(lines: list[str], extra: str, budget: int) -> bool:
    return len("\n".join([*lines, extra]).encode()) <= budget


def digest(handoff: Handoff, doc: str, budget: int = DIGEST_BUDGET) -> str:
    head = [
        f"Compacted long-running drive `{handoff.program}`. Before acting, read {doc} "
        f"(it supersedes the summary), then `{handoff.plan}`. Reload Skill `long-running` if its rules are gone."
    ]
    if handoff.standing:
        head.append(clip(f"Live standing inbox rules: {', '.join(handoff.standing)}.", budget // 4))
    tail = [
        f"Open: {len(handoff.asks)} owner asks, {len(handoff.tasks)} tasks, {len(handoff.lanes)} lanes, "
        f"{len(handoff.monitors)} monitors, {len(handoff.findings) + len(handoff.plan_findings)} lint findings."
    ]
    body: list[str] = []
    entries = [clip(text, DIGEST_RULE_CHARS) for text in handoff.standing.values()]
    entries += [f"{answer['id'][:SHORT]} {clip(answer['title'], DIGEST_TITLE_CHARS)}" for answer in handoff.durable]
    for shown, entry in enumerate(entries):
        rest = len(entries) - shown - 1
        more = [f"+{rest} more in the handoff."] if rest else []
        if not fits(head + body + more + tail, entry, budget):
            body.append(f"+{len(entries) - shown} more in the handoff.")
            break
        body.append(entry)
    return "\n".join(head + body + tail)


def progress_folder(plan: Path) -> Path:
    return plan.with_name(f"{plan.stem}-progress")


def progress_file(plan: Path, stamp: str) -> Path:
    return progress_folder(plan) / f"{stamp}-generated.md"


def build(args: argparse.Namespace, shell: ledger.Shell) -> tuple[Handoff, str | None]:
    plan = Path(args.plan).expanduser()
    session = json.loads(sys.stdin.read() if args.session == "-" else Path(args.session).read_text()) if args.session else {}
    registry = drive.find(None, session["session_id"]) if session.get("session_id") else None
    handoff = Handoff(program=args.program, plan=str(plan), at=datetime.now(timezone.utc), registry=registry)
    background = session.get("background", [])
    handoff.lanes = [task for task in background if task["type"] in LANE_TYPES]
    handoff.monitors = [task for task in background if task["type"] not in LANE_TYPES]
    handoff.tasks = session.get("tasks", [])
    inbox_dir = Path(args.inbox_dir).expanduser() if args.inbox_dir else Path.home() / ".claude" / "scratch" / args.program / "inbox"
    if inbox_dir.is_dir():
        read_inboxes(handoff, inbox_dir)
    if args.narrative_file:
        handoff.narrative, handoff.narrative_from = Path(args.narrative_file).read_text().strip(), f"file {Path(args.narrative_file).name}"
        handoff.narrative_edit = f"in `{args.narrative_file}`"
    if args.folder:
        files = sorted(progress_folder(plan).glob("*-generated.md"), key=lambda path: path.stat().st_mtime)
        previous = files[-1].read_text() if files else None
        if previous and not args.narrative_file:
            handoff.narrative, handoff.narrative_from = narrative_of(previous), carried_from(previous, f"file {files[-1].name}")
        check(handoff, previous, set())
        return handoff, previous
    handoff.durable = durable_answers(shell, args.repo, args.program)
    if ledger_id := args.ledger or (registry or {}).get("ledger"):
        handoff.asks = open_asks(shell, ledger_id)
    docs = active_progress(shell, args.repo, args.program)
    handoff.record = args.narrative_doc or fresh_record(
        shell, args.repo, docs, session.get("session_id"), fresh_cutoff(handoff.at, args.fresh_since)
    )
    handoff.generated = next((doc["id"] for doc in docs if doc["id"] == args.generated_doc), None)
    handoff.stale = [doc["id"] for doc in docs if doc["id"] not in (handoff.record, handoff.generated)]
    active = [doc for doc in docs if doc["id"] != handoff.record]
    previous = doc_body(shell, args.repo, max(active, key=lambda doc: doc["updated_at"])["id"]) if active else None
    if handoff.record and not args.narrative_file:
        handoff.narrative, handoff.narrative_from = doc_body(shell, args.repo, handoff.record).strip(), f"doc {handoff.record[:SHORT]}"
        handoff.narrative_edit = f"via `ccn doc edit {handoff.record[:8]} --body -`"
    elif previous and not args.narrative_file:
        newest = max(active, key=lambda doc: doc["updated_at"])
        handoff.narrative, handoff.narrative_from = narrative_of(previous), carried_from(previous, f"doc {newest['id'][:SHORT]}")
    check(handoff, previous, live_answer_ids(shell, args.repo))
    return handoff, previous


def cmd_generate(args: argparse.Namespace, shell: ledger.Shell) -> int:
    handoff, _ = build(args, shell)
    if args.strict and handoff.findings:
        print("\n".join(handoff.findings))
        return standing.VIOLATIONS
    markdown = render(handoff)
    path = progress_file(Path(handoff.plan), handoff.stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown)
    if args.folder:
        print(json.dumps({"id": None, "generated": None, "file": str(path), "digest": digest(handoff, f"the generated handoff `{path}`")}))
        return 0
    generated = write_generated(handoff, shell, args.repo, markdown)
    active = handoff.record or generated
    for stale in handoff.stale:
        shell.run(["ccn", "-R", args.repo, "doc", "supersede", stale, "--by", active])
    if handoff.record:
        doc = (
            f"the active progress doc `ccn doc show {active[:SHORT]}` (hand-written) and its generated sections "
            f"`ccn doc show {generated[:SHORT]}`"
        )
    else:
        doc = f"the generated handoff `ccn doc show {generated[:SHORT]}`"
    print(json.dumps({"id": active, "generated": generated, "file": str(path), "digest": digest(handoff, doc)}))
    return 0


def write_generated(handoff: Handoff, shell: ledger.Shell, repo: str, markdown: str) -> str:
    if handoff.generated:
        shell.run(["ccn", "-R", repo, "doc", "edit", handoff.generated, "--title", handoff.title, "--body", "-"], stdin=markdown)
        return handoff.generated
    when = f"Resuming or compacting the {handoff.program} drive: read before anything else, after the plan"
    argv = ["ccn", "-R", repo, "doc", "add", handoff.title, "--label", f"progress:{handoff.program}", "--when", when, "--body", "-", "--json"]
    return json.loads(shell.run(argv, stdin=markdown))["id"]


def cmd_lint(args: argparse.Namespace, shell: ledger.Shell) -> int:
    body = Path(args.file).read_text() if args.file else doc_body(shell, args.repo, args.doc)
    previous = (
        Path(args.previous_file).read_text()
        if args.previous_file
        else doc_body(shell, args.repo, args.previous_doc) if args.previous_doc else None
    )
    live = live_answer_ids(shell, args.repo)
    problems = standing.lint(lint_view(body), previous, durable_answers(shell, args.repo, args.program), live)
    if args.plan:
        problems += uncited_plan_lines(Path(args.plan).expanduser(), live)
    for problem in problems:
        print(problem)
    return standing.VIOLATIONS if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="handoff.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="write the generated progress doc and print its id, file, and restore digest")
    generate.add_argument("--program", required=True, metavar="SLUG")
    generate.add_argument("--plan", required=True, metavar="PATH")
    generate.add_argument("--inbox-dir", metavar="DIR", help="default ~/.claude/scratch/<program>/inbox")
    generate.add_argument("--ledger", metavar="ID", help="default: the drive registry's ledger for the session")
    generate.add_argument("--session", metavar="FILE", help="the hook's JSON, - for stdin: session_id, tasks, background")
    narrative = generate.add_mutually_exclusive_group()
    narrative.add_argument("--narrative-doc", metavar="ID", help="the root's freshly written progress doc")
    narrative.add_argument("--narrative-file", metavar="PATH", help="the root's freshly written progress file")
    generate.add_argument("--generated-doc", metavar="ID", help="the session's generated doc, edited in place while active")
    generate.add_argument("--fresh-since", metavar="ISO", help="the previous compaction: a hand-written doc this session wrote since stays active")
    generate.add_argument("--strict", action="store_true", help="write nothing when the narrative fails the lint")
    generate.add_argument("--folder", action="store_true", help="no cc-notes: write the progress file alone")
    generate.set_defaults(handler=cmd_generate)

    check_cmd = subparsers.add_parser("lint", help="standing-rule and owner-gate findings for a handoff and its plan")
    body = check_cmd.add_mutually_exclusive_group(required=True)
    body.add_argument("--doc", metavar="ID")
    body.add_argument("--file", metavar="PATH")
    before = check_cmd.add_mutually_exclusive_group()
    before.add_argument("--previous-doc", metavar="ID")
    before.add_argument("--previous-file", metavar="PATH")
    check_cmd.add_argument("--program", required=True, metavar="SLUG")
    check_cmd.add_argument("--plan", metavar="PATH")
    check_cmd.set_defaults(handler=cmd_lint)

    for sub in (generate, check_cmd):
        sub.add_argument("--repo", default=".", metavar="PATH")
    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    raise SystemExit(main())
