#!/usr/bin/env python3
"""Build a drive's progress record from its register, cci rules, inboxes, and live work.

    handoff.py generate --program SLUG --plan PATH [--inbox-dir DIR] [--ledger ID] [--session FILE|-]
                        [--narrative-doc ID | --narrative-file PATH] [--generated-doc ID] [--fresh-since ISO]
                        [--strict] [--folder] [--repo PATH]
    handoff.py fold     (--doc ID | --file PATH) [--repo PATH]
    handoff.py lint     (--doc ID | --file PATH) --program SLUG [--plan PATH] [--previous-doc ID | --previous-file PATH] [--repo PATH]

``generate`` reads the newest cc-notes doc labelled ``standing-rules:<program>``.
The register holds at most 30 owner-approved rules with answer ids linking to the full
rulings in cc-notes. Generation never builds or writes a register doc or file.
The progress record's ``## Standing owner rules`` section names the register doc and its
rule count; the register itself arrives verbatim after compaction. Live standing rules
come from cci on the drive named by ``--program``. Each follows as ``- #<seq> [<source>]``,
where the source is ``ccn <answer id>`` or ``cci #<seq>``. Retired rules follow.
With no register doc, the section says so.

The record also carries open owner asks, tasks, lanes, monitors, inbox state, and the
drive registry. Generation writes a progress doc under ``progress:<program>`` and the
same markdown at ``<plan-stem>-progress/<UTC>-generated.md``. It augments
``--narrative-doc`` or the newest hand-written progress doc this session created since
``--fresh-since`` or within :data:`FRESH_MINUTES`. Otherwise it edits the active
``--generated-doc`` or adds a doc. The written doc supersedes every other active progress
doc. Generation exits :data:`SEVERAL_ACTIVE` if another remains active.

The root narrative comes last. It comes verbatim from the chosen record or ``--narrative-file``,
else from the newest progress doc. A narrative that differs from the one the doc's last
generation wrote, found by its ``Generated from sources`` stamp in ``ccn doc history``,
was edited into the doc in place and is fresh like a record; an unchanged one is carried
forward. Both are folded by :func:`progress.fold`: every dump the last generation did not
write stays whole, else the last dump, binding sections are carried once, and the rest
become dated digest lines.
A record over :data:`progress.CAP` bytes writes nothing and exits :data:`OVERSIZED`
naming its largest section. ``fold`` folds an existing record in place under the same cap. ``--folder`` skips cc-notes and writes only the
progress file. ``--session`` reads the hook's JSON fields ``session_id``, ``tasks``,
and ``background`` from a file or stdin.

A standing rule missing since the previous handoff appears once as
``- <id> superseded by <id>`` or ``- <id> superseded by nothing: ...`` when no replacement
is known. Old ``R<n>`` ids retire this way once. Lint checks the standing section,
register pointer, carried rule ids, and citations for lines requiring owner approval.
Findings go under ``## Lint findings``.
With ``--strict``, register, carry, narrative, or standing-rule findings write nothing and exit
:data:`standing.VIOLATIONS`. Plan findings never block generation.

Output has fields ``{id, file, register, fresh, digest}``. ``register`` is the register
doc id or null. ``id`` is the progress doc id. Both are null in folder mode. ``fresh`` says
the root wrote the narrative for this compaction rather than generation carrying it
forward. The digest names the register first when one exists, then the progress record
and plan. Its second line reads ``Register: N owner-approved rules, M live standing rules.``

``lint`` checks any handoff and the optional plan. It exits :data:`standing.VIOLATIONS`
on a finding. Both commands use only the Python standard library.
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
import inboxes
import ledger
import progress
import rulings
import standing

DIGEST_BUDGET = 2000
RULING = re.compile(rf"^\s*(?:[-*]\s+)?\**`?({standing.ID})\b")
CATCH_UP = "Catch up with `cci digest --drive {drive}`, then `cci tail --drive {drive}`; never tail, sed, or grep a whole inbox."
RULING_CHARS = 400
TASK_CHARS = 120
SHORT = 7
FRESH_MINUTES = 30
SEVERAL_ACTIVE = 4
OVERSIZED = 5
SHOWN = 10
NARRATIVE = "## Root narrative"
FINDINGS = "## Lint findings"
GENERATED_MARK = "(generated)"
PROVENANCE = re.compile(r"^_From (.+?)(?:, carried forward)?\._\n\n")
GENERATED_BY = "Generated from sources by the long-running compaction hook at "
STAMP = re.compile(rf"^{GENERATED_BY}(\S+?)\.", re.MULTILINE)
CLOSED_ASKS = (ledger.ASK_DROPPED, ledger.ASK_ANSWERED, ledger.ASK_LIVE)
LANE_TYPES = ("subagent", "teammate", "workflow", "cloud session")


@dataclass
class Inbox:
    name: str
    head: str | None
    cursor: str | None
    last: str | None


@dataclass
class Handoff:
    program: str
    plan: str
    at: datetime
    registry: dict | None = None
    register: dict | None = None
    standing: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    superseded: dict[str, str] = field(default_factory=dict)
    retired: list[str] = field(default_factory=list)
    asks: list[str] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    lanes: list[dict] = field(default_factory=list)
    monitors: list[dict] = field(default_factory=list)
    inboxes: list[Inbox] = field(default_factory=list)
    inbox_dir: Path | None = None
    findings: list[str] = field(default_factory=list)
    plan_findings: list[str] = field(default_factory=list)
    narrative: str = ""
    narrative_from: str = ""
    history: str = ""
    narrative_edit: str = "in your next progress record"
    record: str | None = None
    generated: str | None = None
    fresh: bool = False
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
    handoff.inbox_dir = directory
    for path in sorted(directory.glob("*.md")):
        lines = [line.text for line in inboxes.Inbox(path).lines()]
        rulings = [(match[1], line.strip()) for line in lines if (match := RULING.match(line))]
        cursor = path.with_name(f"{path.name}.cursor")
        head, last = rulings[-1] if rulings else (None, None)
        handoff.inboxes.append(Inbox(name=path.name, head=head, cursor=cursor.read_text().strip() if cursor.is_file() else None, last=last))


def read_standing(handoff: Handoff, shell: ledger.Shell) -> None:
    found = standing.read(shell.run, handoff.program)
    handoff.standing, handoff.sources, handoff.superseded = found.rules, found.sources, found.superseded


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


def folded(body: str, at: datetime, history: str) -> str:
    head, marker, rest = body.partition(f"\n{NARRATIVE}\n")
    if not marker:
        return f"{progress.fold(body, at, history, body)}\n"
    provenance = match[0] if (match := PROVENANCE.match(rest.strip())) else ""
    narrative = narrative_of(body)
    return f"{head}\n{NARRATIVE}\n\n{provenance}{progress.fold(narrative, at, history, narrative)}\n"


def doc_history(doc_id: str) -> str:
    return f"`ccn doc history {doc_id[:SHORT]} --json --full`"


def carried_from(body: str, record: str) -> str:
    _, marker, rest = body.partition(f"\n{NARRATIVE}\n")
    return f"{match[1] if marker and (match := PROVENANCE.match(rest.strip())) else record}, carried forward"


def active_progress(shell: ledger.Shell, repo: str, program: str) -> list[dict]:
    return ccn_json(shell, repo, "doc", "list", "--label", f"progress:{program}") or []


def doc_body(shell: ledger.Shell, repo: str, doc_id: str) -> str:
    return ccn_json(shell, repo, "doc", "show", doc_id)["body"]


def stamp(body: str | None) -> str | None:
    return match[1] if body and (match := STAMP.search(body)) else None


def generation(shell: ledger.Shell, repo: str, doc_id: str) -> str | None:
    for entry in ccn_json(shell, repo, "doc", "history", doc_id, "--full"):
        for change in entry["changes"]:
            if change["field"] == "body" and (made := stamp(change["to"])) and made != stamp(change.get("from")):
                return change["to"]
    return None


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


def shown(lines: list[str], more: str) -> list[str]:
    return lines[:SHOWN] + ([f"- {len(lines) - SHOWN} more {more}"] if len(lines) > SHOWN else [])


def rule_lines(handoff: Handoff) -> list[str]:
    return [f"- {rid} [{handoff.sources[rid]}]" for rid in handoff.standing] + [f"- {line}" for line in handoff.retired]


def dashboard_line(registry: dict) -> str:
    server = Path(registry["state_dir"]) / drive.DASHBOARD_SERVER
    if server.exists():
        return f"- Dashboard: {drive.dashboard_url(json.loads(server.read_text()))}; give the owner this link in your next reply"
    return f"- Dashboard: not running; `live-dashboard start --dir {Path(registry['state_dir']) / drive.DASHBOARD_SERVER.parent}` starts it and prints the link for the owner"


def render(handoff: Handoff) -> str:
    registry = handoff.registry or {}
    out = [
        f"# {handoff.title}",
        "",
        f"{GENERATED_BY}{handoff.at:%Y-%m-%dT%H:%M:%SZ}. "
        f"Every section above `{NARRATIVE}` is rebuilt at each handoff; change the sources, never this doc.",
        "",
        standing.section_of(handoff.register, rule_lines(handoff), handoff.narrative),
        "## Read first",
        f"- Plan: `{handoff.plan}`",
    ]
    if registry:
        out.append(
            f"- Drive `{registry['drive']}`: ledger `{registry['ledger'][:SHORT]}`, Orca run `{registry.get('orca_run') or '-'}`, "
            f"checkout `{registry['checkout']}`, root sessions {', '.join(session[:8] for session in registry['sessions'])}"
        )
        out.append(dashboard_line(registry))
    out += ["", "## Open owner asks"] + ([f"- {line}" for line in handoff.asks] or ["- none"])
    active = [task for task in handoff.tasks if task["status"] == "in_progress"]
    out += ["", "## Open tasks"]
    out += [f"- #{task['id']} {clip(task['subject'], TASK_CHARS)}" for task in active] or ["- none in progress"]
    if pending := len(handoff.tasks) - len(active):
        out.append(f"- {pending} pending in `TaskList`")
    out += ["", "## Lanes and monitors"]
    running = [task for task in handoff.monitors + handoff.lanes[::-1] if task["status"] == "running"]
    out += shown([f"- {task['type']}: {clip(task['description'], TASK_CHARS)}" for task in running], "running lanes")
    if not running:
        out.append("- none running at the last stop")
    out += ["", "## Inboxes", ""]
    if handoff.inbox_dir:
        out += [CATCH_UP.format(drive=handoff.inbox_dir.parent.name), ""]
    out += shown(
        [
            f"- {inbox.name}: head {inbox.head or '-'}, cursor {inbox.cursor or '-'}" + (f": {clip(inbox.last, TASK_CHARS)}" if inbox.last else "")
            for inbox in handoff.inboxes
        ],
        "inboxes",
    ) or ["- no inbox directory"]
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
        f"{handoff.sources[rid]}: standing rule {rid} requires owner approval but cites no live answer id: "
        f"{clip(text, RULING_CHARS)}; post a replacement rule with `cci post --drive {handoff.program} --lane root --kind correction "
        f"--re {rid.lstrip('#')} --topic standing --ccn <answer id> --text \"<rule>\"`"
        for rid, text in handoff.standing.items()
        if standing.gated(text, live)
    ]


def uncited_narrative_lines(handoff: Handoff, live: set[str]) -> list[str]:
    return [
        f"narrative ({handoff.narrative_from}) line {number}: owner-gate line cites no live answer id: "
        f"{clip(line, RULING_CHARS)}; end that line with `(answer <id>)` {handoff.narrative_edit}"
        for number, line in enumerate(handoff.narrative.splitlines(), 1)
        if not progress.DIGEST.match(line) and standing.gated(line, live)
    ]


def check(handoff: Handoff, previous: str | None, live: set[str]) -> None:
    retire(handoff, previous)
    handoff.findings = standing.rule_findings(render(handoff), previous, handoff.register)
    handoff.findings += uncited_inbox_rules(handoff, live) + uncited_narrative_lines(handoff, live)
    handoff.plan_findings = uncited_plan_lines(Path(handoff.plan), live)


def digest(handoff: Handoff, doc: str) -> str:
    first = (
        f"read the standing rules register `ccn doc show {handoff.register['id'][:SHORT]}`: it arrives verbatim with your next "
        f"tool result, binds every lane brief, and outranks the summary. Then read {doc}"
        if handoff.register
        else f"read {doc}"
    )
    return "\n".join(
        [
            f"Compacted long-running drive `{handoff.program}`. Before acting, {first}, then `{handoff.plan}`. "
            "Reload Skill `long-running` if its rules are gone.",
            f"Register: {standing.rule_count(handoff.register)} owner-approved rules, {len(handoff.standing)} live standing rules.",
            f"Open: {len(handoff.asks)} owner asks, {len(handoff.tasks)} tasks, {len(handoff.lanes)} lanes, "
            f"{len(handoff.monitors)} monitors, {len(handoff.findings) + len(handoff.plan_findings)} lint findings.",
        ]
    )


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
    if not args.folder:
        read_standing(handoff, shell)
    if args.narrative_file:
        handoff.narrative, handoff.narrative_from = Path(args.narrative_file).read_text().strip(), f"file {Path(args.narrative_file).name}"
        handoff.narrative_edit = f"in `{args.narrative_file}`"
        handoff.fresh = True
    if args.folder:
        files = sorted(progress_folder(plan).glob("*-generated.md"), key=lambda path: path.stat().st_mtime)
        previous = files[-1].read_text() if files else None
        if previous and not args.narrative_file:
            handoff.narrative, handoff.narrative_from = narrative_of(previous), carried_from(previous, f"file {files[-1].name}")
            handoff.history = f"the earlier records in `{progress_folder(plan)}`"
        check(handoff, previous, set())
        handoff.narrative = progress.fold(handoff.narrative, handoff.at, handoff.history, handoff.narrative) if handoff.history else handoff.narrative
        return handoff, previous
    handoff.register = rulings.register(shell, args.repo, args.program)
    if ledger_id := args.ledger or (registry or {}).get("ledger"):
        handoff.asks = open_asks(shell, ledger_id)
    docs = active_progress(shell, args.repo, args.program)
    handoff.record = args.narrative_doc or fresh_record(
        shell, args.repo, docs, session.get("session_id"), fresh_cutoff(handoff.at, args.fresh_since)
    )
    handoff.generated = next((doc["id"] for doc in docs if doc["id"] == args.generated_doc), None)
    handoff.stale = [doc["id"] for doc in docs if doc["id"] != (handoff.record or handoff.generated)]
    active = [doc for doc in docs if doc["id"] != handoff.record or doc["id"] == handoff.generated]
    newest = max(active, key=lambda doc: doc["updated_at"]) if active else None
    previous = doc_body(shell, args.repo, newest["id"]) if newest else None
    earlier = ""
    if handoff.record and not args.narrative_file:
        handoff.narrative, handoff.narrative_from = narrative_of(doc_body(shell, args.repo, handoff.record)), f"doc {handoff.record[:SHORT]}"
        handoff.narrative_edit = f"via `ccn doc edit {handoff.record[:8]} --body -`"
        generated = generation(shell, args.repo, handoff.record)
        handoff.fresh = generated is None or narrative_of(generated) != handoff.narrative
    elif previous and newest and not args.narrative_file:
        generated = generation(shell, args.repo, newest["id"])
        handoff.narrative = narrative_of(previous)
        earlier = narrative_of(generated) if generated else handoff.narrative
        handoff.fresh = handoff.narrative != earlier
        if handoff.fresh:
            handoff.narrative_from = f"doc {newest['id'][:SHORT]}"
            handoff.narrative_edit = f"via `ccn doc edit {newest['id'][:8]} --body -`"
        else:
            handoff.narrative_from = carried_from(previous, f"doc {newest['id'][:SHORT]}")
        handoff.history = doc_history(newest["id"])
    check(handoff, previous, live_answer_ids(shell, args.repo))
    handoff.narrative = progress.fold(handoff.narrative, handoff.at, handoff.history, earlier) if handoff.history else handoff.narrative
    return handoff, previous


def cmd_generate(args: argparse.Namespace, shell: ledger.Shell) -> int:
    handoff, _ = build(args, shell)
    if args.strict and handoff.findings:
        print("\n".join(handoff.findings))
        return standing.VIOLATIONS
    markdown = render(handoff)
    if refusal := progress.oversized(markdown):
        print(refusal)
        return OVERSIZED
    path = progress_file(Path(handoff.plan), handoff.stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown)
    if args.folder:
        print(json.dumps({"id": None, "file": str(path), "register": None, "fresh": handoff.fresh, "digest": digest(handoff, f"the generated handoff `{path}`")}))
        return 0
    written = write_progress(handoff, shell, args.repo, markdown)
    for stale in handoff.stale:
        shell.run(["ccn", "-R", args.repo, "doc", "supersede", stale, "--by", written])
    if (active := [doc["id"] for doc in active_progress(shell, args.repo, args.program)]) != [written]:
        print(
            f"{len(active)} active progress:{args.program} docs after generation, expected only {written[:SHORT]}: "
            f"{', '.join(doc[:SHORT] for doc in active)}; supersede every other with `ccn doc supersede <id> --by {written[:SHORT]}`"
        )
        return SEVERAL_ACTIVE
    doc = f"the progress doc `ccn doc show {written[:SHORT]}`"
    register = handoff.register["id"] if handoff.register else None
    print(json.dumps({"id": written, "file": str(path), "register": register, "fresh": handoff.fresh, "digest": digest(handoff, doc)}))
    return 0


def write_progress(handoff: Handoff, shell: ledger.Shell, repo: str, markdown: str) -> str:
    if handoff.record:
        shell.run(["ccn", "-R", repo, "doc", "edit", handoff.record, "--body", "-"], stdin=markdown)
        return handoff.record
    if handoff.generated:
        shell.run(["ccn", "-R", repo, "doc", "edit", handoff.generated, "--title", handoff.title, "--body", "-"], stdin=markdown)
        return handoff.generated
    when = f"Resuming or compacting the {handoff.program} drive: read before anything else, after the plan"
    argv = ["ccn", "-R", repo, "doc", "add", handoff.title, "--label", f"progress:{handoff.program}", "--when", when, "--body", "-", "--json"]
    return json.loads(shell.run(argv, stdin=markdown))["id"]


def cmd_fold(args: argparse.Namespace, shell: ledger.Shell) -> int:
    at = datetime.now(timezone.utc)
    if args.file:
        path = Path(args.file).expanduser()
        before = path.read_text()
        after = folded(before, at, f"the earlier records in `{path.parent}`")
    else:
        before = doc_body(shell, args.repo, args.doc)
        after = folded(before, at, doc_history(args.doc))
    if refusal := progress.oversized(after):
        print(refusal)
        return OVERSIZED
    if args.file:
        path.write_text(after)
    else:
        shell.run(["ccn", "-R", args.repo, "doc", "edit", args.doc, "--body", "-"], stdin=after)
    print(f"folded {args.file or args.doc[:SHORT]}: {len(before.encode())} -> {len(after.encode())} bytes")
    return 0


def cmd_lint(args: argparse.Namespace, shell: ledger.Shell) -> int:
    body = Path(args.file).read_text() if args.file else doc_body(shell, args.repo, args.doc)
    previous = (
        Path(args.previous_file).read_text()
        if args.previous_file
        else doc_body(shell, args.repo, args.previous_doc) if args.previous_doc else None
    )
    live = live_answer_ids(shell, args.repo)
    problems = standing.lint(lint_view(body), previous, rulings.register(shell, args.repo, args.program), live)
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
    generate.add_argument("--fresh-since", metavar="ISO", help="the previous compaction: a hand-written doc this session wrote since is augmented in place")
    generate.add_argument("--strict", action="store_true", help="write nothing when the narrative fails the lint")
    generate.add_argument("--folder", action="store_true", help="no cc-notes: write the progress file alone")
    generate.set_defaults(handler=cmd_generate)

    fold = subparsers.add_parser("fold", help="fold a progress record's narrative in place: last dump whole, binding sections once")
    record = fold.add_mutually_exclusive_group(required=True)
    record.add_argument("--doc", metavar="ID")
    record.add_argument("--file", metavar="PATH")
    fold.set_defaults(handler=cmd_fold)

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

    for sub in (generate, fold, check_cmd):
        sub.add_argument("--repo", default=".", metavar="PATH")
    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    raise SystemExit(main())
