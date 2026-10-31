from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, drive_entry, judged, rulings, script, script_module
from .lane_landing import DRIVE_ENV, LANDERS, lane_name, named_repo, without_repo
from .register_injection import drive_args
from .ruling_misread import CLAUDE, FLAGGED, MATCH, MATCHED, answer, candidate_rows
from .tests.register_fixtures import register
from .tests.refusal_fixtures import (
    CARD_TERMS,
    CARD_TERMS_KIND,
    CITED,
    DESK_RUNNER_ON_BASE,
    DURATION_FIELDS,
    E2E_AND_HARNESS,
    FAILS_OPEN,
    HELPER_PACKAGE_CLOCK,
    HELPER_TRANSCRIPT,
    HEREDOC_BODY,
    HOOK_CAP,
    LAUNCH_STALLED,
    LOGGED_STATE,
    NEW_SENTINEL,
    NO_DRIVE,
    ON_BASE,
    PLAIN,
    REFUSED_IDENTIFIERS,
    RELISTED,
    RETIRE_AND_RECONVERT,
    RUST_TESTS_RS,
    SENTINEL_ONLY,
    TEST_ONLY,
    TREE,
    UNCITED,
    UPLOAD_BUDGET,
    sources,
)

ACTIVE = [CompactionState(active=True)]
LANE = "alane-a-0123456789abcdef"
READ_TIMEOUT_SECONDS = 20
SHOWN = 12
TEXT_SHOWN = 160
SHIP_BODY = ("--pr-body-file",)
SHIP_VALUES = frozenset(
    {
        "-m", "--message", "--bookmark", "--branch", "--budget", "--expect-remote", "--landed", "--new-pr", "--only-hunk",
        "--parent", "--pr-body-file", "--pr-title", "--skip-hunk",
    }
)
SHIP_OPTIONAL_VALUES = frozenset({"--new-branch", "--create"})
GH_HEAD = ("--head", "-H")
GH_BODY = ("--body", "-b")
GH_BODY_FILE = ("--body-file", "-F")
GH_EDIT_VALUES = frozenset(
    {
        "--body", "-b", "--body-file", "-F", "--title", "-t", "--base", "-B", "--milestone", "-m", "--repo", "-R",
        "--add-label", "--remove-label", "--add-reviewer", "--remove-reviewer", "--add-assignee", "--remove-assignee",
        "--add-project", "--remove-project",
    }
)
TRUNK = ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
UNTRACKED = ["git", "ls-files", "--others", "--exclude-standard", "--full-name", "-z"]
TOPLEVEL = ["git", "rev-parse", "--show-toplevel"]
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
EXEMPT = re.compile(rf"{script_module('testfiles').TEST_FILE.pattern}|\.(?:md|mdx|txt|rst|lock|sum)$|(?:^|/)package-lock\.json$")
COMMENT = re.compile(r"^\s*(?:#|//|--\s|/\*|\*\s|\*$)")
TEST_LINE = re.compile(r"\b(?:Input|Block|Allow|Warn|Ask|Rewrite)\(")
KIND = re.compile(r"""["'`](?P<name>[A-Z][A-Z0-9]+(?:-[A-Z][A-Z0-9]+)+)["'`]""")
SENTINEL = re.compile(r"\b(?P<name>err[A-Z]\w*)\s*:?=\s*(?:errors\.New|fmt\.Errorf)\(")
RETRY = re.compile(r"\b(?P<name>\w*(?:retr(?:y|ies|ied|ying)|backoff)\w*)", re.IGNORECASE)
CLOCK = re.compile(
    r"(?P<name>\btime\.(?:[Ss]leep|NewTicker|NewTimer|Tick|After|AfterFunc)\b|\basyncio\.sleep\b|\bthreading\.Timer\b"
    r"|\bset(?:Interval|Timeout)\b|\btimedelta\(|\*\s*time\.(?:Second|Minute|Hour|Millisecond)\b|\bsleep\s+\d)"
)
BUDGET = re.compile(
    r"""\b(?!max_fires\b)(?P<name>\w*(?:max|limit|budget|timeout|deadline|ttl|threshold|window|interval|seconds|minutes|hours|attempts|cooldown|grace)\w*)"""
    r"""["']?\s*(?::[^=\n]*)?[:=]\s*-?\d""",
    re.IGNORECASE,
)
ASSIGNED = re.compile(r"""^\s*(?:(?:const|var|let|export|final)\s+)*["']?(?P<name>[A-Za-z_]\w+)["']?\s*(?::[^=\n]*)?(?::=|=|:)(?!=)""")
DECLARING = frozenset(":,]}")
PLAN_LINES = 12
PLAN_LINE_CHARS = 300
NO_RULINGS = "No ruling of his was found that bears on this pull request."
PLAN_SAID = "The plan's lines that name these mechanisms, verbatim:"
MECHANISM_WARNING = (
    "Warning, nothing was blocked: a judge reads this PR as adding a refusal, retry, timer or wait that neither the owner's words nor the plan name."
    " Remove it, or bring him the case for it with `cci post --kind ask --to root`."
)
BODY_WARNING = (
    "Warning, nothing was blocked: a judge reads this PR's body as leaving out a fix the diff puts off and why, or what the diff builds for the owner in words he follows."
    " Say both in the body."
)
CARRIED = {**FLAGGED, "verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "It is named."}
ROWS = f"{sys.executable} {script('rulings.py')} rows --drive 900424b6"
PLAN = f"{sys.executable} {script('rulings.py')} plan --drive 900424b6"
JUDGED = {ROWS: register("quiet"), MATCH: MATCHED, PLAN: "", CLAUDE: answer(FLAGGED)}


@dataclass(frozen=True)
class Finding:
    kind: str
    name: str
    path: str
    line: int
    text: str

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.kind} {self.name}: {self.text.strip()[:TEXT_SHOWN]}"


@dataclass(frozen=True)
class Request:
    raw: str
    cwd: Path | None
    gh: tuple[str, ...]
    base: str | None
    bodies: tuple[str, ...]
    texts: tuple[str, ...]
    edit: tuple[str, ...] | None
    worktree: bool
    paths: tuple[str, ...] = ()
    head: str | None = None
    stdin: str | None = None


def run(argv: list[str], cwd: Path | None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read(argv: list[str], cwd: Path | None) -> str | None:
    done = run(argv, cwd)
    return done.stdout if done is not None and done.returncode == 0 else None


def parsed(text: str | None) -> dict | None:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def text_of(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def values(args: tuple[str, ...], flags: tuple[str, ...]) -> list[str]:
    found = [value for flag, value in zip(args, args[1:], strict=False) if flag in flags]
    return found + [arg.split("=", 1)[1] for arg in args for flag in flags if flag.startswith("--") and arg.startswith(f"{flag}=")]


def ship_paths(args: tuple[str, ...]) -> tuple[str, ...]:
    paths, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            paths.extend(args[index + 1 :])
            break
        if arg in SHIP_VALUES or (arg in SHIP_OPTIONAL_VALUES and args[index + 1 : index + 2] and not args[index + 1].startswith("-")):
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            paths.append(arg)
            index += 1
    return tuple(paths)


def in_scope(req: Request, path: str) -> bool:
    return not req.paths or any(path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/") for scope in req.paths)


def selector(args: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(args):
        if args[index] in GH_EDIT_VALUES:
            index += 2
        elif args[index].startswith("-"):
            index += 1
        else:
            return (args[index],)
    return ()


def stdin_file(call: Call) -> str | None:
    return next((redirect.target for redirect in call.redirects if redirect.op == "<" and redirect.fd in (None, 0)), None)


def request(evt: BaseHookEvent, call: Call) -> Request | None:
    cwd = call.cwd or evt.cwd
    if call.name == "ccx" and call.args[:2] == ("vcs", "ship") and "--no-pr" not in call.args:
        parent = values(call.args, ("--parent",))
        return Request(
            str(evt.command), cwd, (), parent[0] if parent else None, tuple(values(call.args, SHIP_BODY)), (), None, True, ship_paths(call.args[2:]),
            stdin=stdin_file(call),
        )
    argv = without_repo((call.name, *call.args)) if call.name == "gh" else ()
    repo = ("-R", found) if (found := named_repo(call)) else ()
    if argv[:3] == ("gh", "pr", "create"):
        base, head = values(argv, ("--base", "-B")), values(argv, GH_HEAD)
        return Request(
            str(evt.command), cwd, repo, base[0] if base else None, tuple(values(argv, GH_BODY_FILE)), tuple(values(argv, GH_BODY)), None, False,
            head=head[0] if head else None, stdin=stdin_file(call),
        )
    if argv[:3] == ("gh", "pr", "edit") and (values(argv, GH_BODY) or values(argv, GH_BODY_FILE)):
        return Request(
            str(evt.command), cwd, repo, None, tuple(values(argv, GH_BODY_FILE)), tuple(values(argv, GH_BODY)), selector(argv[3:]), False,
            stdin=stdin_file(call),
        )
    return None


def lane_prs(evt: BaseHookEvent) -> list[Request]:
    lane = lane_name(evt)
    if (lane and LANDERS.match(lane)) or not (lane or drive_entry(evt)):
        return []
    return [found for call in evt.cmd.calls() if (found := request(evt, call))]


def added(diff: str) -> Iterator[tuple[str, int, str]]:
    path, line = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
        elif hunk := HUNK.match(raw):
            line = int(hunk["start"])
        elif raw.startswith("+") and path:
            yield path, line, raw[1:]
            line += 1
        elif raw.startswith(" "):
            line += 1


def removed(diff: str) -> Iterator[tuple[str, str]]:
    path = ""
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
        elif raw.startswith("-") and not raw.startswith("--- ") and path:
            yield path, raw[1:]


def toplevel(req: Request) -> Path | None:
    found = (read(TOPLEVEL, req.cwd) or "").strip() if req.worktree else ""
    return Path(found) if found else None


def untracked(cwd: Path | None, root: Path) -> Iterator[tuple[str, int, str]]:
    for name in filter(None, (read(UNTRACKED, cwd) or "").split("\0")):
        if not EXEMPT.search(name) and (content := text_of(root / name)) is not None:
            yield from ((name, number, text) for number, text in enumerate(content.splitlines(), start=1))


def pr_view(req: Request) -> dict | None:
    found = read(["gh", "pr", "view", *req.gh, "--json", "baseRefName,body"], req.cwd)
    return None if found is None else parsed(found)


def base_ref(req: Request, view: dict) -> str:
    return f"origin/{branch}" if (branch := req.base or view.get("baseRefName")) else (read(TRUNK, req.cwd) or "").strip()


def diff_text(req: Request, base: str) -> str | None:
    if req.edit is not None:
        return read(["gh", "pr", "diff", *req.edit, *req.gh], req.cwd) or ""
    if not base:
        return None
    span = ["--merge-base", base] if req.worktree else [f"{base}...{req.head or 'HEAD'}"]
    return read(["git", "diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "--unified=0", *span], req.cwd) or ""


def new_findings(req: Request, diff: str, base: str) -> list[Finding]:
    root = toplevel(req)
    lines = [*added(diff), *(untracked(req.cwd, root) if root else ())]
    kept = {(path, finding.name) for path, text in removed(diff) for finding in classify(path, 0, text)}
    found = [finding for path, line, text in lines if in_scope(req, path) for finding in classify(path, line, text) if (path, finding.name) not in kept]
    on_base = {path: read(["git", "show", f"{base}:{path}"], req.cwd) or "" for path in {finding.path for finding in found}}
    return [finding for finding in found if not mentions(on_base[finding.path], finding.name)]


def named(text: str, match: re.Match[str]) -> str:
    if found := ASSIGNED.match(text):
        return found["name"]
    return (match.groupdict().get("name") or match.group(0)).strip("\"'` ")


def kind_of(name: str) -> str:
    return "quarantine kind" if "quarantin" in name.casefold() else "refusal kind"


def declared(text: str, match: re.Match[str]) -> bool:
    return text[match.end() :].lstrip()[:1] in DECLARING


def classify(path: str, line: int, text: str) -> list[Finding]:
    if EXEMPT.search(path) or COMMENT.match(text) or (path.endswith(".py") and TEST_LINE.search(text)):
        return []
    if kinds := [Finding(kind_of(match["name"]), match["name"], path, line, text) for match in KIND.finditer(text) if declared(text, match)]:
        return kinds
    if match := SENTINEL.search(text):
        return [Finding(kind_of(match["name"]), match["name"], path, line, text)]
    for kind, pattern in (("retry", RETRY), ("clock", CLOCK), ("budget", BUDGET)):
        if match := pattern.search(text):
            return [Finding(kind, named(text, match), path, line, text)]
    return []


def body_file(req: Request, value: str) -> str | None:
    if value == "-":
        if req.stdin:
            return text_of(Path(req.cwd or ".") / req.stdin)
        return req.raw if "<<" in req.raw else None
    candidates = (Path(req.cwd or ".") / part for part in dict.fromkeys((value, value.split("=", 1)[-1])))
    return next((text for path in candidates if path.is_file() and (text := text_of(path)) is not None), None)


def body_text(req: Request, view: dict) -> str | None:
    if not (req.bodies or req.texts):
        return str(view.get("body") or "")
    files = [body_file(req, value) for value in req.bodies]
    return None if None in files else "\n".join([*req.texts, *files])


def mentions(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.IGNORECASE))


def evidence(findings: list[Finding]) -> str:
    shown = [finding.render() for finding in findings[:SHOWN]]
    more = [f"... {len(findings) - SHOWN} more"] if len(findings) > SHOWN else []
    return "\n".join(["```", *shown, *more, "```"])


def owner_evidence(evt: BaseHookEvent, drive: list[str], text: str) -> str:
    judge, env = script_module("long_running/judge"), reqenv.env_map()
    state, rows = candidate_rows(evt, drive, text)
    return "\n\n".join(judge.owner_words(key, judge.said(row, state, env)) for key, row in rows.items()) or NO_RULINGS


def plan_lines(evt: BaseHookEvent, drive: list[str], findings: list[Finding]) -> list[str]:
    found = rulings(str(evt.cwd), "plan", *drive).strip()
    if not found or not (plan := Path(found)).is_file():
        return []
    names = {finding.name for finding in findings} | {Path(finding.path).name for finding in findings}
    lines = enumerate(plan.read_text(errors="replace").splitlines(), start=1)
    return [f"{number}: {line.strip()[:PLAN_LINE_CHARS]}" for number, line in lines if any(mentions(line, name) for name in names)][:PLAN_LINES]


def changed_files(diff: str) -> str:
    counts: dict[str, list[int]] = {}
    path = ""
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
            counts.setdefault(path, [0, 0])
        elif path and raw.startswith(("+", "-")) and not raw.startswith("--- "):
            counts[path][raw.startswith("-")] += 1
    return "\n".join(f"{name} +{added} -{gone}" for name, (added, gone) in counts.items())


def read_pr(req: Request) -> tuple[str, str, str] | None:
    view = pr_view(req) if req.edit is None and (req.base is None or not (req.bodies or req.texts)) else {}
    if view is None and not (req.bodies or req.texts):
        return None
    base = base_ref(req, view or {})
    if (diff := diff_text(req, base)) is None or (body := body_text(req, view or {})) is None:
        return None
    return diff, base, body


def mechanisms_named(evt: BaseHookEvent, req: Request) -> HookResult | None:
    if not (drive := drive_args(evt)) or not (read := read_pr(req)) or not (findings := new_findings(req, *read[:2])):
        return None
    body, shown = read[2], "\n".join(finding.render() for finding in findings[:SHOWN])
    plan = plan_lines(evt, drive, findings)
    said = owner_evidence(evt, drive, f"{shown}\n{body}") + (f"\n\n{PLAN_SAID}\n" + "\n".join(plan) if plan else "")
    if not (found := judged("mechanism", said, f"The lines it adds:\n{shown}\n\nIts body:\n{body}", drive[1])):
        return None
    return evt.context("\n".join([MECHANISM_WARNING, found, evidence(findings)]))


def body_names_what_it_defers(evt: BaseHookEvent, req: Request) -> HookResult | None:
    if not (req.bodies or req.texts) or not (drive := drive_args(evt)) or not (read := read_pr(req)):
        return None
    diff, base, body = read
    findings = new_findings(req, diff, base)
    stand_ins = "\n\nLines it adds that may stand in for a fix:\n" + "\n".join(finding.render() for finding in findings[:SHOWN]) if findings else ""
    said = f"{owner_evidence(evt, drive, body)}\n\nWhat the diff changes:\n{changed_files(diff)}{stand_ins}"
    found = judged("pr-body", said, body, drive[1])
    return evt.context("\n".join([BODY_WARNING, found])) if found else None


def ship(command: str, verdict: dict = FLAGGED, **extra: object) -> Input:
    commands = JUDGED | {CLAUDE: answer(verdict)} | extra.pop("commands", {})
    return Input(**({"command": command, "agent_id": LANE, "state": ACTIVE, "env": DRIVE_ENV, "cwd": str(TREE), "commands": commands} | extra))


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    max_fires=1,
    tests={
        ship(f"ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Warn(
            pattern=r"^Warning, nothing was blocked: a judge reads this PR as adding a refusal, retry, timer or wait[\s\S]*Artifact: \"per destination and key\""
            r"[\s\S]*desk-runner\.py:369 refusal kind LAUNCH-STALLED[\s\S]*STALL_SECONDS"
        ),
        ship(f"ccx vcs ship --pr-body-file {CITED}", CARRIED, commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file p1-hooks/stall={CITED}", commands=sources(LAUNCH_STALLED)): Warn(pattern=r"LAUNCH-STALLED"),
        ship(f"gh pr edit 1 --body-file - < {CITED}", commands=sources(LAUNCH_STALLED)): Warn(pattern=r"LAUNCH-STALLED"),
        ship(HEREDOC_BODY, commands=sources(LAUNCH_STALLED)): Warn(pattern=r"LAUNCH-STALLED"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(UPLOAD_BUDGET)): Warn(
            pattern=r"upload\.go:41 clock uploadBudget[\s\S]*upload\.go:44 clock ticker"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(SENTINEL_ONLY)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RETIRE_AND_RECONVERT)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(NEW_SENTINEL)): Warn(pattern=r"view\.go:4 refusal kind errReadOnly"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(DURATION_FIELDS)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(HELPER_PACKAGE_CLOCK)): Allow(),
        ship(f"ccx vcs ship -m 'drive: digest' plugins/long-running/skills/long-running/scripts/drive.py --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship -m 'runner: page' plugins/long-running/skills/long-running/scripts --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Warn(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr="not json")): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LOGGED_STATE)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}.missing", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"gh pr edit 1 --body-file {CITED} && gh pr edit 2 --body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Warn(pattern=r"LAUNCH-STALLED"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(TEST_ONLY)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(CARD_TERMS)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(CARD_TERMS_KIND)): Warn(
            pattern=r"\A(?![\s\S]*card_terms\.py:1[34] )[\s\S]*card_terms\.py:3 refusal kind CARD-STALE"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(PLAIN)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(HOOK_CAP)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RELISTED)): Warn(
            pattern=r"\A(?![\s\S]*refusal kind (?:UNBOUND|LAUNCH-STALLED))[\s\S]*refusal kind LAUNCH-DUPLICATE"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(FAILS_OPEN)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(ON_BASE, base=DESK_RUNNER_ON_BASE)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(ON_BASE)): Warn(pattern=r"refusal kind MERGE-ASK"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(E2E_AND_HARNESS)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(REFUSED_IDENTIFIERS)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RUST_TESTS_RS)): Allow(),
        ship(f"ccx vcs ship --no-pr --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr=(1, "GraphQL: API rate limit exceeded"))): Allow(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr='{"baseRefName": "main", "body": ""}')): Warn(),
        ship(f"gh pr create --base main --title 'page a stalled launch' --body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Warn(pattern=r"LAUNCH-STALLED"),
        ship(f"gh pr edit 101 -R acme/service --body-file {UNCITED}", commands=sources(NEW_SENTINEL)): Warn(),
        ship("gh pr edit 101 --add-label docs", commands=sources(NEW_SENTINEL)): Allow(),
        ship(f"git diff && echo {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED) | {CLAUDE: (1, "model unavailable")}): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", agent_id="a1b2c3d4e5f60718", transcript=HELPER_TRANSCRIPT, state=[], commands=sources(LAUNCH_STALLED)): Warn(
            pattern=r"LAUNCH-STALLED"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", agent_id="alanding-desk-0123456789abcdef", commands=sources(LAUNCH_STALLED)): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {UNCITED}", env=NO_DRIVE, commands=sources(LAUNCH_STALLED)): Allow(),
    },
)
def refusal_kinds_need_owner_answer(evt: BaseHookEvent) -> HookResult | None:
    return next((result for req in lane_prs(evt) if (result := mechanisms_named(evt, req)) is not None), None)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    max_fires=1,
    tests={
        ship(f"ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Warn(
            pattern=r"^Warning, nothing was blocked: a judge reads this PR's body as leaving out a fix[\s\S]*Artifact: \"per destination and key\""
        ),
        ship(f"ccx vcs ship --pr-body-file {CITED}", CARRIED, commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(TEST_ONLY)): Warn(pattern=r"body as leaving out a fix"),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr='{"baseRefName": "main", "body": ""}')): Allow(),
        ship(f"gh pr edit 1 --body-file - < {CITED}", commands=sources(LAUNCH_STALLED)): Warn(pattern=r"body as leaving out a fix"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}.missing", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED) | {CLAUDE: (1, "model unavailable")}): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {UNCITED}", env=NO_DRIVE, commands=sources(LAUNCH_STALLED)): Allow(),
    },
)
def pr_bodies_name_what_they_defer(evt: BaseHookEvent) -> HookResult | None:
    return next((result for req in lane_prs(evt) if (result := body_names_what_it_defers(evt, req)) is not None), None)
