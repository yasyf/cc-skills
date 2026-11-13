import itertools
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from drive import (
    agents,
    card,
    cli,
    dashboard,
    github,
    land,
    launchd,
    limits,
    orca,
    page,
    picture,
    public_check,
    receiver,
    served,
    settled,
    slack,
    stage,
    state,
    talk,
    view,
    watch,
)

SESSION = "session-under-test"
WRITTEN_AT = datetime(2026, 11, 10, 5, 0).astimezone()
LEAD = (
    "--headline",
    "His cards say why a change is made",
    "--because",
    "He could not tell from a card whether to merge its change",
    "--does",
    "Every card he taps opens with why, what and downstream",
    "--downstream",
    "Builders write four short lines for each PR they open",
)
DRIVE_TERMINAL = "term_drive"
REAL_DRIVE_TERMINAL = orca.drive_terminal
REAL_KEEP_LOGS = orca.keep_logs
REAL_SAVED_BRANCHES = github.saved_branches
REAL_SESSION_OF = orca.session_of
REAL_KEEP_TRANSCRIPTS = orca.keep_transcripts
REAL_REGISTER = agents.register
REAL_SPRITE_CONFIG = orca.sprite_config
SPRITE_CONFIG = "4b925c205080" + "0" * 52
LANE_SPEC = Path(__file__).resolve().parents[2] / "orca" / "lib" / "lane.json"
SETTLED_NOTE = cli.settled_note
POST_ASK = cli.post_ask
SETTLED_MATCHED = settled.matched
TEAM = [
    {"name": "rowan", "real_name": "Rowan Vale", "email": "rowan@example.com"},
    {"name": "juniper.cole", "real_name": "Juniper Cole", "email": "juniper.cole@example.com"},
    {"name": "mac.ivers", "real_name": "Mac Ivers", "email": "mac.ivers@example.com"},
]
LIVE_WORKER = {
    "worker": {"state": "ready", "agentTerminalHandle": "term_live"},
    "dispatch": {"runId": "run_live", "taskId": "task_live"},
    "projection": {"liveness": {"verdict": "live"}, "outcome": "in_progress", "attention": {"categories": []}},
    "terminal": {"handle": "term_live", "lastOutputAt": None},
}
CARD_BODY = """## Summary
`land.py` lands the PR once your tap comes in.

## What you'll hate most
It lands without a second look.

## His words, step by step
- "land it" beside `land.py:1`
"""

RESEARCH = """DONE researched the store copy
Researched:
rulings: none on the copy path
exists: supervisor/copies.rs store_copy_at copies one store
open: none
"""

LOCK_STALL = (
    "Drive tooling: one long drive command, a break-glass that ran 14 minutes on the previous release, held its release's lock the whole"
    " time, so every drive command that writes on the newer release waited with no output for up to 11 minutes, about 60 of them across every session"
)
LOCK_STALL_AGAIN = (
    "Drive tooling: one long command of a break-glass job on an older install held every newer verb, so for about ten minutes no session"
    " could add a job, post a record or start a handler"
)

SHORT_ASK = """ASK How should a finished run's files be laid out so its page opens without guessing?
Drive tooling: your pick decides how a finished run's files are laid out; a run is about 180 small files, each with its page map at the end, so the browser guesses where each map starts.
Recommended: one snapshot file per finished run, so any finished run opens in 3 requests.
Meanwhile nothing is built on either layout.
Show me:
1 · One snapshot file per finished run, the requests its page makes:
```http
GET snapshot.ltx      map at a fixed place
GET 2 page ranges
```

2 · A map file beside each journal file, the requests its page makes:
```http
GET journal/0001.map  the map, no guessing
GET journal/0001.ltx  one page range
... 180 files, two requests each
```
- One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests (recommended)
  When a run finishes, the uploader in `journal/snapshot.go` writes one snapshot.ltx to the team's bucket with its page map at a fixed place, and the run page's reader in `site/journal.js` reads that map, then 2 page ranges; a backfill writes one for each old run.
- A map file beside each journal file: no guessing, but one request per file and twice the objects
  Each journal upload in `journal/upload.go` also writes a map object beside its file in the team's bucket, and `site/journal.js` reads each map, then its page range; every journal upload pays one more object write.
- Map at the start of each file: changes the shared file format and still needs a length read first
  The shared LTX format in `journal/ltx.go` moves each file's page map to its start, so every reader and writer of journal files changes, and the run page still reads the map's length before the map and the range.

Findings:
Root cause: none: a design choice, with no defect behind it
Simplest: One snapshot file per finished run, written when it ends, old runs backfilled
Holds: none: no option changes code any job builds
(1) What a journal file is: a run's journal is a SQLite database, copied to the team's bucket as LTX files.
(2) Each LTX file ends with a page map; its length sits in the last 24 bytes.
"""
DESCRIPTION = "  The drive runs it once he picks it and keeps nothing past the run; it changes `lib/drive/cli.py` only.\n"


def explained(block: str) -> str:
    """The block with the plain-text description `drive job ask` asks of every option under each option above its findings."""
    lines, fenced, findings = [], False, False
    for line in block.splitlines(keepends=True):
        lines.append(line)
        fenced ^= line.lstrip().startswith("```")
        findings = findings or line.startswith("Findings:")
        if line.startswith("- ") and not (fenced or findings):
            lines.append(("" if line.endswith("\n") else "\n") + DESCRIPTION)
    return "".join(lines)


def git_in(repo: Path, *args: str) -> str:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", "-C", str(repo), *identity, *args], check=True, capture_output=True, text=True).stdout.strip()


def staged(stage: Callable[[watch.Watcher, datetime], list[str]], watching: watch.Watcher, moment: datetime) -> list[str]:
    """A watch stage's lines as its pass takes them: those it joined to the watcher's made lines at its save, then those it returned."""
    returned = stage(watching, moment)
    made, watching.made = watching.made, []
    return [*made, *returned]


def commit_files(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", message)
    return git_in(repo, "rev-parse", "HEAD")


def cloned(tmp_path: Path, name: str, files: dict[str, str] | None = None) -> str:
    """Commit files, by default an .orca.json, on main of an origin that the drive's coordinator checkout is a clone of, both made on the first call, as the drive's checkout is moved to before a launch; main's head."""
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    if not origin.exists():
        origin.mkdir()
        git_in(origin, "init", "-q", "-b", "main")
    head = commit_files(origin, "the launch files", files or {".orca.json": "{}"})
    if not clone.exists():
        git_in(tmp_path, "clone", "-q", str(origin), str(clone))
        with state.edit(name) as data:
            data["roles"]["coordinator"]["cwd"] = str(clone)
    return head


FAKE_CCX = """#!/bin/sh
[ "$1 $2 $3 $5 $6" = "vcs worktree add --jj none" ] || exit 64
echo "$4" >>"{made}"
git show-ref -q --verify "refs/heads/$4" && exec git worktree add -q "{root}/$4" "$4"
git fetch -q --no-tags --no-write-fetch-head origin +refs/heads/main:refs/remotes/origin/main || exit 1
exec git worktree add -q -b "$4" "{root}/$4" origin/main
"""


@pytest.fixture
def ccx_worktrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ccx first on PATH whose vcs worktree add makes a git worktree under the folder it returns, as ccx does under its worktree root, on the local branch it names as that branch stands, or else on a new branch cut from origin/main once it fetched origin's main into it, as ccx fetches the trunk, naming each branch it made in that folder's made file."""
    root, bin_dir = tmp_path / "worktrees", tmp_path / "ccx-bin"
    root.mkdir()
    bin_dir.mkdir()
    (bin_dir / "ccx").write_text(FAKE_CCX.format(made=root / "made", root=root))
    (bin_dir / "ccx").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return root


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "long-running"
    monkeypatch.delenv("LONG_RUNNING_STATE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_outer")
    monkeypatch.setenv("TMUX", "/private/tmp/tmux-501/default,1,0")
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def run(state_root: Path, capsys: pytest.CaptureFixture[str]) -> Callable[..., tuple[int, str]]:
    def invoke(*argv: str) -> tuple[int, str]:
        code = cli.main(list(argv))
        return code, capsys.readouterr().out.strip()

    return invoke


@pytest.fixture
def demo(run: Callable[..., tuple[int, str]]) -> str:
    run("start", "demo", "--role", "coordinator")
    return "demo"


@pytest.fixture
def communicator(run: Callable[..., tuple[int, str]], demo: str) -> str:
    run("start", demo, "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["session"] = "elsewhere"
    return demo


@pytest.fixture
def meta(run: Callable[..., tuple[int, str]], demo: str) -> str:
    run("start", demo, "--role", "meta")
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["session"] = "elsewhere"
    return demo


@pytest.fixture(autouse=True)
def written_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every write the typed-time check reads is stamped WRITTEN_AT, so a clock time in a test's words is past or to come by the test, never by when it runs."""
    monkeypatch.setattr(limits, "now", lambda: WRITTEN_AT)


@pytest.fixture(autouse=True)
def judged_pictures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test keeps the verdicts on which fences illustrate something under its own state root, never the real one or another test's."""
    monkeypatch.delenv("LONG_RUNNING_STATE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def lane_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every session the drive starts reads the lane settings this tree's orca plugin ships, never an install of this Mac's."""
    monkeypatch.setattr(agents, "lane_spec", lambda: json.loads(LANE_SPEC.read_text()))


@pytest.fixture(autouse=True)
def drive_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every drive speaks to Orca as a terminal of its own it already keeps, never one Orca opens for the test."""
    monkeypatch.setattr(orca, "drive_terminal", lambda drive: DRIVE_TERMINAL)


@pytest.fixture(autouse=True)
def cards_drawn(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Each card drive job ask or the watch draws, at a link of its own recorded on its question as talk.posted records it, under waits ids minted 7, 8, … in turn; a test of the draw itself puts back POST_ASK."""
    drawn: list[dict] = []
    minted = itertools.count(7)

    def post(name: str, job_id: str, file: Path, card: dict) -> str:
        drawn.append({"job": job_id, "file": str(file)} | card)
        link = drawn_card(len(drawn))
        cli.follow_card(name, card["waits"], set(), link)
        return f"his card {link} asks it under {card['waits']}"

    monkeypatch.setattr(cli, "post_ask", post)
    monkeypatch.setattr(state, "new_waits", lambda name, job_id: f"{name}:{job_id}-{next(minted)}")
    return drawn


def drawn_card(n: int) -> str:
    return f"https://acme.slack.com/archives/D123/p{1794400000000100 + n}"


@pytest.fixture(autouse=True)
def closed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(github, "close", urls.append)
    return urls


@pytest.fixture(autouse=True)
def drawn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    pages: list[str] = []
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    monkeypatch.setattr(dashboard, "tailnet", lambda: None)
    monkeypatch.setattr(launchd, "kept", lambda label: True)
    monkeypatch.setattr(page, "draw", lambda name, markdown: pages.append(markdown) or page.Drawn("https://slack.example/docs/T1/F1", False))
    return pages


@pytest.fixture(autouse=True)
def landers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(land, "spawn", lambda name, keys: started.append(keys) or os.getpid())
    monkeypatch.setattr(github, "default_branch", lambda repo: "main")
    monkeypatch.setattr(github, "below", lambda key: [])
    return started


@pytest.fixture(autouse=True)
def provers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []
    monkeypatch.setattr(watch, "prove", lambda name, job_id: started.append(job_id) or os.getpid())
    return started


@pytest.fixture(autouse=True)
def checkers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []
    monkeypatch.setattr(served, "spawn", lambda name, key: started.append(key) or os.getpid())
    return started


@pytest.fixture(autouse=True)
def native_stacks(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[tuple[str, str]]]:
    stacks: dict[str, list[tuple[str, str]]] = {}
    monkeypatch.setattr(github, "stacks", lambda keys: {key: stacks[key] for key in keys if key in stacks})
    return stacks


@dataclass
class Thread:
    messages: list[dict] = field(default_factory=list)
    reads: list[tuple[str, ...]] = field(default_factory=list)

    def post(self, moment: datetime, **fields: object) -> str:
        ts = f"{moment.timestamp():.6f}"
        self.messages.append({"ts": ts, "thread_ts": "1794200000.000100", **fields})
        return f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}?thread_ts=1794200000.000100"


@pytest.fixture(autouse=True)
def records(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    shown: dict[str, str] = {}
    monkeypatch.setattr(agents, "record", lambda seq: shown.get(seq, f"m{seq}"))
    return shown


@pytest.fixture(autouse=True)
def cci_records(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    kept: dict[str, dict] = {}
    monkeypatch.setattr(agents, "shown", lambda seq: kept.get(seq, {"seq": int(seq), "kind": "report", "lane": "root", "fields": {"attrs": {}}}))
    return kept


@pytest.fixture(autouse=True)
def cci_amendments(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, list[str]]]:
    kept: dict[str, dict[str, list[str]]] = {kind: {} for kind in agents.AMENDING}
    monkeypatch.setattr(agents, "amendments", lambda name, seqs: {kind: {seq: found[seq] for seq in seqs if seq in found} for kind, found in kept.items()})
    return kept


@pytest.fixture(autouse=True)
def trunk_queue(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    kept: list[dict] = []
    monkeypatch.setattr(agents, "trunk_failures", lambda keys, since: list(kept))
    return kept


@pytest.fixture(autouse=True)
def ended_sessions(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    ended: set[str] = set()
    monkeypatch.setattr(agents, "gone", lambda session: session in ended)
    return ended


@dataclass
class Judge:
    against: list[dict] = field(default_factory=list)
    asked: list[tuple[str, dict]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def judge(monkeypatch: pytest.MonkeyPatch) -> Judge:
    judging = Judge()
    monkeypatch.setattr(served, "change", lambda key: {"title": f"Fix: {key}", "body": "", "head": card.drive_prs().get(key, ("", {"head": None}))[1]["head"], "diff": b""})
    monkeypatch.setattr(served, "judged", lambda name, said, changes: judging.asked.append((said, changes)) or list(judging.against))
    return judging


@pytest.fixture(autouse=True)
def his_dm(monkeypatch: pytest.MonkeyPatch) -> str:
    """Every drive reads his DM as cc-slack whoami names it, D123, never this Mac's."""
    monkeypatch.setattr(slack, "dm", lambda: "D123")
    return "D123"


@pytest.fixture(autouse=True)
def slack_thread(monkeypatch: pytest.MonkeyPatch) -> Thread:
    thread = Thread()
    monkeypatch.setattr(slack, "launcher", lambda: "cc-slack")
    monkeypatch.setattr(slack, "thread", lambda *where: thread.reads.append(where) or list(thread.messages))
    return thread


@dataclass
class Checked:
    findings: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    asked: list[list[str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def checked(monkeypatch: pytest.MonkeyPatch) -> Checked:
    check = Checked()
    monkeypatch.setattr(slack, "check_post", lambda text, asked: check.texts.append(text) or check.asked.append(asked) or list(check.findings))
    return check


@dataclass
class Drawing:
    refusal: str | None = None
    cards: list[dict] = field(default_factory=list)


@pytest.fixture(autouse=True)
def drawing(monkeypatch: pytest.MonkeyPatch) -> Drawing:
    draw = Drawing()
    monkeypatch.setattr(slack, "draw_refusal", lambda card: draw.cards.append(card) or draw.refusal)
    return draw


@dataclass
class Chrome:
    refusal: str | None = None
    drawn: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def chrome(monkeypatch: pytest.MonkeyPatch) -> Chrome:
    shots = Chrome()

    def visual_png(kind: str, source: str, out: Path) -> None:
        if shots.refusal:
            raise picture.PictureError(shots.refusal)
        shots.drawn.append((kind, source))
        out.write_bytes(b"\x89PNG\r\n")

    monkeypatch.setattr(picture, "visual_png", visual_png)
    return shots


@dataclass
class Withdrawals:
    cards: list[str] = field(default_factory=list)
    asked: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def withdrawals(monkeypatch: pytest.MonkeyPatch) -> Withdrawals:
    withdrawn = Withdrawals()
    monkeypatch.setattr(slack, "withdraw_pr_card", lambda url, reason: withdrawn.asked.append((url, reason)) or list(withdrawn.cards))
    return withdrawn


@dataclass
class Redraws:
    asked: list[str] = field(default_factory=list)
    failure: str | None = None


@pytest.fixture(autouse=True)
def redraws(monkeypatch: pytest.MonkeyPatch) -> Redraws:
    redrawn = Redraws()

    def redraw(url: str) -> int:
        redrawn.asked.append(url)
        if redrawn.failure:
            raise slack.SlackError(redrawn.failure)
        return 1

    monkeypatch.setattr(slack, "redraw_pr_cards", redraw)
    return redrawn


@dataclass
class Draws:
    keys: list[list[str]] = field(default_factory=list)
    again: list[bool] = field(default_factory=list)
    failure: str | None = None
    link: str = "https://forge.slack.com/archives/D123/p1794379200123456"


@pytest.fixture(autouse=True)
def draws(monkeypatch: pytest.MonkeyPatch) -> Draws:
    drew = Draws()

    def draw(name: str, keys: list[str], again: bool = False) -> str:
        drew.keys.append(list(keys))
        drew.again.append(again)
        if drew.failure:
            raise slack.SlackError(drew.failure)
        with state.edit(name) as data:
            for key in keys:
                data["prs"][key]["card"] = drew.link
        return drew.link

    monkeypatch.setattr(talk, "draw", draw)
    return drew


@pytest.fixture(autouse=True)
def woken(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(stage, "wake", lambda name, key, text: sent.append((key, text)))
    return sent


@pytest.fixture(autouse=True)
def live_sessions(monkeypatch: pytest.MonkeyPatch) -> dict[str, int | None]:
    pids: dict[str, int | None] = {}
    monkeypatch.setattr(orca, "live_pid", lambda session: pids.get(session, 4242))
    return pids


@pytest.fixture(autouse=True)
def tooled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    placed: list[str] = []
    monkeypatch.setattr(orca, "place_tools", placed.append)
    return placed


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every checkout holds the cc-remote config whose SHA-256 is SPRITE_CONFIG, which the drive's checkout in a test holds no file of."""
    monkeypatch.setattr(orca, "sprite_config", lambda checkout: SPRITE_CONFIG)


@pytest.fixture(autouse=True)
def saved_on_github(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """The branches GitHub holds under saved/<lane>/, by lane, or with every under saved/<lane>-<n>/ of each later attempt of that lane too, as its one matching-refs read names them; none until a test saves some."""
    held: dict[str, list[str]] = {}

    def saved_branches(repo: str, lane: str, every: bool = False) -> list[str]:
        return [branch for saved, branches in held.items() if saved == lane or every and re.fullmatch(rf"{re.escape(lane)}-\d+", saved) for branch in branches]

    monkeypatch.setattr(github, "saved_branches", saved_branches)
    return held


@pytest.fixture(autouse=True)
def kept_logs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Each dead lane whose Sprite's logs a resume or an end copied to the drive's folder, by job and dispatch."""
    kept: list[tuple[str, str]] = []
    monkeypatch.setattr(orca, "keep_logs", lambda drive, job_id, job, dispatch: kept.append((job_id, dispatch)))
    return kept


@pytest.fixture(autouse=True)
def runtimes_listening(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Whether anything listens where Orca's runtime serves on each workspace's Sprite, as that Sprite answers over ssh, by workspace; no answer, as from a Sprite ssh does not reach, unless a test puts one."""
    answers: dict[str, bool] = {}
    monkeypatch.setattr(orca, "runtime_listening", answers.get)
    return answers


@pytest.fixture(autouse=True)
def sessions(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The Claude session each Sprite answers with for a dead lane's dispatch, by workspace, which a resume of that lane resumes there; none unless a test puts one."""
    held: dict[str, str] = {}
    monkeypatch.setattr(orca, "session_of", lambda workspace, dispatch: held.get(workspace))
    return held


@pytest.fixture(autouse=True)
def kept_transcripts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Each lane whose Sprite's transcripts an end or a relaunch copied to the drive's folder, by job and dispatch."""
    kept: list[tuple[str, str]] = []
    monkeypatch.setattr(orca, "keep_transcripts", lambda drive, job_id, job, dispatch: kept.append((job_id, dispatch)))
    return kept


@pytest.fixture(autouse=True)
def agents_at_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None)
    monkeypatch.setattr(watch, "codex_gap", lambda job_id, job: None)
    monkeypatch.setattr(watch, "last_write", lambda job, data: (state.now(), None))
    monkeypatch.setattr(orca, "worker", lambda dispatch: LIVE_WORKER)
    monkeypatch.setattr(orca, "task_title", lambda run, task: f"lane-of-{task}")
    monkeypatch.setattr(github, "open_on", lambda heads: {})


@pytest.fixture(autouse=True)
def relaunches(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each job whose dead lane the watch relaunched, as a drive job resume that still runs."""
    started: list[str] = []
    monkeypatch.setattr(watch, "relaunch", lambda name, job_id: started.append(job_id) or os.getpid())
    return started


@pytest.fixture
def ended_lanes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each waiting job whose dead lane on a Sprite the watch ended, as a drive job end that still runs."""
    ended: list[str] = []
    monkeypatch.setattr(watch, "end_lane", lambda name, job_id: ended.append(job_id))
    return ended


@dataclass
class MetaLaunches:
    started: list[tuple[str, str]] = field(default_factory=list)
    open: set[str] = field(default_factory=set)


@pytest.fixture(autouse=True)
def meta_launches(monkeypatch: pytest.MonkeyPatch) -> MetaLaunches:
    launches = MetaLaunches()

    def start_meta(drive: str, worktree: str, earlier: str | None) -> str | None:
        if earlier in launches.open:
            return None
        launches.started.append((drive, worktree))
        launches.open.add(handle := f"term_meta{len(launches.started)}")
        return handle

    monkeypatch.setattr(orca, "start_meta", start_meta)
    return launches


@dataclass
class WatchProcess:
    ensured: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def posted_lanes(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    lanes: set[str] = set()
    monkeypatch.setattr(cli, "lane_posted", lambda name, lane: lane in lanes)
    return lanes


@pytest.fixture(autouse=True)
def watch_process(monkeypatch: pytest.MonkeyPatch) -> WatchProcess:
    process = WatchProcess()

    def ensure(name: str, project: str | None = None) -> str:
        process.ensured.append(f"{name} --project {project}" if project else name)
        return f"the watch runs for {name}"

    monkeypatch.setattr(watch, "ensure", ensure)
    monkeypatch.setattr(launchd, "session_env", dict)
    return process


@dataclass
class Receiver:
    unhooked: set[str] = field(default_factory=set)
    hooked: list[str] = field(default_factory=list)
    answering: bool = True
    unadministered: set[str] = field(default_factory=set)
    refused: dict[str, Exception] = field(default_factory=dict)
    tried: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def registered(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    sessions: list[tuple[str, str, str]] = []

    def register(name: str, role: str, project: str | None, worktree: str, session: str) -> str:
        sessions.append((state.alias(name, role, project), worktree, session))
        return f"{state.alias(name, role, project)} registered: cci session run keeps session {session} running in tmux"

    monkeypatch.setattr(agents, "register", register)
    return sessions


@pytest.fixture(autouse=True)
def tmux_shown(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    shown: dict[str, str] = {}
    monkeypatch.setattr(orca, "tmux", lambda *args: shown.get(args[0], ""))
    return shown


@pytest.fixture(autouse=True)
def receiving(monkeypatch: pytest.MonkeyPatch) -> Receiver:
    taking = Receiver()

    def hook(repo: str) -> bool:
        taking.tried.append(repo)
        if repo in taking.refused:
            raise taking.refused[repo]
        if repo in taking.unadministered:
            return False
        taking.hooked.append(repo)
        taking.unhooked.discard(repo)
        return True

    monkeypatch.setattr(receiver, "keep", lambda: None)
    monkeypatch.setattr(receiver, "unhooked", lambda repos: sorted(taking.unhooked & repos))
    monkeypatch.setattr(receiver, "hook", hook)
    monkeypatch.setattr(receiver, "answering", lambda: taking.answering)
    return taking


@pytest.fixture(autouse=True)
def delivered_each_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub delivering a change of every PR the watch hears and of every release before each pass that runs its 60-second block, so a test reads them all there; a test of what deliveries owe runs the real pass."""
    passed = watch.run_pass

    def run_pass(watching: watch.Watcher, moment: datetime) -> list[str]:
        if watching.read_at is None or moment - watching.read_at >= watch.READ_EVERY:
            watching.owed, watching.release_marks = None, {}
        return passed(watching, moment)

    monkeypatch.setattr(watch, "run_pass", run_pass)


@pytest.fixture(autouse=True)
def rests(monkeypatch: pytest.MonkeyPatch) -> None:
    """The watch's rest between passes as a sleep of POLL_SECONDS, since the kqueue that wakes it on an event exists on macOS alone."""

    def slept(name: str) -> Iterator[None]:
        while True:
            yield
            watch.time.sleep(watch.POLL_SECONDS)

    monkeypatch.setattr(watch, "rests", slept)


@pytest.fixture(autouse=True)
def teammates(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    team = [dict(user) for user in TEAM]
    monkeypatch.setattr(public_check, "roster", lambda: public_check.Roster.of(team, "me@example.com"))
    return team


@pytest.fixture(autouse=True)
def visibilities(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(github, "visibility", lambda repo: seen.get(repo, "private"))
    return seen


@pytest.fixture(autouse=True)
def pr_reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    reads: dict[str, dict] = {}
    monkeypatch.setattr(github, "fetch", lambda keys: ({key: reads[key] for key in keys if key in reads}, None))
    return reads


@dataclass
class Answers:
    shown: dict[str, dict] = field(default_factory=dict)
    unrecorded: set[str] = field(default_factory=set)
    matched: list[dict] = field(default_factory=list)


@dataclass
class Heard:
    refusal: str | None = None
    checked: list[tuple[str, str | None]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def heard(monkeypatch: pytest.MonkeyPatch) -> Heard:
    hearing = Heard()

    def unsaid(kept: list[str], link: str | None, found: dict | None, data: dict) -> str | None:
        for words in kept:
            hearing.checked.append((words, link))
            if hearing.refusal:
                return hearing.refusal
        return None

    monkeypatch.setattr(settled, "unsaid", unsaid)
    return hearing


@pytest.fixture(autouse=True)
def backfilled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    checkouts: list[str] = []
    monkeypatch.setattr(settled, "backfill", checkouts.append)
    return checkouts


@pytest.fixture(autouse=True)
def recorded(monkeypatch: pytest.MonkeyPatch) -> Answers:
    answers = Answers()

    def shown(answer_id: str, cwd: str) -> dict:
        if answer_id in answers.unrecorded:
            raise state.DriveError(f'could not read his answer {answer_id}: not-found: entity not found: no answer matches "{answer_id}"')
        return answers.shown.get(answer_id, {"id": answer_id, "title": "", "body": "", "tags": [], "created_at": "2026-11-12T11:33:00Z"})

    monkeypatch.setattr(settled, "shown", shown)
    monkeypatch.setattr(settled, "matched", lambda text, cwd: answers.matched)
    return answers


@dataclass
class Jev:
    asked: list[dict] = field(default_factory=list)
    answer: dict | None = field(default_factory=lambda: {"type": "label", "choice": "none", "probabilities": {"none": 0.9}, "confidence": 0.9})
    ruled: dict[str, str] = field(default_factory=dict)
    changes: dict[str, float] = field(default_factory=dict)
    plain: set[str] = field(default_factory=set)
    illustrated: list[str] = field(default_factory=list)
    looks: float = 0.0
    looked: list[dict] = field(default_factory=list)

    def picks(self, choice: str, probability: float = 0.9) -> None:
        self.answer = label(choice, {"none": round(1 - probability, 2), choice: probability})

    def breaks(self, part: str, word_id: str) -> None:
        self.ruled[part] = word_id

    def judges(self, questions: dict) -> dict:
        return dict.fromkeys(questions, self.answer) | {part: label(word_id, {"none": 0.1, word_id: 0.9}) for part, word_id in self.ruled.items()}


def label(choice: str, probabilities: dict[str, float]) -> dict:
    return {"type": "label", "choice": choice, "probabilities": probabilities, "confidence": probabilities[choice]}


@pytest.fixture(autouse=True)
def jev(monkeypatch: pytest.MonkeyPatch) -> Jev:
    judged = Jev()

    def answered(request: dict) -> dict:
        questions = request["questions"]
        if "illustrates" in questions:
            judged.illustrated.append(request["state"]["code"])
        elif limits.LOOKS in questions:
            judged.looked.append(request["state"])
        else:
            judged.asked.append(request)
        if judged.answer is None:
            raise settled.Unheard("no jev decision within 20s")
        if "illustrates" in questions:
            p_yes = 0.05 if request["state"]["code"] in judged.plain else 0.95
            return {"illustrates": {"type": "binary", "p_yes": p_yes, "confidence": 0.9}}
        if limits.LOOKS in questions:
            return {limits.LOOKS: label(limits.LOOKS if judged.looks >= 0.5 else "none", {"none": round(1 - judged.looks, 2), limits.LOOKS: judged.looks})}
        if "changes" in request["questions"]:
            read = {"none": 0.9} | judged.changes
            return {"changes": label(max(read, key=read.__getitem__), read)}
        if "card" in questions:
            return judged.judges(questions)
        return {key: judged.answer for key in request["questions"]}

    monkeypatch.setattr(settled, "jev", answered)
    return judged


@pytest.fixture(autouse=True)
def settled_notes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "settled_note", lambda name, data, job_id, title, answer: told.append((job_id, cli.answered_at(answer))))
    return told


@pytest.fixture(autouse=True)
def on_github(monkeypatch: pytest.MonkeyPatch) -> dict:
    pr = {
        "title": "Fix: land it",
        "body": CARD_BODY,
        "author": {"login": "yasyf", "is_bot": False},
        "comments": [],
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc",
        "baseRefName": "main",
        "headRefName": "land-it",
        "reviewDecision": "REVIEW_REQUIRED",
        "labels": [],
        "url": "https://github.com/Forge-AI/monorepo/pull/42",
    }
    monkeypatch.setattr(card, "run", lambda cmd, **_: json.dumps(pr["comments"] if cmd[1] == "api" else pr))
    return pr


FAKE_CODEX_ASK = """#!/bin/sh
run="$2"
if [ "$1" = --dispatch ]; then
  run="$3"
  if [ "$(cat "$run/state" 2>/dev/null)" = running ]; then
    echo "codex-ask: lane $run is busy" >&2
    exit 1
  fi
  mkdir -p "$run"
  printf '%s\\n' "$@" > "$run/argv"
  cat > "$run/question"
  printf running > "$run/state"
  : > "$run/codex-r-1"
  printf '%s\\n%s\\n{}\\n' "$run/codex-r-1" "$run/codex-q-1.log" > "$run/meta"
  exit
fi
printf '{"lane":".","state":"%s","reply_file":"%s","reply_size":1,"log_file":"%s","question":"q","question_files":1}\\n' \\
  "$(cat "$run/state")" "$(sed -n 1p "$run/meta")" "$(sed -n 2p "$run/meta")"
"""


@dataclass
class CodexAsk:
    """Run folders shaped as codex-ask writes them, made by a codex-ask whose `--dispatch` starts a run going and whose `--collect` prints the state each folder's `state` file holds."""

    runs: Path
    made: int = 0

    def run(self, state: str, reply: str = "", log: str = "") -> Path:
        self.made += 1
        folder = self.runs / f"codex-ask.{self.made}"
        folder.mkdir()
        (folder / f"codex-r-{self.made}").write_text(reply)
        (folder / f"codex-q-{self.made}.log").write_text(log)
        (folder / "meta").write_text(f"{folder / f'codex-r-{self.made}'}\n{folder / f'codex-q-{self.made}.log'}\n{{}}\n")
        (folder / "state").write_text(state)
        return folder

    def end(self, folder: Path, state: str, reply: str = "", log: str = "") -> None:
        reply_file, log_file = (folder / "meta").read_text().splitlines()[:2]
        Path(reply_file).write_text(reply)
        Path(log_file).write_text(log)
        (folder / "state").write_text(state)


def codex_reply(state: str, line: str, report: str = "") -> str:
    return json.dumps({"state": state, "line": line, "report": report})


@pytest.fixture
def codex_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CodexAsk:
    plugin = tmp_path / "codex-plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "codex-ask").write_text(FAKE_CODEX_ASK)
    (plugin / "bin" / "codex-ask").chmod(0o755)
    config = tmp_path / "config"
    (config / "plugins").mkdir(parents=True)
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"codex@skills": [{"installPath": str(plugin)}]}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    (tmp_path / "runs").mkdir()
    return CodexAsk(tmp_path / "runs")


@pytest.fixture(autouse=True)
def unrecorded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The defects say sends the meta session for posts it could not record on their orders."""
    sent: list[str] = []
    monkeypatch.setattr(talk, "unrecorded", lambda name, text: sent.append(text))
    return sent


@pytest.fixture(autouse=True)
def detached_in_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the handler start `drive handler` detaches and the order record `say` detaches in the test's own process, the record with no session as state.detach runs it, so a test reads the `claude --bg` it ran and say's line carries its record; every other detached verb is detached as before."""
    detached = state.detach

    def detach(name: str, args: list[str], log: str) -> int:
        if args[:1] == ["handler-start"]:
            agents.start_kept(name, args[1])
            return 0
        if args[:1] == ["say-record"]:
            with pytest.MonkeyPatch.context() as sessionless:
                sessionless.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
                talk.record_kept(name, Path(args[1]))
            return 0
        return detached(name, args, log)

    monkeypatch.setattr(state, "detach", detach)
