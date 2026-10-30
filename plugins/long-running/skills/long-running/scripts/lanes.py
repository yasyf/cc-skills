"""Answer what code a lane holds: every worktree it owns, and each one's branch, dirty files, commits ahead of trunk, last commit, push state, and PRs.

A lane's worktrees come from four places, deduplicated by path: its Orca launch receipt
``<receipts>/<lane>.worktree``; its own cci records, a ``worktree`` attr or an absolute
worktree path in the text of a ``WORKTREE``, ``FIRST-REPORT``, ``started``, or any other record;
``git worktree list`` in the drive's checkout, a worktree whose directory or branch is the lane's
name; and the head branch of each PR the lane's records name. Only paths that exist count.
A lane with no worktree on this machine, such as an Orca worker on a paired remote server, is
read from the drive's Orca Run instead: ``orca_status`` matches the lane to the Run's tasks by
title, display name, or the lane its spec opens with, and reports each matching dispatch. A drive
that recorded no Run reads the newest Run its root terminal coordinates. A lane that is an
in-process teammate of the drive's root sessions also gets a line: ``teammate_status`` finds it in Claude
Code's team config ``teams/session-<id prefix>/config.json`` and reads its state from the tail of its
transcript ``projects/*/<session>/subagents/agent-*.jsonl``.
Every call goes through ``run``, which returns stdout and raises CalledProcessError on a
nonzero exit. STDLIB ONLY.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cci
import ghread

Run = Callable[[list[str]], str]
WORKTREE_PATH = re.compile(r"(?<![\w/])(/[\w.~/-]*/(?:worktrees|workspaces)/[\w.-]+(?:/[\w.-]+)*)")
DIRTY_NAMED = 5
PULLS_SHOWN = 3
SHORT = 10
PR_REF = re.compile(r"(?:^|#)(\d+)$")
CHECK_FAILED = frozenset({"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"})
CHECK_PASSED = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
ROLE = re.compile(r"^ccx:(?:.*[ \t])?role=([\w-]+)", re.MULTILINE)
TITLE_ROLES = frozenset({"evidence", "proof", "closeout", "audit"})
NOT_FIX_ROLES = frozenset({"evidence", "proof", "closeout", "desk", "audit"})
DISPATCH = "ctx_"
FOLLOW_UP = " follow-up"
SPEC_LANE = re.compile(r"^(?:Lane|Follow-up for lane) ([\w.-]+)[:\s]")
IN_PROGRESS = "in_progress"
HEARTBEAT = "heartbeat"
INBOX_READ = 500
TEAMMATE = "in_process_teammate"
TEAMMATE_STALLED = timedelta(hours=1)
TRANSCRIPT_TAIL = 1 << 20
TURN_ENTRIES = frozenset({"assistant", "user"})


@dataclass
class Worktree:
    path: Path
    repo: str = ""
    sources: list[str] = field(default_factory=list)
    branch: str = ""
    dirty: list[str] = field(default_factory=list)
    ahead: int | None = None
    commit: str = ""
    commit_minutes: int | None = None
    pushed: str = "not pushed"
    pr: str = ""
    error: str = ""

    def line(self) -> str:
        if self.error:
            return f"- {self.repo}: {self.path} ({', '.join(self.sources)}): {self.error}"
        more = f" and {len(self.dirty) - DIRTY_NAMED} more" if len(self.dirty) > DIRTY_NAMED else ""
        dirty = f"{len(self.dirty)} dirty file{'' if len(self.dirty) == 1 else 's'}" + (f": {', '.join(self.dirty[:DIRTY_NAMED])}{more}" if self.dirty else "")
        ahead = "ahead unknown" if self.ahead is None else f"{self.ahead} ahead of trunk"
        commit = f"last commit {self.commit_minutes} min ago {self.commit}" if self.commit_minutes is not None else "no commit"
        return f"- {self.repo}: {self.path} ({', '.join(self.sources)}): {self.branch}, {dirty}, {ahead}, {commit}, {self.pushed}, {self.pr or 'no PR'}"


@dataclass
class OrcaWorker:
    dispatch: str
    task: str = ""
    environment: str = ""
    worktree: str = ""
    branch: str = ""
    stage: str = ""
    outcome: str = ""
    liveness: str = ""
    updated_minutes: int | None = None
    heartbeat_minutes: int | None = None
    terminal: str = ""
    message: str = ""
    error: str = ""

    def line(self) -> str:
        where = f"{self.worktree or 'no worktree'} on {self.environment or 'this machine'}"
        source = f"Orca dispatch {self.dispatch}" + (f', task "{self.task}"' if self.task else "")
        if self.error:
            return f"- {where} ({source}): {self.error}"
        updated = "updated unknown" if self.updated_minutes is None else f"updated {self.updated_minutes} min ago"
        heartbeat = "no heartbeat" if self.heartbeat_minutes is None else f"heartbeat {self.heartbeat_minutes} min ago"
        return f"- {where} ({source}): {self.branch or 'no branch'}, stage {self.stage}, {self.outcome}, {self.liveness}, {updated}, {heartbeat}, terminal {self.terminal or 'none'}, last message {self.message or 'none'}"


def output(run: Run, argv: list[str]) -> str | None:
    try:
        return run(argv)
    except subprocess.CalledProcessError:
        return None


def record_paths(records: Iterable[dict]) -> list[str]:
    paths: list[str] = []
    for record in records:
        if attr := ((record.get("fields") or {}).get("attrs") or {}).get("worktree"):
            paths.append(attr)
        paths += WORKTREE_PATH.findall(record.get("text") or "")
    return paths


def record_prs(records: Iterable[dict], repo: str) -> list[tuple[str, str]]:
    """Each PR the records name, as its repository and number; a bare `#<n>` is `repo`'s."""
    return list(dict.fromkeys((str(pr).rpartition("#")[0] or repo, match[1]) for record in records for pr in (record.get("refs") or {}).get("prs") or [] if (match := PR_REF.search(str(pr)))))


def listed_worktrees(run: Run, checkout: Path) -> dict[str, str]:
    """Each worktree of the checkout's repository mapped to its branch, empty when detached."""
    listing = output(run, ["git", "-C", str(checkout), "worktree", "list", "--porcelain"]) or ""
    found: dict[str, str] = {}
    for block in listing.strip().split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if "worktree" in fields:
            found[fields["worktree"]] = fields.get("branch", "").removeprefix("refs/heads/")
    return found


def pool_worktrees(run: Run, checkout: Path) -> dict[str, tuple[str, str]]:
    """Each worktree of the drive's checkout and of every repository with a working copy under `~/.claude/worktrees`, mapped to its branch and its repository's name."""
    pool = Path.home() / ".claude" / "worktrees"
    anchors = [checkout, *(next((child for child in repo.iterdir() if (child / ".git").exists()), None) for repo in sorted(pool.iterdir()) if repo.is_dir())] if pool.is_dir() else [checkout]
    found: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    for anchor in filter(None, anchors):
        common = (output(run, ["git", "-C", str(anchor), "rev-parse", "--path-format=absolute", "--git-common-dir"]) or "").strip()
        if not common or common in seen:
            continue
        seen.add(common)
        for path, branch in listed_worktrees(run, anchor).items():
            found.setdefault(path, (branch, repo_name(Path(path), pool, Path(common).parent.name)))
    return found


def repo_name(path: Path, pool: Path, fallback: str) -> str:
    """The pool folder a worktree sits in, `~/.claude/worktrees/<repo>/<lane>`, else `fallback`."""
    return path.relative_to(pool).parts[0] if path.is_relative_to(pool) else fallback


def lane_worktrees(lane: str, records: list[dict], receipts: Iterable[Path], checkout: Path, repo: str, run: Run, excluded: frozenset[str] = frozenset()) -> list[Worktree]:
    """Every existing worktree the lane owns in any repository, each naming the sources that found it; `excluded` paths, such as the landing runner's own checkout, are never a lane's."""
    found: dict[str, Worktree] = {}
    listed = pool_worktrees(run, checkout)
    skip = {str(Path(path).expanduser().resolve()) for path in excluded}

    def add(path: str, source: str) -> None:
        resolved = str(Path(path).expanduser())
        if Path(resolved).is_dir() and str(Path(resolved).resolve()) not in skip:
            name = listed[resolved][1] if resolved in listed else repo_name(Path(resolved), Path.home() / ".claude" / "worktrees", Path(resolved).parent.name)
            found.setdefault(resolved, Worktree(Path(resolved), name)).sources.append(source)

    for receipt in receipts:
        if (file := receipt / f"{lane}.worktree").is_file():
            add(file.read_text().strip(), "launch receipt")
    for path in record_paths(records):
        add(path, "cci record")
    for path, (branch, _) in listed.items():
        if Path(path).name == lane or branch == lane or branch.endswith(f"/{lane}"):
            add(path, "git worktree list")
    for pr_repo, pr in record_prs(records, repo) if repo else []:
        head = json.loads(output(run, ghread.read("pr", "view", pr, "--repo", pr_repo, "--json", "headRefName")) or "{}").get("headRefName")
        for path, (branch, _) in listed.items():
            if head and branch == head:
                add(path, f"PR #{pr} head")
    for worktree in found.values():
        worktree.sources = list(dict.fromkeys(worktree.sources))
    return list(found.values())


def ci_state(rollup: list[dict]) -> str:
    states = [check.get("conclusion") or check.get("state") or check.get("status") or "" for check in rollup]
    if any(state in CHECK_FAILED for state in states):
        return "CI red"
    if all(state in CHECK_PASSED for state in states):
        return "CI green"
    return "CI pending"


def inspect(worktree: Worktree, trunk: str, repo: str, now: datetime, run: Run) -> Worktree:
    """Fill in the worktree's branch, dirty files, commits ahead of `origin/<trunk>`, last commit, whether the origin branch holds HEAD, and the branch's newest PRs with an open one's CI state."""
    path = str(worktree.path)
    status = output(run, ["git", "-C", path, "status", "--short", "--branch"])
    if status is None:
        worktree.error = "git status failed"
        return worktree
    head, *dirty = status.splitlines() or [""]
    worktree.branch = head.removeprefix("## ")
    worktree.dirty = [line[3:] for line in dirty]
    ahead = output(run, ["git", "-C", path, "rev-list", "--count", f"origin/{trunk}..HEAD"])
    worktree.ahead = int(ahead) if ahead and ahead.strip().isdigit() else None
    last = (output(run, ["git", "-C", path, "log", "-1", "--format=%ct %h %s"]) or "").strip()
    stamp, _, subject = last.partition(" ")
    if stamp.isdigit():
        worktree.commit, worktree.commit_minutes = subject, int((now - datetime.fromtimestamp(int(stamp), timezone.utc)).total_seconds() // 60)
    branch = head.removeprefix("## ").split("...")[0]
    if branch and not branch.startswith("HEAD "):
        remote = (output(run, ["git", "-C", path, "ls-remote", "origin", f"refs/heads/{branch}"]) or "").split()
        local = (output(run, ["git", "-C", path, "rev-parse", "HEAD"]) or "").strip()
        worktree.pushed = "no origin branch" if not remote else "pushed" if remote[0] == local else f"HEAD not pushed, origin at {remote[0][:SHORT]}"
    if branch and repo and not branch.startswith("HEAD "):
        pulls = json.loads(output(run, ghread.read("pr", "list", "--repo", repo, "--head", branch, "--state", "all", "--limit", str(PULLS_SHOWN), "--json", "number,state,statusCheckRollup")) or "[]")
        worktree.pr = ", ".join(f"PR #{pull['number']} {pull['state'].lower()}" + (f" {ci_state(pull.get('statusCheckRollup') or [])}" if pull["state"] == "OPEN" else "") for pull in pulls)
    return worktree


def lane_status(lane: str, records: list[dict], receipts: Iterable[Path], checkout: Path, repo: str, trunk: str, now: datetime, run: Run, excluded: frozenset[str] = frozenset()) -> list[Worktree]:
    return [inspect(worktree, trunk, repo, now, run) for worktree in lane_worktrees(lane, records, receipts, checkout, repo, run, excluded)]


def task_lanes(task: dict) -> set[str]:
    """The lane names an Orca task carries: its title and display name, each without a trailing ` follow-up`, and the lane its spec opens with."""
    names = {name.removesuffix(FOLLOW_UP) for name in (task.get("task_title"), task.get("display_name")) if name}
    if match := SPEC_LANE.match(task.get("spec") or ""):
        names.add(match[1])
    return names


def lane_dispatches(lane: str, tasks: list[dict], workers: list[dict]) -> list[str]:
    """The dispatches to report for the lane: a `ctx_` lane is its own dispatch; otherwise every in-progress dispatch of a task naming the lane, newest task first, or else the newest such dispatch."""
    if lane.startswith(DISPATCH):
        return [lane]
    owned = sorted((task for task in tasks if lane in task_lanes(task)), key=lambda task: task.get("created_at") or "", reverse=True)
    dispatches = [worker for task in owned for worker in workers if worker.get("taskId") == task["id"]]
    live = [worker["dispatchId"] for worker in dispatches if (worker.get("projection") or {}).get("outcome") == IN_PROGRESS]
    return live or [worker["dispatchId"] for worker in dispatches[:1]]


def orca(run: Run, *argv: str) -> dict | None:
    """The `result` of one `orca orchestration` call, or None when it fails."""
    reply = json.loads(output(run, ["orca", "orchestration", *argv, "--json"]) or "{}")
    return reply.get("result") if reply.get("ok") else None


def orca_workers(run: Run, run_id: str) -> list[dict]:
    rows: list[dict] = []
    cursor: list[str] = []
    while page := orca(run, "worker-list", "--run", run_id, "--include-remote", "--limit", "100", *cursor):
        rows += page["workers"]
        if not (after := (page.get("page") or {}).get("nextCursor")):
            break
        cursor = ["--cursor", after]
    return rows


def coordinated_run(run: Run, terminal: str) -> str | None:
    """The newest Orca Run the terminal coordinates, for a drive whose registry entry names its root terminal but no Run."""
    runs: list[dict] = []
    cursor: list[str] = []
    while page := orca(run, "run-list", "--limit", "100", *cursor):
        runs += page["runs"]
        if not (after := page.get("nextCursor")):
            break
        cursor = ["--cursor", str(after)]
    return max((row for row in runs if row.get("coordinator_handle") == terminal), key=lambda row: row.get("created_at") or "", default={}).get("id")


def minutes_since(stamp: str | None, now: datetime) -> int | None:
    if not stamp:
        return None
    at = datetime.fromisoformat(stamp)
    return int((now - (at if at.tzinfo else at.replace(tzinfo=timezone.utc))).total_seconds() // 60)


def last_message(messages: list[dict], senders: set[str], now: datetime) -> str:
    """The newest message the worker sent to the Run that is not a heartbeat, or else its newest heartbeat with the phase it reported."""
    sent = sorted((message for message in messages if message.get("from_handle") in senders), key=lambda message: message.get("sequence") or 0, reverse=True)
    if not (message := next((message for message in sent if message.get("type") != HEARTBEAT), sent[0] if sent else None)):
        return ""
    age = f"{minutes_since(message.get('created_at'), now)} min ago"
    if message.get("type") == HEARTBEAT:
        return f"heartbeat {json.loads(message.get('payload') or '{}').get('phase', '')} {age}".replace("  ", " ")
    return f'{message.get("type")} "{message.get("subject")}" {age}'


def run_tasks(run: Run, run_id: str) -> list[dict]:
    return (orca(run, "task-list", "--run", run_id) or {}).get("tasks") or []


def run_context(run: Run, run_id: str, tasks: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """The Run's task titles by task id, and its inbox."""
    messages = (orca(run, "inbox", "--terminal", f"run:{run_id}", "--limit", str(INBOX_READ)) or {}).get("messages") or []
    return {task["id"]: task.get("task_title") or task.get("display_name") or "" for task in tasks}, messages


def orca_worker(dispatch: str, contexts: dict[str, tuple[dict[str, str], list[dict]]], now: datetime, run: Run) -> OrcaWorker:
    if (shown := orca(run, "worker-show", "--dispatch", dispatch)) is None:
        return OrcaWorker(dispatch, error="orca orchestration worker-show failed")
    if (run_id := shown["dispatch"]["runId"]) not in contexts:
        contexts[run_id] = run_context(run, run_id, run_tasks(run, run_id))
    titles, messages = contexts[run_id]
    worker, terminal, projection = shown.get("worker") or {}, shown.get("terminal") or {}, shown.get("projection") or {}
    stage = projection.get("stage") or {}
    handle = terminal.get("handle") or worker.get("agentTerminalHandle") or ""
    return OrcaWorker(
        dispatch,
        task=titles.get(shown["dispatch"]["taskId"], ""),
        environment=(shown.get("server") or {}).get("name", ""),
        worktree=terminal.get("worktreePath") or (worker.get("worktreeId") or "").rpartition("::")[2],
        branch=(terminal.get("branch") or "").removeprefix("refs/heads/"),
        stage=f"{stage.get('worker') or worker.get('state')}/{stage.get('detail') or worker.get('stage')}",
        outcome=projection.get("outcome", "outcome unknown"),
        liveness=(projection.get("liveness") or {}).get("verdict", "liveness unknown"),
        updated_minutes=minutes_since(worker.get("updatedAt"), now),
        heartbeat_minutes=minutes_since(shown["dispatch"].get("lastHeartbeatAt"), now),
        terminal=f"{handle} {'connected' if terminal.get('connected') else 'disconnected'}" if handle else "",
        message=last_message(messages, {f"dispatch:{dispatch}", handle} - {""}, now),
    )


def orca_status(lane: str, run_id: str | None, now: datetime, run: Run) -> list[OrcaWorker]:
    """Each dispatch `lane_dispatches` reports for the lane from the Orca Run, as `worker-show` and its Run's inbox show it; a `ctx_` lane needs no Run."""
    contexts: dict[str, tuple[dict[str, str], list[dict]]] = {}
    tasks: list[dict] = []
    workers: list[dict] = []
    if run_id and not lane.startswith(DISPATCH):
        tasks, workers = run_tasks(run, run_id), orca_workers(run, run_id)
    if (dispatches := lane_dispatches(lane, tasks, workers)) and run_id and tasks:
        contexts[run_id] = run_context(run, run_id, tasks)
    return [orca_worker(dispatch, contexts, now, run) for dispatch in dispatches]


@dataclass
class Teammate:
    name: str
    session: str
    agent_type: str = ""
    model: str = ""
    state: str = ""
    active_minutes: int | None = None
    started: str = ""

    def line(self) -> str:
        active = "no transcript" if self.active_minutes is None else f"last activity {self.active_minutes} min ago"
        kind = ", ".join(part for part in (self.agent_type, self.model) if part)
        return f"- in-process teammate {self.name} of session {self.session[:8]} ({kind}): {self.state}, {active}, started {self.started or 'unknown'}"


def team_members(lane: str, team: dict) -> list[dict]:
    """The team config's members the lane names, by name or by the name before the `@` of its agent id."""
    return [member for member in team.get("members") or [] if lane in (member.get("name"), (member.get("agentId") or "").partition("@")[0])]


def turn_state(last: dict | None, now: datetime) -> str:
    """`idle` once the teammate's last turn ended, `running` while it is mid-turn with activity in the last hour, `stalled mid-turn` past that."""
    if last is None:
        return "state unknown"
    if last.get("type") == "assistant" and (last.get("message") or {}).get("stop_reason") == "end_turn":
        return "idle"
    active = minutes_since(last.get("timestamp"), now)
    return "running" if active is not None and active < TEAMMATE_STALLED.total_seconds() // 60 else "stalled mid-turn"


def last_turn_entry(transcript: Path) -> dict | None:
    """The transcript's newest assistant or user entry, from its last megabyte."""
    with transcript.open("rb") as handle:
        handle.seek(max(0, transcript.stat().st_size - TRANSCRIPT_TAIL))
        lines = handle.read().splitlines()[1:] if handle.tell() else handle.read().splitlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") in TURN_ENTRIES:
            return entry
    return None


def teammate_transcript(config_dir: Path, session: str, name: str) -> Path | None:
    """The newest transcript of the session's in-process teammate with this name."""
    transcripts = [
        meta.with_name(meta.name.removesuffix(".meta.json") + ".jsonl")
        for meta in config_dir.glob(f"projects/*/{session}/subagents/agent-*.meta.json")
        if (shown := json.loads(meta.read_text())).get("taskKind") == TEAMMATE and shown.get("name") == name
    ]
    return max((path for path in transcripts if path.is_file()), key=lambda path: path.stat().st_mtime, default=None)


def teammate_status(lane: str, sessions: Iterable[str], config_dir: Path, now: datetime) -> list[Teammate]:
    """Each in-process teammate of the drive's root sessions the lane names, with its agent type, model, turn state, last activity, and when it joined."""
    found: list[Teammate] = []
    for session in sessions:
        config = config_dir / "teams" / f"session-{session[:8]}" / "config.json"
        if not config.is_file() or (team := json.loads(config.read_text())).get("leadSessionId") != session:
            continue
        for member in team_members(lane, team):
            last = last_turn_entry(transcript) if (transcript := teammate_transcript(config_dir, session, member["name"])) else None
            joined = member.get("joinedAt")
            found.append(Teammate(
                member["name"],
                session,
                agent_type=member.get("agentType", ""),
                model=member.get("model", ""),
                state=turn_state(last, now),
                active_minutes=minutes_since(last.get("timestamp"), now) if last else None,
                started=datetime.fromtimestamp(joined / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if joined else "",
            ))
    return found


def summary(lane: str, worktrees: list[Worktree]) -> str:
    """One line for an escalation: each worktree's state, or that none was found."""
    if not worktrees:
        return f"no worktree found for lane {lane}"
    return " | ".join(worktree.line().removeprefix("- ") for worktree in worktrees)


def brief_roles(state: Path, lane: str) -> set[str]:
    """The roles an in-process lane's brief names, from `<state>/briefs/<lane>.md` or `<state>/<lane>/brief.md`: its `ccx:` line's `role=`, and an evidence, proof, closeout, or audit word in its title line."""
    brief = next((path for path in (state / "briefs" / f"{lane}.md", state / lane / "brief.md") if path.is_file()), None)
    if brief is None:
        return set()
    text = brief.read_text()
    title = next((line for line in text.splitlines() if line.startswith("# ")), "")
    return set(ROLE.findall(text)) | (TITLE_ROLES & set(re.findall(r"[a-z]+", title.lower())))


def owes_no_pr(state: Path, lane: str) -> bool:
    """Whether the lane's name or brief makes it an evidence, proof, closeout, desk, or audit lane, which never owes a PR."""
    return bool(NOT_FIX_ROLES & {*lane.split("-"), *brief_roles(state, lane)})


def owed_mechanism(lane: str, records: list[dict]) -> dict | None:
    """The lane's mechanism still owed a PR: its newest MECHANISM or CANDIDATE that no settling record of its own, and no PR, ready, fix-live, or root go from any lane for the same incident, and no root ruling addressed to the lane since, has answered."""
    answered: set[str] = set()
    owed: dict | None = None
    for record in sorted(records, key=lambda record: record["seq"]):
        if answers := cci.answers_mechanism(record):
            answered |= answers
            if owed and answers & owed_keys(owed):
                owed = None
        if record["lane"] != lane:
            continue
        if cci.mechanism_mark(record):
            owed = None if cci.incident_keys(record) & answered else record
        elif cci.settles_mechanism(record):
            owed = None
    return owed


def owed_keys(record: dict) -> set[str]:
    return cci.incident_keys(record) | {f"seq:{record['seq']}", f"lane:{record['lane']}"}
