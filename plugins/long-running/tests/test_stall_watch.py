"""stall_watch.py over recorded cci records, ledger rows, a board, a task list, and a scripted Sonnet: every post and every judge call is recorded on the fake shell."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import actions
from long_running import board_rows
from long_running import drive
from long_running import grades
import pytest
import stall_watch

NOW = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)
DRIVE = "d1"
SESSION = "s-root"
REPO = "Forge-AI/monorepo"
HEAD = "abc1234def5678abc1234def5678abc1234def56"
OLD = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


def stamp(minutes_ago: float) -> str:
    return actions.stamp(NOW - timedelta(minutes=minutes_ago))


def record(seq: int, lane: str, kind: str, text: str, minutes_ago: float, pr: str = "", **extra) -> dict:
    return {"seq": seq, "num": f"#{seq}", "drive": DRIVE, "lane": lane, "kind": kind, "at": stamp(minutes_ago), "text": text, "to": ["root"], "refs": {"prs": [f"{REPO}#{pr}"]} if pr else {}} | extra


def hold(seq: int, text: str, *prs: str) -> dict:
    return record(seq, "root", "hold", text, 300, status="open", refs={"prs": [f"{REPO}#{pr}" for pr in prs]})


def head(seq: int, pr: str, sha: str, minutes_ago: float, text: str = "") -> dict:
    return record(seq, "api-lane", "head", text or f"HEAD #{pr} {sha}", minutes_ago, pr)


def finder(seq: int, pr: str, sha: str, minutes_ago: float) -> dict:
    return record(seq, "finder-desk", "review", f"finder: ok at {sha[:7]}", minutes_ago, pr)


def fidelity(seq: int, pr: str, sha: str, minutes_ago: float) -> dict:
    return record(seq, "fidelity-desk", "review", f"fidelity: ok {sha[:7]}", minutes_ago, pr)


def row(pr: str, sha: str = HEAD, **fields) -> dict:
    graded = {"pr": pr, "head": sha, "lane": "api-lane", "state": "open", "test_state": "success", "ai_review": "success", "branch": f"b{pr}", "base": "dev"} | fields
    return {"green_at": stamp(60) if graded["test_state"] == "success" else ""} | graded


class FakeShell(stall_watch.Shell):
    def __init__(self, records: list[dict], rows: list[dict]) -> None:
        self.records = records
        self.rows = rows
        self.doc: dict = {"blocks": []}
        self.triage: dict = {}
        self.choices: dict = {}
        self.answer = lambda question: {}
        self.judge_fails = False
        self.ledger_code = 0
        self.posts: list[list[str]] = []
        self.prompts: list[str] = []
        self.review_titles: dict[str, str] = {}
        self.comments: dict[str, list[dict]] = {}
        self.shapes: list[dict] = []
        self.peers: list[dict] = []
        self.clock = NOW

    def run(self, argv, stdin=None, env=None, timeout=stall_watch.COMMAND_SECONDS):
        if argv[:2] == ["cci", "tail"]:
            since = argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1]
            kinds = {arg.split("=", 1)[1] for arg in argv if arg.startswith("--kind=")}
            topics = {arg.split("=", 1)[1] for arg in argv if arg.startswith("--topic=")}
            lane = argv[argv.index("--lane") + 1] if "--lane" in argv else None
            pool = [*self.records, *self.board_records()] if lane else self.records
            page = [found for found in pool if (not since.isdigit() or found["seq"] > int(since)) and (not kinds or found["kind"] in kinds) and (not topics or found.get("topic") in topics) and (lane is None or found["lane"] == lane)]
            return stall_watch.Done(0, "".join(json.dumps(found) + "\n" for found in page), "")
        if argv[:2] == ["cci", "who"]:
            assert argv[2:] == ["--drive", DRIVE, "--all", "--json"]
            return stall_watch.Done(0, "".join(json.dumps(peer) + "\n" for peer in self.peers), "")
        if argv[:2] == ["cci", "post"]:
            self.posts.append(argv)
            return stall_watch.Done(0, f"posted #{9000 + len(self.posts)}", "")
        if argv[:3] == ["ccx", "vcs", "gh"] and argv[argv.index("--") + 1 : argv.index("--") + 3] == ["pr", "view"]:
            return stall_watch.Done(0, json.dumps({"comments": self.comments.get(argv[argv.index("--") + 3], [])}), "")
        if argv[:3] == ["ccx", "vcs", "gh"]:
            endpoint = argv[argv.index("--") + 2]
            assert endpoint.endswith("/check-runs?check_name=ai-review")
            return stall_watch.Done(0, self.review_titles[endpoint.split("/")[4]] + "\n", "")
        if Path(argv[1]).name == "rulings.py":
            assert argv[2:] == ["shapes", "--untested", "--json", "--drive", "durable"]
            return stall_watch.Done(0, json.dumps(self.shapes), "")
        if Path(argv[1]).name == "ledger.py":
            return stall_watch.Done(self.ledger_code, json.dumps(self.rows) if self.ledger_code == 0 else "", "ccn down" if self.ledger_code else "")
        raise AssertionError(f"unexpected command {argv}")

    def batch(self, kind: str, prompt: str, schema: str, *, drive: str | None = None, timeout: float = 0) -> dict | None:
        assert (kind, schema, drive) == (stall_watch.JUDGE_KIND, stall_watch.SCHEMA, "durable")
        self.prompts.append(prompt)
        if self.judge_fails:
            return None
        questions = json.loads(prompt.split("Questions:\n", 1)[1])
        return {"verdicts": [{"id": question["id"], "why": "the record says so"} | self.answer(question) for question in questions]}

    def board_records(self) -> list[dict]:
        found = [record(800000, "board-watch", "note", "board", 1, topic="board-rows", fields={"attrs": {"session": SESSION, "board": json.dumps(board_rows.snapshot(self.doc))}})]
        for block, rows in self.triage.items():
            for row_id, entry in rows.items():
                attrs = {"session": SESSION, "block": block, "row": row_id, "verdict": entry["verdict"], "head": entry.get("head") or "", "at": entry.get("at") or ""}
                found.append(record(800000 + len(found), "board-watch", "owner", f"{row_id} {entry['verdict']}", 1, fields={"attrs": {name: value for name, value in attrs.items() if value}}))
        for block, choice in self.choices.items():
            if choice:
                found.append(record(800000 + len(found), "board-watch", "owner", "picked", 1, fields={"attrs": {"session": SESSION, "event": "choice.selected", "block": block, "picked": ",".join(choice.get("optionIds") or [])}}))
        return found

    def now(self):
        return self.clock

    def topics(self) -> list[str]:
        return [post[post.index("--topic") + 1] for post in self.posts]

    def post(self, topic: str) -> dict[str, str]:
        found = next(post for post in self.posts if post[post.index("--topic") + 1] == topic)
        return {"to": found[found.index("--to") + 1], "text": found[found.index("--text") + 1], "kind": found[found.index("--kind") + 1]}


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    registry = home / "long-running/drives/durable.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"drive": "durable", "cci_drive": DRIVE, "sessions": ["s-old", SESSION]}))
    (home / "tasks" / SESSION).mkdir(parents=True)
    path = tmp_path / "runner.json"
    path.write_text(
        json.dumps(
            {
                "store": str(tmp_path / "store"),
                "drive": DRIVE,
                "orca": {"desk_inbox": str(tmp_path / "inbox/orca-desk.md")},
                "landing": {"repo": REPO, "ledger": "abc", "checkout": str(tmp_path / "checkout"), "fidelity_lane": "fidelity-desk", "merge_mode": "every", "trunk_check": "buildkite/test"},
                "stall": {},
            }
        )
    )
    return path


def task(tmp_path: Path, number: int, owner: str, status: str = "in_progress", minutes_ago: float = 60) -> None:
    path = tmp_path / "claude/tasks" / SESSION / f"{number}.json"
    path.write_text(json.dumps({"id": str(number), "subject": f"lane {owner}", "status": status, "owner": owner}))
    written = (NOW - timedelta(minutes=minutes_ago)).timestamp()
    os.utime(path, (written, written))


def opened(config: Path) -> dict:
    return json.loads((config.parent / "inbox/stall-watch.json").read_text())["open"]


def watch(shell: FakeShell, config: Path) -> None:
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(stall_watch.judge, "batch", shell.batch)
        assert stall_watch.main(["--config", str(config)], shell) == 0


def test_a_command_is_handed_a_deadline_before_the_watch_kills_it_so_a_script_ends_its_own_commands_first():
    before = time.time()

    done = stall_watch.Shell().run([sys.executable, "-c", f"import os; print(os.environ[{drive.DEADLINE_ENV!r}])"], timeout=30)

    caller, _, deadline = done.out.strip().partition(":")
    assert caller == str(os.getpid())
    assert round(before + 30 - drive.REAP_SECONDS, 3) <= float(deadline) <= time.time() + 30 - drive.REAP_SECONDS


def test_a_stall_reaches_the_live_successor_of_the_lane_that_posted_the_prs_newest_head(config):
    """Root #215820: stalls went to n-enumeration-1 and port-memory-1, dead since their successors took over."""
    rotated = record(1, "port-memory-1", "head", f"HEAD #101 {HEAD}", 40, "101")
    shell = FakeShell([rotated, fidelity(2, "101", HEAD, 35), record(3, "port-memory-3", "state", "took over port-memory-1's PRs", 5)], [row("101", test_state="pending")])

    watch(shell, config)

    assert shell.post("finder:101:abc1234")["to"] == "port-memory-3@d1"


def test_a_head_no_finder_names_posts_one_urgent_record_to_root_and_its_lane_until_a_review_clears_it(config):
    shell = FakeShell([head(1, "101", HEAD, 40), fidelity(2, "101", HEAD, 35), head(3, "102", OTHER, 9), fidelity(4, "102", OTHER, 8)], [row("101", test_state="pending"), row("102", OTHER, test_state="pending")])

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == ["finder:101:abc1234"]
    assert shell.post("finder:101:abc1234")["kind"] == "urgent"
    assert shell.post("finder:101:abc1234")["to"] == "api-lane@d1"
    assert shell.post("finder:101:abc1234")["text"].startswith("STALL finder #101 at abc1234, 40 min: no finder review names this head")
    shell.records.append(finder(5, "101", HEAD, 1))
    watch(shell, config)
    assert "finder:101:abc1234" not in opened(config)
    shell.rows[0] = row("101", OTHER, test_state="pending")
    shell.records += [head(6, "101", OTHER, 31), fidelity(7, "101", OTHER, 25)]
    watch(shell, config)
    assert shell.topics() == ["finder:101:abc1234", "finder:101:fedcba9"]


@pytest.mark.parametrize(("green", "stalls"), [(5, []), (11, ["fidelity:101:abc1234"])])
def test_a_green_head_with_no_fidelity_grade_for_ten_minutes_alarms_the_graders_and_root_whatever_the_desk_holds_queued(config, tmp_path, green, stalls):
    desk_job(tmp_path, "desk-fidelity", "fidelity", "101", HEAD, None)
    shell = FakeShell([head(1, "101", HEAD, 20), finder(2, "101", HEAD, 15)], [row("101", green_at=stamp(green))])

    watch(shell, config)

    assert [topic for topic in shell.topics() if not topic.startswith("ungraded:")] == stalls
    for topic in stalls:
        assert shell.post(topic)["to"] == "fidelity-desk@d1"
        assert shell.post(topic)["text"].startswith(f"STALL ungraded #101 at abc1234, {green} min: its build has read green since")
        assert shell.post(f"ungraded:{actions.stamp(NOW)}") == {"to": "root", "kind": "urgent", "text": "STALL ungraded, 1 green head past 10 min with no grade: #101 at abc1234 has no fidelity grade, green since 12:49 PM, owner fidelity-desk | each owner heard its own line"}


@pytest.mark.parametrize(("named", "stalls"), [(5, []), (40, ["finder:101:abc1234"])])
def test_a_new_head_counts_from_the_first_record_naming_it_never_from_an_older_record_with_no_sha(config, named, stalls):
    bulk = record(1, "api-lane", "head", "HEAD api-lane: #101 conflict fix, then the stack above restacked onto it. Published 9:00 AM.", 600, "101")
    ready = record(2, "api-lane", "report", f"[101] clean READY #101 at {HEAD[:10]}", named, "101")
    shell = FakeShell([bulk, ready, fidelity(3, "101", HEAD, 1)], [row("101", test_state="pending")])

    watch(shell, config)

    assert shell.topics() == stalls
    if stalls:
        assert "no finder review names this head, first named 12:20 PM in #2" in shell.post("finder:101:abc1234")["text"]


def test_a_source_that_does_not_read_keeps_its_stall_open_so_nothing_posts_twice(config):
    shell = FakeShell([head(1, "101", HEAD, 40), fidelity(2, "101", HEAD, 35)], [row("101", test_state="pending")])

    watch(shell, config)
    shell.ledger_code = 1
    watch(shell, config)
    shell.ledger_code = 0
    watch(shell, config)

    assert shell.topics() == ["finder:101:abc1234"]


def test_a_root_go_no_queued_record_names_goes_to_whoever_holds_its_next_step_and_backlog_draws_none(config):
    records = [head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(4, "root", "go", f"go #101 at {HEAD[:7]}", 12)]
    records += [head(5, "102", OTHER, 30), finder(6, "102", OTHER, 25), fidelity(7, "102", OTHER, 25), record(8, "root", "go", f"go #102 at {OTHER[:7]}", 12)]
    shell = FakeShell(records, [row("101"), row("102", OTHER, test_state="failure")])

    watch(shell, config)

    assert shell.topics() == ["go:102:fedcba9"]
    assert shell.post("go:102:fedcba9")["to"] == "api-lane@d1"
    assert "fix the red build of #102 at fedcba9" in shell.post("go:102:fedcba9")["text"]


@pytest.mark.parametrize(
    ("title", "step"),
    [
        ("🛑 Held — 2 blocking open; security: high", "#101 at abc1234: the review bot holds it on 2 blocking findings; api-lane fixes or accepts them"),
        ("ℹ️ Waiting for restack — push or comment @forge-pr-reviewer review", "#101 at abc1234: the review bot waits for a restack push"),
    ],
)
def test_a_go_whose_green_head_the_review_bot_holds_or_waits_to_see_restacked_goes_to_its_lane_not_the_landing_desk(config, title, step):
    records = [head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(4, "root", "go", f"go #101 at {HEAD[:7]}", 12)]
    shell = FakeShell(records, [row("101", ai_review="neutral")])
    shell.review_titles[HEAD] = title

    watch(shell, config)

    assert shell.post("go:101:abc1234")["to"] == "api-lane@d1"
    assert f"owner api-lane | {step}" in shell.post("go:101:abc1234")["text"]


def test_gos_stacked_on_one_parent_post_once_naming_the_parent_and_a_held_parent_draws_none(config, tmp_path):
    merge_mode(config, "ask")
    records = [record(1, "root", "go", f"go #202 at {OTHER[:7]}", 12), record(2, "root", "go", f"go #203 at {OLD[:7]}", 11), record(3, "root", "go", f"go #302 at {OTHER[:7]}", 12), head(10, "201", HEAD, 40), finder(11, "201", HEAD, 39), fidelity(12, "201", HEAD, 39), hold(900, "hold #301 waits on the schema release", "301")]
    rows = [row("201"), row("202", OTHER, base="b201"), row("203", OLD, base="b202"), row("301"), row("302", OTHER, base="b301")]
    shell = FakeShell(records, rows)

    watch(shell, config)

    assert shell.topics() == ["go:201:abc1234"]
    assert shell.post("go:201:abc1234")["to"] == "root"
    said = f"root gos name #202, #203 in the stack from #201 (#202: go #1 by root at {grades.clock(records[0]['at'])}), and no QUEUED record does | owner root | #201 at abc1234: no go names this head, and a stack enqueues from the bottom"
    assert said in shell.post("go:201:abc1234")["text"]


def test_a_root_decide_that_opens_with_a_go_at_the_head_counts_as_its_go(config):
    """Root #202036 went to #44922 at 519ea97 as a decide opening `go ... TAP-KEPT`, and the watch still raised go and carry stalls on that head (#202033, #202103)."""
    records = [record(1, "root", "go", f"go #202 at {OTHER[:7]}", 30), record(2, "root", "decide", f"12:32 AM go #201 at {HEAD[:7]} TAP-KEPT: no new behavior since the tap at {OLD[:7]}", 30)]
    shell = FakeShell(records, [row("201"), row("202", OTHER, base="b201")])

    watch(shell, config)

    assert shell.topics() == ["go:201:abc1234"]
    assert "#201 at abc1234: no go names this head" not in shell.post("go:201:abc1234")["text"]
    assert "the landing runner wrote no QUEUED, MERGED or blocker record for #201 at abc1234" in shell.post("go:201:abc1234")["text"]


def test_a_pr_below_a_go_that_waits_only_on_the_owners_tap_is_no_go_stall(config):
    """#200414 raised a go stall to root for #45334, whose merge row already waited on the owner's tap."""
    shell = FakeShell([record(1, "root", "go", f"go #202 at {OTHER[:7]}", 30)], [row("201"), row("202", OTHER, base="b201")])
    shell.doc = {"blocks": [{"id": f"merge:201:{HEAD[:7]}", "type": "card", "children": []}]}

    watch(shell, config)

    assert not [topic for topic in shell.topics() if topic.startswith("go:")]


@pytest.mark.parametrize(
    ("mode", "reviewed", "stalls"),
    [("every", True, []), ("ask", False, []), ("ask", True, ["go:201:abc1234"])],
    ids=["tap-row-under-merge-mode-every", "no-finder-review", "graded-and-waits-on-root"],
)
def test_a_pr_below_a_go_draws_a_go_stall_only_when_its_next_step_is_roots_go(config, mode, reviewed, stalls):
    """Root #220563: a stack bottom with no grades waits on its grades, not on root; #224791 and #224880 raised go stalls to root for a bottom whose next step was its re-ask on the owner's board."""
    merge_mode(config, mode)
    below = [head(10, "201", HEAD, 40), record(11, "fidelity-desk", "review", f"fidelity: ok {HEAD[:7]} new-tree fix, stays a tap row", 39, "201"), *([finder(12, "201", HEAD, 39)] if reviewed else [])]
    shell = FakeShell([*below, record(1, "root", "go", f"go #202 at {OTHER[:7]}", 30)], [row("201"), row("202", OTHER, base="b201")])

    watch(shell, config)

    assert [topic for topic in shell.topics() if topic.startswith("go:")] == stalls


@pytest.mark.parametrize(
    "note",
    [
        f"TAP-NOT-CARRIED #101: the owner's tap at {OLD[:10]} (1:14 PM Oct 30 Pacific) on board block merge-prs does not carry to {HEAD[:10]}, since cci #2 says that head adds new behavior",
        f"TAP-KEPT #101: the owner's tap at {OLD[:10]} on board block merge-prs keeps at {HEAD[:10]}, a review fix with no new behavior per fidelity grade cci #3 (rule 399b5fa)",
        f"TAP-CARRIED #101: the owner's tap at {OLD[:10]} on board block merge-prs now stands at {HEAD[:10]}, a patch-identical restack, per cci #2",
    ],
    ids=["not-carried", "kept", "carried"],
)
def test_a_carry_the_desk_runners_tap_note_settles_at_the_head_draws_no_carry_stall_and_asks_sonnet_nothing(config, note):
    """#224789, #224790 and #224879 raised carry stalls to root on heads whose desk runner TAP-NOT-CARRIED or TAP-KEPT note already settled the carry (root #224826)."""
    fixed = f"HEAD #101 {HEAD} (was {OLD}): one added commit, NEW BEHAVIOR: the startup pass runs as its own task"
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 40, fixed), record(3, "desk-runner", "note", note, 30, "101", to=["root", "board-refresher-2"])], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}

    watch(shell, config)

    assert not [topic for topic in shell.topics() if topic.startswith("carry:")] and shell.prompts == []


def test_a_desk_runner_tap_note_about_another_head_leaves_the_carry_stall_standing(config):
    fixed = f"HEAD #101 {HEAD} (was {OLD}): answers the reviewer bot's finding with a nil check and its test"
    note = f"TAP-NOT-CARRIED #101: the owner's tap at {OLD[:10]} (1:14 PM Oct 30 Pacific) on board block merge-prs does not carry to {OTHER[:10]}, since cci #2 says that head adds new behavior"
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 40, fixed), record(3, "desk-runner", "note", note, 30, "101")], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}

    watch(shell, config)

    assert shell.post("carry:101:abc1234")["to"] == "root"


def test_a_go_the_runner_answered_with_a_merge_ask_is_no_go_stall(config):
    records = [record(1, "root", "go", f"go #101 at {HEAD[:7]}", 30), record(2, "desk-runner", "blocker", f"MERGE-ASK #101 at {HEAD[:10]}: the owner's merge row waits on his tap", 20)]
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert not [topic for topic in shell.topics() if topic.startswith("go:")]


def test_gos_above_a_green_unqueued_bottom_post_one_stall_naming_the_first_pr_whose_build_still_runs(config):
    records = [record(1, "root", "go", f"go #401 at {HEAD[:7]}", 30), record(2, "root", "go", f"go #402 at {OTHER[:7]}", 30), record(3, "root", "go", f"go #403 at {OLD[:7]}", 30)]
    rows = [row("401"), row("402", OTHER, base="b401", test_state="pending"), row("403", OLD, base="b402", test_state="pending")]
    shell = FakeShell(records, rows)

    watch(shell, config)

    assert shell.topics() == ["go:402:fedcba9"]
    assert "#402 at fedcba9 has no green build yet" in shell.post("go:402:fedcba9")["text"]


@pytest.mark.parametrize(("graded", "stalls"), [(3, []), (15, ["go:101:abc1234"])])
def test_a_go_a_fidelity_carry_moved_to_a_new_head_stalls_only_once_the_carry_grade_is_old(config, graded, stalls):
    carry = record(3, "fidelity-desk", "review", f"fidelity: ok {HEAD[:7]} carried from {OLD[:7]}, patch-identical replay onto dev.", graded, "101")
    blocker = record(4, "desk-runner", "blocker", f"#101 {HEAD[:10]}: nothing in #101 was enqueued; FINDER-PENDING #101: no `finder:` review record names this head", 1, "101", to=["api-lane", "finder-desk"], topic="101")
    shell = FakeShell([record(1, "root", "go", f"go #101 at {OLD[:7]}", 60), head(2, "101", HEAD, 40), carry, blocker], [row("101")])

    watch(shell, config)

    assert shell.topics() == stalls


def test_a_go_the_landing_desk_queued_at_its_head_is_no_stall(config):
    records = [head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(4, "root", "go", f"go #101 at {HEAD[:7]}", 12)]
    records.append(record(5, "desk-runner", "report", f"QUEUED #101 at {HEAD[:10]} (go #4): the landing runner put it in the merge queue at 12:52 PM", 8))
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert shell.topics() == []


def test_a_go_the_landing_desk_enqueued_by_hand_at_its_head_is_no_stall(config):
    records = [head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(4, "root", "go", f"go #101 at {HEAD[:7]}", 12)]
    records.append(record(5, "landing-desk-3", "report", f"QUEUED #101 at {HEAD[:10]} (go #4): enqueued by hand after the runner skipped it", 8))
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert shell.topics() == []

def gone(pr: str, sha: str) -> list[dict]:
    return [head(1, pr, sha, 30), finder(2, pr, sha, 25), fidelity(3, pr, sha, 25), record(4, "root", "go", f"go #{pr} at {sha[:7]}", 12)]


def test_a_go_the_runner_refused_names_its_newest_open_blocker_for_that_head_and_goes_to_that_blockers_lanes(config):
    records = gone("101", HEAD)
    records.append(record(5, "desk-runner", "blocker", f"#101 {OLD[:10]}: nothing in #101 was enqueued; BUILD-FAILED #101: an older head", 11, to=["api-lane"], topic="101"))
    records.append(record(6, "desk-runner", "blocker", f"#101 {HEAD[:10]}: nothing in #101 was enqueued; FINDER-PENDING #101: an answered finding", 10, to=["api-lane"], topic="101", status="closed"))
    records.append(record(7, "desk-runner", "blocker", f"#101 {HEAD[:10]}: nothing in #101 was enqueued; GO-UNQUEUED #101: the merge queue refused it at this head", 9, to=["api-lane", "board-refresher"], topic="101", status="open"))
    records.append(record(8, "desk-runner", "note", f"#101 {HEAD[:10]}: WARNING `rulings.py check-pr` warns at {HEAD[:10]}, and the head is not held for it", 8, to=["api-lane"], topic="101"))
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    post = shell.post("go:101:abc1234")
    assert post["to"] == "root,api-lane@d1,board-refresher@d1"
    assert post["text"].endswith(f"| owner api-lane, board-refresher | GO-UNQUEUED: the runner's newest blocker for #101 at abc1234, cci #7: #101 {HEAD[:10]}: nothing in #101 was enqueued; GO-UNQUEUED #101: the merge queue refused it at this head")


@pytest.mark.parametrize("withdrawn", [False, True])
def test_a_root_correction_withdrawing_a_go_ends_its_go_stall(config, withdrawn):
    """#243593: root withdrew go #237906 on #46228 with a correction, not a withdraw, and the stall watch still counted the go."""
    records = gone("101", HEAD)
    records.append(record(5, "desk-runner", "blocker", f"#101 {HEAD[:10]}: nothing in #101 was enqueued; GO-UNQUEUED #101: the merge queue refused it at this head", 9, to=["api-lane"], topic="101", status="open"))
    if withdrawn:
        records.append(record(6, "root", "correction", "Withdraw the go in #4; the finder clear stands. I had not verified a tap.", 8, re=4))
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert bool([topic for topic in shell.topics() if topic.startswith("go:101")]) != withdrawn


def finder_pass(tmp_path: Path, pr: str, sha: str, status: str) -> None:
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("desk-finder", "desk-finder", 0, stamp(30), {}))
    with store.transaction("desk-finder", generation=0) as incident:
        incident.accept(f"finder:{pr}:{sha}", "finder", json.dumps({"pr": pr, "head": sha, "lane": "api-lane", "seq": 1, "full": False}), "cci#1", NOW)
        if status == "started":
            incident.start(f"finder:{pr}:{sha}", NOW)


@pytest.mark.parametrize(("status", "stalls"), [("accepted", []), ("started", []), (None, ["go:101:abc1234"])])
def test_a_go_held_finder_pending_draws_no_stall_while_the_finder_desk_holds_a_pass_on_its_head(config, tmp_path, status, stalls):
    if status:
        finder_pass(tmp_path, "101", HEAD, status)
    records = [*gone("101", HEAD)[:1], *gone("101", HEAD)[3:]]
    records.append(record(5, "desk-runner", "blocker", f"#101 {HEAD[:10]}: nothing in #101 was enqueued; FINDER-PENDING #101: no `finder:` review record names this head", 9, to=["api-lane", "finder-desk"], topic="101"))
    shell = FakeShell([*records, fidelity(6, "101", HEAD, 25)], [row("101")])

    watch(shell, config)

    assert [topic for topic in shell.topics() if topic.startswith("go:")] == stalls
    if stalls:
        assert "| owner api-lane, finder-desk | FINDER-PENDING: the runner's newest blocker for #101 at abc1234, cci #5:" in shell.post("go:101:abc1234")["text"]


def test_a_graded_pr_missing_from_the_board_belongs_to_the_drives_board_lane_not_root(config):
    raw = json.loads(config.read_text())
    raw["landing"]["board_lane"] = "board-refresher-2"
    config.write_text(json.dumps(raw))
    shell = FakeShell([head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20)], [row("101")])
    watch(shell, config)
    assert "| owner board-refresher-2 |" in shell.post("board:101:abc1234")["text"]


def test_a_graded_pr_missing_from_the_board_and_a_tap_no_go_follows_each_post_until_the_board_and_root_catch_up(config):
    records = [head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20), head(4, "102", OTHER, 40), finder(5, "102", OTHER, 30), fidelity(6, "102", OTHER, 30)]
    shell = FakeShell(records, [row("101"), row("102", OTHER)])
    shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 102, "head": OTHER[:7]}]}]}]}
    shell.triage = {"merge-prs": {"102": {"verdict": "approved", "head": OTHER[:7], "at": stamp(12)}}}

    watch(shell, config)

    assert shell.topics() == ["board:101:abc1234", "tap:102:fedcba9"]
    assert "the owner's board holds no row or card for it at this head" in shell.post("board:101:abc1234")["text"]
    assert "post `go #102 at fedcba9`" in shell.post("tap:102:fedcba9")["text"]
    shell.doc["blocks"].append({"id": f"merge:101:{HEAD[:7]}", "type": "card", "children": []})
    shell.records.append(record(7, "root", "go", f"go #102 at {OTHER[:7]}", 1))
    watch(shell, config)
    assert opened(config) == {}


def test_a_go_from_a_numbered_successor_of_a_go_lane_settles_the_tap(config):
    raw = json.loads(config.read_text())
    raw["landing"]["go_lanes"] = ["go-sweep-5"]
    config.write_text(json.dumps(raw))
    records = [head(1, "102", OTHER, 40), finder(2, "102", OTHER, 30), fidelity(3, "102", OTHER, 30), record(4, "go-sweep-6", "go", f"go #102 at {OTHER[:7]}", 1)]
    shell = FakeShell(records, [row("102", OTHER)])
    shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 102, "head": OTHER[:7]}]}]}]}
    shell.triage = {"merge-prs": {"102": {"verdict": "approved", "head": OTHER[:7], "at": stamp(12)}}}

    watch(shell, config)

    assert "tap:102:fedcba9" not in shell.topics()


def test_a_tap_at_an_earlier_head_asks_sonnet_once_whether_the_new_head_replays_it(config):
    restack = f"HEAD #101 {HEAD} (was {OLD}): restack, range-diff patch-identical (3 commits)"
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 20, restack)], [row("101", test_state="pending")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}
    shell.answer = lambda question: {"restack": True}

    watch(shell, config)
    watch(shell, config)

    assert len(shell.prompts) == 1
    assert restack in shell.prompts[0]
    assert [topic for topic in shell.topics() if topic.startswith("carry:")] == ["carry:101:abc1234"]
    assert shell.post("carry:101:abc1234")["to"] == "fidelity-desk@d1"
    assert "post `fidelity: ok abc1234 carried from 0123456`" in shell.post("carry:101:abc1234")["text"]


@pytest.mark.parametrize(
    ("answer", "step"),
    [
        ({"fix": True}, "his tap at 0123456 keeps over a fix with no new behavior (rule 399b5fa): root posts `go #101 at abc1234` naming the fix once fidelity grades it, and the row lists the fix"),
        ({}, "re-ask the owner at abc1234 unless a record shows it adds no new behavior"),
    ],
)
def test_a_tap_at_an_earlier_head_keeps_over_a_fix_with_no_new_behavior_and_only_new_behavior_re_asks_the_owner(config, answer, step):
    fixed = f"HEAD #101 {HEAD} (was {OLD}): answers the reviewer bot's finding with a nil check and its test"
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 20, fixed)], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}
    shell.answer = lambda question: answer

    watch(shell, config)

    assert shell.post("carry:101:abc1234")["to"] == "root"
    assert f"| owner root | {step}" in shell.post("carry:101:abc1234")["text"]


def test_a_root_go_that_opens_with_its_clock_time_settles_the_carry_of_its_head(config):
    fixed = f"HEAD #101 {HEAD} (was {OLD}): answers the reviewer bot's finding with a nil check and its test"
    go = record(3, "root", "go", f"1:19 AM go #101 at {HEAD} (his tap kept per the re-tap rule, 399b5fa).", 18, "101")
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 40, fixed), go], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}
    shell.answer = lambda question: {}

    watch(shell, config)

    assert not [topic for topic in shell.topics() if topic.startswith("carry:")] and shell.prompts == []


def test_a_failed_sonnet_call_leaves_the_carry_to_both_owners_and_asks_again_next_pass(config):
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 20)], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}
    shell.judge_fails = True

    watch(shell, config)
    watch(shell, config)

    assert len(shell.prompts) == 2
    assert shell.post("carry:101:abc1234")["to"] == "root,fidelity-desk@d1"
    assert "if it is a patch-identical restack; if it adds no new behavior, his tap keeps (rule 399b5fa) and root posts `go #101 at abc1234` naming the fix once fidelity grades it, and the row lists the fix; else root re-asks the owner at abc1234" in shell.post("carry:101:abc1234")["text"]


def test_one_sonnet_call_judges_every_quiet_lane_and_only_a_kept_promise_or_a_named_wait_holds_off_its_stall(config, tmp_path):
    for number, lane in enumerate(["lane-a", "lane-b", "lane-c", "lane-d", "lane-e"], start=1):
        task(tmp_path, number, lane, minutes_ago=45)
    task(tmp_path, 6, "lane-f", status="completed")
    task(tmp_path, 7, "lane-g", minutes_ago=5)
    records = [
        record(1, "lane-a", "state", "pushed the fix", 40),
        record(2, "lane-b", "state", "waiting on CI build 123, about an hour", 35),
        record(3, "lane-c", "state", "next state by 12:50 PM", 20),
        record(4, "lane-e-2", "state", "rebasing now", 2),
        record(5, "lane-f", "state", "done earlier", 300),
    ]
    shell = FakeShell(records, [])
    verdicts = {"lane-a": {"explained": False}, "lane-b": {"explained": True}, "lane-c": {"promised": stamp(10)}}
    shell.answer = lambda question: verdicts[question["lane"]]

    watch(shell, config)

    assert len(shell.prompts) == 1
    assert sorted(json.loads(shell.prompts[0].split("Questions:\n", 1)[1]), key=lambda question: question["lane"]) == [
        {"kind": "silence", "lane": "lane-a", "last_at": stamp(40), "minutes_silent": 40, "last_record": "pushed the fix", "id": "lane:lane-a:1"},
        {"kind": "silence", "lane": "lane-b", "last_at": stamp(35), "minutes_silent": 35, "last_record": "waiting on CI build 123, about an hour", "id": "lane:lane-b:2"},
        {"kind": "silence", "lane": "lane-c", "last_at": stamp(20), "minutes_silent": 20, "last_record": "next state by 12:50 PM", "id": "lane:lane-c:3"},
    ]
    assert sorted(shell.topics()) == ["lane:lane-a", "lane:lane-c", "lane:lane-d"]
    assert shell.post("lane:lane-a")["to"] == "lane-a@d1"
    assert "promised its next state by 12:50 PM" in shell.post("lane:lane-c")["text"]
    assert shell.post("lane:lane-d")["to"] == "root"
    assert "it owns task #4 in progress, given it by 12:15 PM, and has posted nothing since" in shell.post("lane:lane-d")["text"]


def test_an_open_ask_to_root_stalls_until_root_answers_it(config):
    records = [
        record(1, "api-lane", "ask", "which head do I rebase on?", 20, status="open"),
        record(2, "api-lane", "ask", "may I drop the shim?", 20, status="open"),
        record(3, "root", "decide", "drop it", 5, re=2),
        record(4, "api-lane", "ask", "is the release out?", 10, status="open"),
        record(5, "api-lane", "ask", "already closed", 30, status="closed"),
        record(6, "api-lane", "ask", "to a peer", 30, status="open", to=["fidelity-desk"]),
    ]
    shell = FakeShell(records, [])

    watch(shell, config)

    assert shell.topics() == ["ask:1"]
    assert "answer it with `cci post --re '#1'`" in shell.post("ask:1")["text"]


def needs_lane(seq: int, block: str, minutes_ago: float, status: str = "open") -> dict:
    return record(seq, "board-watch", "ask", f"NEEDS-LANE {block} on board s-root: the owner picked \u201cyes\u201d", minutes_ago, topic=f"needs-lane:{block}", status=status, fields={"attrs": {"block": block}})


def test_an_owner_pick_no_lane_binds_stalls_to_root_once_at_fifteen_minutes_and_clears_once_a_lane_claims_it(config, tmp_path):
    """Root #225532: three owner picks sat two hours with no lane; a spawn whose brief names the pick, a claim, a lane's record or an answer binds it, and board-watch's own records never do."""
    brief = tmp_path / "pick-lane.md"
    brief.write_text("Route the owner's pick #4 to a fix.")
    records = [
        needs_lane(1, "share-pick", 20),
        needs_lane(2, "claimed-pick", 20),
        record(3, "api-lane", "claim", "taking claimed-pick", 10),
        needs_lane(4, "spawned-pick", 20),
        record(5, "root", "state", f"SPAWNED pick-lane {brief}", 10, topic="spawn:pick-lane"),
        record(6, "pick-lane", "state", "started", 9),
        needs_lane(7, "fresh-pick", 5),
        needs_lane(8, "closed-pick", 30, status="closed"),
        needs_lane(9, "answered-pick", 20),
        record(10, "root", "decide", "no lane: the owner withdrew it", 5, re=9),
        needs_lane(11, "noted-pick", 20),
        record(12, "board-watch", "owner", "Owner on board s-root, noted-pick: wrote \u201cmore\u201d", 10, topic="noted-pick"),
    ]
    shell = FakeShell(records, [])

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == ["pick:1", "pick:11"]
    assert shell.post("pick:1")["to"] == "root"
    assert shell.post("pick:1")["kind"] == "urgent"
    assert shell.post("pick:1")["text"].startswith("STALL pick owner pick share-pick, 20 min: board-watch asked root for a lane at ")
    assert shell.post("pick:1")["text"].endswith("| owner root | spawn a lane whose brief names share-pick or #1, or have a lane post a `claim` naming it; close it with `cci post --resolves '#1'`")
    shell.records.append(record(13, "share-lane", "state", "working share-pick", 0))
    watch(shell, config)
    assert "pick:1" not in opened(config)
    assert "pick:11" in opened(config)


def alive(seq: int, desk: str, minutes_ago: float) -> dict:
    restart = f"nohup desk-runner.py run --config /drive/runner.json --desk {desk} >> /drive/{desk}-runner.log 2>&1 < /dev/null &"
    return record(seq, "desk-runner", "state", f"{desk} alive 9:00 PM pass 4", minutes_ago, topic=desk, to=[f"drive-watchdog@{DRIVE}"], fields={"attrs": {"pid": "73091", "restart": restart}})


def test_a_desk_runner_whose_alive_record_went_silent_stalls_to_root_with_its_restart_command_wherever_it_runs(config):
    """Oct 29: the runners must move off this Mac, so the stall watch reads their alive records, never a heartbeat file (cci #199401)."""
    shell = FakeShell([alive(1, "orca", 30), alive(2, "landing", 25), alive(3, "orca", 1)], [])

    watch(shell, config)

    assert shell.topics() == ["desk:landing"]
    assert shell.post("desk:landing")["to"] == "root"
    assert shell.post("desk:landing")["text"].startswith("STALL desk landing desk runner, 25 min: the landing desk runner has posted no `landing alive` record since ")
    assert shell.post("desk:landing")["text"].endswith("restart it from the Run's bound terminal: `nohup desk-runner.py run --config /drive/runner.json --desk landing >> /drive/landing-runner.log 2>&1 < /dev/null &`")
    shell.records.append(alive(4, "landing", 0))
    watch(shell, config)
    assert "desk:landing" not in opened(config)


def spawn(seq: int, lane: str, minutes_ago: float, brief: str = "/briefs/lane.md") -> dict:
    return record(seq, "root", "state", f"SPAWNED {lane} {brief}", minutes_ago, topic=f"spawn:{lane}")


def test_a_spawned_lane_that_neither_posts_nor_binds_within_three_minutes_stalls_to_root_naming_its_brief(config):
    """A spawned lane that never registered with cci sat unnoticed until the root looked; the spawn record and cci who name it at three minutes."""
    shell = FakeShell([spawn(1, "lane-quiet", 5, "/briefs/quiet.md"), spawn(2, "lane-posted", 5), record(3, "lane-posted", "state", "started", 4), spawn(4, "lane-bound", 5), spawn(5, "lane-fresh", 2)], [])
    shell.peers = [{"lane": "lane-bound", "seen_at": stamp(1)}, {"lane": "lane-quiet", "seen_at": stamp(60)}, {"session": "s-other"}]

    watch(shell, config)

    assert shell.topics() == ["unregistered:lane-quiet"]
    assert shell.post("unregistered:lane-quiet")["to"] == "root"
    assert shell.post("unregistered:lane-quiet")["text"].startswith("STALL unregistered lane-quiet, 5 min: spawned ")
    assert "from /briefs/quiet.md, and has neither posted a cci record nor been bound as lane-quiet since" in shell.post("unregistered:lane-quiet")["text"]
    shell.records.append(record(6, "lane-quiet", "state", "started", 0))
    watch(shell, config)
    assert "unregistered:lane-quiet" not in opened(config)


def test_a_sub_worker_of_a_live_lane_stalls_to_that_lane_never_to_root(config):
    """Root #235327: a sub-worker whose name starts with a live lane's name is that lane's own, so its unregistered stall goes to the parent lane, which has it post one state line first; a numbered successor and a lane whose parent ended still go to root."""
    shell = FakeShell([record(1, "mq-health-4", "state", "supervising", 6), spawn(2, "mq-health-4-supervise", 5), record(3, "mq-health-4", "state", "still supervising", 1), spawn(4, "mq-health-4-2", 5), record(5, "gone-1", "done", "finished", 6), spawn(6, "gone-1-helper", 5)], [])
    shell.peers = []

    watch(shell, config)

    assert shell.post("unregistered:mq-health-4-supervise")["to"] == "mq-health-4@d1"
    assert shell.post("unregistered:mq-health-4-supervise")["text"].endswith("| owner mq-health-4 | mq-health-4 has mq-health-4-supervise post one state line as its first act")
    assert shell.post("unregistered:mq-health-4-2")["to"] == "root"
    assert shell.post("unregistered:gone-1-helper")["to"] == "root"


def test_untested_shape_rulings_stall_to_root_once_they_are_half_an_hour_old(config):
    """Owner, Oct 30 5:21 AM: one local replica alongside one local primary, everything a client of it; 144 chats had started 146 replication processes, and no test held the count."""
    shell = FakeShell([], [])
    shell.shapes = [{"key": "rule/0f31b56", "updated_at": stamp(45)}, {"key": "prompt/1026-184423", "updated_at": stamp(31)}, {"key": "rule/fresh00", "updated_at": stamp(5)}]

    watch(shell, config)

    assert shell.topics() == ["shape:prompt/1026-184423,rule/0f31b56"]
    assert shell.post("shape:prompt/1026-184423,rule/0f31b56")["to"] == "root"
    assert shell.post("shape:prompt/1026-184423,rule/0f31b56")["text"].startswith("STALL shape 2 shape rulings, 45 min: no CI test is recorded for prompt/1026-184423, rule/0f31b56, ")
    shell.shapes = []
    watch(shell, config)
    assert not [key for key in opened(config) if key.startswith("shape:")]


def test_a_held_pr_draws_no_stall(config, tmp_path):
    shell = FakeShell([head(1, "101", HEAD, 40), hold(900, "hold #101 waits on the schema release", "101")], [row("101"), row("102", OTHER, hold_until=stamp(-60))])
    shell.records.append(head(2, "102", OTHER, 40))

    watch(shell, config)

    assert shell.topics() == []


def test_a_red_head_goes_to_its_live_lane_every_half_hour_to_root_once_when_no_live_lane_owns_it_and_probe_reds_fold_into_one_daily_line(config, tmp_path):
    red = {"name": "buildkite/test", "url": "https://buildkite.com/forge/test/builds/9", "job": "Lint (fmt, clippy)", "error": "error: used `expect()` on a `Result` value", "probe": False}
    probe = {"name": "buildkite/test", "url": "https://buildkite.com/forge/test/builds/8", "job": "Run api tests", "error": "buildkite-agent: fatal: failed to get step: 404 Not Found", "probe": True}
    bot = {"name": "ai-review", "url": "https://github.com/Forge-AI/monorepo/runs/7"}
    records = [record(1, "api-lane", "state", "working on #101", 5), record(2, "root", "answer", "root reassigns #105 (gone-lane) to api-lane; #106 to other-lane", 4), record(3, "old-lane-2", "state", "took over #107", 3), hold(900, "hold #103 waits on the schema release", "103")]
    raw = json.loads(config.read_text())
    config.write_text(json.dumps(raw | {"stall": {"minutes": {"unclassed": 600, "sweep": 600}}}))
    shell = FakeShell(
        records,
        [
            row("101", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([red, bot])),
            row("102", OTHER, test_state="failure", red_at=stamp(60), failing_checks=json.dumps([bot])),
            row("103", OLD, lane="gone-lane", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([red])),
            row("104", lane="gone-lane", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([probe])),
            row("105", lane="gone-lane", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([red])),
            row("107", OTHER, lane="old-lane", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([red])),
        ],
    )

    watch(shell, config)
    assert shell.topics() == []

    shell.clock = NOW + timedelta(minutes=2)
    watch(shell, config)
    assert sorted(shell.topics()) == ["probe:2026-10-27", "red:101:abc1234", "red:103:0123456:root", "red:105:abc1234", "red:107:fedcba9"]
    assert shell.post("red:107:fedcba9")["to"] == "old-lane-2@d1"
    assert shell.post("red:101:abc1234") == {
        "to": "api-lane@d1",
        "kind": "blocker",
        "text": "STALL red #101 at abc1234, 3 min: buildkite/test has read failed in job Lint (fmt, clippy) since 12:59 PM: error: used `expect()` on a `Result` value (https://buildkite.com/forge/test/builds/9) | owner api-lane | fix the red build of #101 at abc1234 and push",
    }
    assert shell.post("red:105:abc1234")["to"] == "api-lane@d1"
    assert shell.post("red:103:0123456:root")["to"] == "root"
    assert shell.post("red:103:0123456:root")["text"].endswith("| owner root | assign the red build of #103 at 0123456 to a live lane")
    assert shell.post("probe:2026-10-27")["to"] == "root" and shell.post("probe:2026-10-27")["kind"] == "report"
    assert "#104 at abc1234 (gone-lane, https://buildkite.com/forge/test/builds/8)" in shell.post("probe:2026-10-27")["text"]

    shell.clock = NOW + timedelta(minutes=20)
    watch(shell, config)
    assert len(shell.posts) == 5

    shell.clock = NOW + timedelta(minutes=33)
    watch(shell, config)
    assert sorted(shell.topics()[5:]) == ["red:101:abc1234", "red:105:abc1234", "red:107:fedcba9"]

    shell.rows[0] = row("101")
    watch(shell, config)
    assert "red:101:abc1234" not in opened(config) and "red:103:0123456:root" in opened(config)


@pytest.mark.parametrize(("red_class", "stalls"), [("inherited", []), ("flake", []), ("stale", []), ("real", ["red:101:abc1234"])])
def test_a_red_head_classed_inherited_flake_or_stale_draws_no_red_stall_until_the_head_moves(config, red_class, stalls):
    red = {"name": "buildkite/test", "url": "https://buildkite.com/forge/test/builds/9", "job": "Run api tests", "error": "FAIL api/src/x.test.ts", "probe": False}
    raw = json.loads(config.read_text())
    config.write_text(json.dumps(raw | {"stall": {"minutes": {"unclassed": 600, "sweep": 600}}}))
    classed = record(1, "api-lane", "red", f"class={red_class} head=abc1234 owner=api-lane fails on dev too", 1, "101")
    shell = FakeShell([classed], [row("101", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([red])), row("102", test_state="failure", red_at=stamp(1), failing_checks=json.dumps([red]))])

    shell.clock = NOW + timedelta(minutes=2)
    watch(shell, config)
    assert sorted(shell.topics()) == [*stalls, "red:102:abc1234"]

    shell.rows[0] = row("101", OTHER, test_state="failure", red_at=stamp(-2), failing_checks=json.dumps([red]))
    shell.clock = NOW + timedelta(minutes=6)
    watch(shell, config)
    assert "red:101:fedcba9" in shell.topics()


def test_an_owner_order_naming_a_pr_no_named_lane_cites_a_pr_for_three_minutes_posts_one_urgent_record_to_root(config):
    revert = record(1, "root", "decide", "Owner 8:15 PM, verbatim (answer e7170fa): 'back that out, revert the merge'. Binding now: landing-desk reverts 78a4e35.", 4, to=["landing-desk-3", "drive-watchdog-2"])
    merge = record(2, "root", "decide", "Owner 8:16 PM, verbatim: 'merge it now'.", 4, to=["landing-desk-3"])
    fresh = record(3, "root", "decide", "Owner 8:18 PM, verbatim: 'fix the lint'.", 2, to=["lint-lane"])
    plain = record(4, "root", "decide", "root settles fidelity #9 on #45403: refuted.", 10, to=["signal-infra-1"])
    shell = FakeShell([revert, merge, fresh, plain], [])
    shell.answer = lambda question: {"pr": "revert" in question["order"] or "fix" in question["order"]} if question["kind"] == "order" else {}

    watch(shell, config)

    assert shell.topics() == ["order:1"]
    assert shell.post("order:1")["to"] == "root" and shell.post("order:1")["kind"] == "urgent"
    assert "no record from landing-desk-3, drive-watchdog-2 cites a PR since" in shell.post("order:1")["text"]
    assert len(shell.prompts) == 1 and '"kind": "order"' in shell.prompts[0]
    shell.records.append(record(5, "landing-desk-3", "state", "working on the revert", 1))
    watch(shell, config)
    assert "order:1" in opened(config)
    shell.records.append(record(6, "landing-desk-3", "done", "revert merged", 1, pr="45425"))
    watch(shell, config)
    assert "order:1" not in opened(config)


def graded(seq: int, pr: str, sha: str, minutes_ago: float) -> list[dict]:
    return [head(seq, pr, sha, minutes_ago), finder(seq + 1, pr, sha, minutes_ago - 1), fidelity(seq + 2, pr, sha, minutes_ago - 1)]


def stack(config: Path) -> dict[str, dict]:
    return {found["pr"]: found for found in json.loads((config.parent / "inbox/stack.json").read_text())["prs"]}


@pytest.mark.parametrize(
    ("fields", "state", "step"),
    [
        ({"test_state": "failure", "failing_checks": json.dumps([{"name": "buildkite/test"}])}, "red on buildkite/test", "fix the red build of #101 at abc1234"),
        ({"mergeable_state": "dirty"}, "conflicts with its base", "restack #101 at abc1234 and post HEAD"),
        ({"base": "graphite-base/101"}, "base drifted to graphite-base/101", "resubmit #101 at abc1234 with `ccx vcs ship --tip-only` and post HEAD"),
        ({"ai_review": "failure"}, "the reviewer bot holds it", "answer each blocking finding on #101 with a fix or a one-line rebuttal"),
    ],
)
def test_a_pr_in_one_state_for_two_hours_posts_one_stale_record_naming_its_owner_and_next_step_until_its_state_moves(config, fields, state, step):
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101", **fields)])
    shell.doc = {"blocks": [{"id": f"merge:101:{HEAD[:7]}", "type": "card", "children": []}]}

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == ["stale:101:abc1234"]
    assert shell.post("stale:101:abc1234") == {"to": "api-lane@d1", "kind": "urgent", "text": f"STALE #101 at abc1234, 150 min: {state} | owner api-lane | {step}"}
    shell.rows[0] = row("101", OTHER, **fields)
    shell.records += graded(4, "101", OTHER, 5)
    watch(shell, config)
    assert opened(config) == {}


def test_a_green_head_with_a_go_left_unqueued_stalls_to_root_and_the_landing_desk_as_the_runners_defect_not_its_lane(config):
    """p8a-step7 #200851: #45381 sat green with its go for two hours, and the stale record went to p8a-step7, whose step it was not. Owner, 5:16 AM Oct 30 (root #208560): such a head is the runner's defect, raised fifteen minutes after its go."""
    desk_lane(config, "landing-desk-3")
    go = record(4, "root", "go", f"go #101 at {HEAD[:7]}", 150)
    shell = FakeShell([*graded(1, "101", HEAD, 150), go], [row("101")])

    watch(shell, config)

    assert shell.topics() == ["go:101:abc1234"]
    post = shell.post("go:101:abc1234")
    assert post["to"] == "root,landing-desk-3@d1"
    assert post["text"].startswith(f"STALL go #101 at abc1234, 150 min: root go names #101 (#101: go #4 by root at {grades.clock(go['at'])}), and no QUEUED record does | owner root, desk-runner | root routes the tooling lane: the landing runner wrote no QUEUED, MERGED or blocker record for #101 at abc1234")


def test_a_graphite_base_under_an_open_parent_reads_as_a_drifted_base_never_a_landed_parent(config):
    shell = FakeShell([*graded(1, "100", HEAD, 150), *graded(4, "101", HEAD, 150)], [row("100", test_state="pending"), row("101", base="graphite-base/101")])
    shell.doc = {"blocks": [{"id": f"merge:101:{HEAD[:7]}", "type": "card", "children": []}]}

    watch(shell, config)

    text = shell.post("stale:101:abc1234")["text"]
    assert text == "STALE #101 at abc1234, 150 min: base drifted to graphite-base/101 | owner api-lane | resubmit #101 at abc1234 with `ccx vcs ship --tip-only` and post HEAD"
    assert "landed" not in text


def test_a_drifted_base_stale_stays_open_through_a_head_record_that_changes_nothing_and_clears_once_the_base_is_fixed(config):
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101", base="graphite-base/101")])
    shell.doc = {"blocks": [{"id": f"merge:101:{HEAD[:7]}", "type": "card", "children": []}]}

    watch(shell, config)
    shell.records.append(head(4, "101", HEAD, 1, f"HEAD #101 {HEAD} unchanged; resubmitted with ccx --tip-only"))
    watch(shell, config)

    assert "stale:101:abc1234" in opened(config)
    shell.rows[0] = row("101")
    watch(shell, config)
    assert "stale:101:abc1234" not in opened(config) and shell.topics() == ["stale:101:abc1234"]


def test_a_pr_a_hold_record_names_by_ref_draws_no_board_stall(config, tmp_path):
    shell = FakeShell([*graded(1, "101", HEAD, 150), *graded(4, "200", HEAD, 150), hold(900, "hold the batch: restack onto #200 and land after the cloud-runs batch", "100", "101")], [row("101"), row("200", test_state="pending")])

    watch(shell, config)

    assert [topic for topic in shell.topics() if topic.endswith(":101:abc1234")] == []
    assert next(found for found in json.loads((config.parent / "inbox/stack.json").read_text())["prs"] if found["pr"] == "101")["state"].startswith("held: ")


def merge_mode(config: Path, mode: str) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["merge_mode"] = mode
    config.write_text(json.dumps(raw))


@pytest.mark.parametrize(
    ("board", "verdict", "topic", "post"),
    [
        (True, "", "tap-wait:101:abc1234", {"to": "root", "kind": "state", "text": "#101 at abc1234 waits on the owner's tap since 10:30 AM"}),
        (True, "rejected", "stale:101:abc1234", {"to": "root,api-lane@d1", "kind": "urgent", "text": "STALE #101 at abc1234, 150 min: green, and no go names this head | owner api-lane | root posts `go #101 at abc1234` or puts its merge row on the owner's board"}),
        (False, "", "stale:101:abc1234", {"to": "root,api-lane@d1", "kind": "urgent", "text": "STALE #101 at abc1234, 150 min: green, and no go names this head | owner api-lane | root posts `go #101 at abc1234` or puts its merge row on the owner's board"}),
    ],
    ids=["live-tap-row", "rejected-tap-row", "no-tap-row"],
)
def test_a_green_head_with_no_go_waits_on_a_live_tap_row_with_one_state_record_and_stales_without_one(config, board, verdict, topic, post):
    merge_mode(config, "ask")
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101")])
    if board:
        shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 101, "head": HEAD[:7]}]}]}]}
    if verdict:
        shell.triage = {"merge-prs": {"101": {"verdict": verdict, "head": HEAD[:7], "at": stamp(140)}}}

    watch(shell, config)
    shell.clock += timedelta(minutes=40)
    watch(shell, config)

    assert shell.topics() == [topic]
    assert shell.post(topic) == post


@pytest.mark.parametrize(
    ("board", "fields", "owed", "stalls"),
    [(True, {}, False, []), (False, {}, False, []), (True, {}, True, ["lane:api-lane"]), (True, {"test_state": "failure"}, False, ["lane:api-lane"])],
    ids=["pr-on-the-tap-list", "pr-waits-on-root", "owes-a-record", "owes-a-red-build"],
)
def test_a_quiet_lane_with_open_prs_stalls_only_when_it_owes_a_record_or_a_fix(config, tmp_path, board, fields, owed, stalls):
    merge_mode(config, "ask")
    task(tmp_path, 1, "api-lane", minutes_ago=200)
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101", **fields)])
    if board:
        shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 101, "head": HEAD[:7]}]}]}]}
    if owed:
        shell.records.append(record(4, "root", "decide", "api-lane: rebase #101 on dev", 60, to=["api-lane@d1"]))
    shell.answer = lambda question: {"explained": False}

    watch(shell, config)

    assert [topic for topic in shell.topics() if topic.startswith("lane:")] == stalls


@pytest.mark.parametrize(
    ("gate", "stales"),
    [({}, []), ({"test_state": "failure"}, ["stale:303:abc1234"])],
    ids=["gates-on-the-tap-list", "a-gate-is-red"],
)
def test_a_held_batch_whose_hold_record_names_prs_on_or_stacked_above_the_tap_list_posts_one_state_record_per_head_and_a_red_gate_stales_only_itself(config, tmp_path, gate, stales):
    merge_mode(config, "ask")
    records = [*graded(1, "201", HEAD, 150), *graded(4, "202", HEAD, 150), *graded(7, "301", HEAD, 150), *graded(10, "302", HEAD, 150), *graded(13, "303", HEAD, 150), hold(900, "hold #201 (root #159418): lands after #301 and #302", "201"), hold(901, "hold #202 (root #159418): same batch as #201", "202")]
    shell = FakeShell(records, [row("201"), row("202"), row("301"), row("302", base="b303"), row("303", **gate)])
    shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": number, "head": HEAD[:7]} for number in (301, 302, 303)]}]}]}

    watch(shell, config)
    watch(shell, config)

    assert sorted(topic for topic in shell.topics() if topic.startswith("stale:")) == stales
    if not stales:
        assert [topic for topic in shell.topics() if topic.startswith("tap-wait:")] == ["tap-wait:digest"]
        assert shell.post("tap-wait:digest")["text"].startswith("4 heads wait on the owner's tap: #201 at abc1234 is held on #301, #303, each waiting on the owner's tap, his board answer, or the merge queue, since 10:30 AM; #202 at abc1234 is held on #301, #303")


@pytest.mark.parametrize(("tap", "stales"), [(None, []), ("approved", ["stale:201:abc1234"])], ids=["parent-untapped", "parent-tapped"])
def test_a_row_held_behind_an_open_untapped_parent_never_stales(config, tmp_path, tap, stales):
    merge_mode(config, "ask")
    shell = FakeShell([*graded(1, "201", HEAD, 150), *graded(4, "301", HEAD, 150), hold(900, "hold #201 lands as the top of the cloud-runs batch above #301 (root 162279)", "201")], [row("201"), row("301", test_state="pending")])
    shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 301, "head": HEAD[:7]}]}]}]}
    if tap:
        shell.triage = {"merge-prs": {"301": {"verdict": tap, "head": HEAD[:7], "at": stamp(140)}}}

    watch(shell, config)

    assert sorted(topic for topic in shell.topics() if topic.startswith("stale:201")) == stales


@pytest.mark.parametrize(
    ("line", "pr_block", "answer", "topic"),
    [
        ("hold #401 off the merge card until his pick on the eviction-card question", True, None, "tap-wait:401:abc1234"),
        ("hold #401 waits on eviction-route-choice", False, None, "tap-wait:401:abc1234"),
        ("hold #401 off the merge card until his pick on the eviction-card question", True, {"optionIds": ["eviction-route-app"]}, "stale:401:abc1234"),
    ],
    ids=["card-holding-the-pr-unanswered", "block-id-unanswered", "card-answered"],
)
def test_a_hold_on_an_owner_card_waits_while_the_card_is_unanswered_and_stales_once_he_answers(config, tmp_path, line, pr_block, answer, topic):
    merge_mode(config, "ask")
    shell = FakeShell([*graded(1, "401", HEAD, 150), hold(900, line, "401")], [row("401")])
    children = [{"id": "eviction-route-choice", "type": "choice", "options": [{"id": "eviction-route-app"}]}, *([{"id": "eviction-route-pr", "type": "pr", "number": 401}] if pr_block else [])]
    shell.doc = {"blocks": [{"id": "card-eviction", "type": "card", "children": children}]}
    shell.choices = {"eviction-route-choice": answer}

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == [topic]
    if topic.startswith("tap-wait:"):
        assert shell.post(topic)["text"] == "#401 at abc1234 is held on eviction-route-choice, each waiting on the owner's tap, his board answer, or the merge queue, since 10:30 AM"


@pytest.mark.parametrize(
    ("records", "text"),
    [
        ([record(1, "api-lane", "report", f"READY #101 at {HEAD[:7]}", 60, "101")], None),
        ([record(1, "api-lane", "state", "pushed the fix", 60)], "its last record, state #1 at 12:00 PM, explains no silence"),
        ([record(1, "api-lane", "report", f"READY #101 at {HEAD[:7]}", 60, "101"), record(2, "board-refresher", "ask", "which tests does #101 run?", 45, status="open", to=["api-lane"])], "it owes a reply to #2 (ask from board-refresher at 12:15 PM)"),
    ],
    ids=["last-record-ready", "last-record-explains-nothing", "owes-an-ask"],
)
def test_a_quiet_lane_with_no_open_pr_is_idle_after_a_ready_record_unless_it_owes_a_reply(config, tmp_path, records, text):
    task(tmp_path, 1, "api-lane", minutes_ago=90)
    shell = FakeShell(records, [])
    shell.answer = lambda question: {"explained": False}

    watch(shell, config)

    assert shell.topics() == (["lane:api-lane"] if text else [])
    if text:
        assert text in shell.post("lane:api-lane")["text"]


@pytest.mark.parametrize(("text", "stalls"), [("Owners for the reds: #101 stays with api-lane, READY", []), ("api-lane: rebase #101 on dev", ["lane:api-lane"])], ids=["listed-as-recipient", "given-an-order"])
def test_a_lane_owes_a_root_decide_only_when_it_gives_the_lane_an_order(config, tmp_path, text, stalls):
    task(tmp_path, 1, "api-lane", minutes_ago=90)
    shell = FakeShell([record(1, "api-lane", "report", f"READY #101 at {HEAD[:7]}", 60, "101"), record(2, "root", "decide", text, 45, to=["api-lane@d1", "fidelity-desk@d1"])], [])

    watch(shell, config)

    assert shell.topics() == stalls


@pytest.mark.parametrize(("ready", "stalls"), [(False, []), (True, ["board:101:abc1234"])], ids=["row-pulled", "row-due-after-ready"])
def test_a_merge_row_root_pulled_by_a_decide_is_not_missing_until_the_lane_posts_ready(config, ready, stalls):
    records = [head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20), record(4, "root", "decide", "Confirmed: pull #101's tap row until api-lane posts READY with the red fixed", 15, "101")]
    if ready:
        records.append(record(5, "api-lane", "report", f"READY #101 at {HEAD[:7]}", 5, "101"))
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert shell.topics() == stalls


@pytest.mark.parametrize(
    ("fields", "stalls"),
    [({"test_state": "pending"}, []), ({"green_at": stamp(5)}, []), ({"green_at": stamp(16)}, ["board:101:abc1234"])],
    ids=["build-running", "green-5-min-ago", "green-16-min-ago"],
)
def test_a_board_stall_counts_from_the_build_turning_green_when_it_greens_after_the_grades(config, fields, stalls):
    shell = FakeShell([head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20)], [row("101", **fields)])

    watch(shell, config)

    assert shell.topics() == stalls


def exempt_grades(config: Path, phrases: list[str]) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["exempt_grades"] = phrases
    config.write_text(json.dumps(raw))


@pytest.mark.parametrize(
    ("grade", "phrases", "stalls"),
    [
        ("ok", None, ["board:101:abc1234"]),
        ("ok pure port, no interaction: it only ports old-tree #43020.", None, []),
        ("ok test-only or CI-only, no non-test code: it only edits tests.", None, []),
        ("ok pure port, no interaction: it only ports old-tree #43020.", ["trivial move"], ["board:101:abc1234"]),
        ("ok trivial move: it only renames a file.", ["trivial move"], []),
    ],
    ids=["plain-ok", "pure-port", "test-only", "port-phrase-replaced-by-config", "configured-phrase"],
)
def test_a_head_whose_fidelity_grade_says_it_lands_with_no_board_row_draws_no_board_stall(config, grade, phrases, stalls):
    if phrases:
        exempt_grades(config, phrases)
    graded = record(3, "fidelity-desk", "review", f"fidelity: {grade.replace('ok', 'ok ' + HEAD[:7], 1)}", 20, "101")
    shell = FakeShell([head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), graded], [row("101")])

    watch(shell, config)

    assert shell.topics() == stalls


@pytest.mark.parametrize(("speaker", "text", "stalls"), [("root", "#101 gets no row: it lands on my go", []), ("root", "#102 gets no row: it lands on my go", ["board:101:abc1234"]), ("api-lane", "#101 needs no row", ["board:101:abc1234"])], ids=["root-says-no-row", "root-names-another-pr", "a-lane-is-not-root"])
def test_a_root_record_that_says_a_pr_gets_no_row_draws_no_board_stall(config, speaker, text, stalls):
    records = [head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20), record(4, speaker, "decide", text, 15)]
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert shell.topics() == stalls


def tap_row(pr: str, sha: str = HEAD, written: float | None = None) -> dict:
    return {"number": int(pr), "head": sha[:7]} | ({"textAt": stamp(written)} if written is not None else {})


def tap_board(*rows: dict) -> dict:
    return {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": list(rows)}]}]}


@pytest.mark.parametrize(("written", "ready", "since"), [(140, None, "10:40 AM"), (None, 130, "10:50 AM"), (140, 145, "10:35 AM")], ids=["row-write", "ready-record", "earlier-of-both"])
def test_a_tap_row_counts_from_its_board_write_or_its_ready_record_not_from_the_pass_that_first_read_it(config, written, ready, since):
    merge_mode(config, "ask")
    state = config.parent / "inbox/stall-watch.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"open": {}, "verdicts": {}, "states": {"101": {"signature": f"{HEAD} waits on the owner's tap", "since": stamp(0)}}}))
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101")])
    if ready is not None:
        shell.records.append(record(4, "api-lane", "report", f"READY #101 at {HEAD[:7]}", ready, "101"))
    shell.doc = tap_board(tap_row("101", written=written))

    watch(shell, config)

    assert shell.topics() == ["tap-wait:101:abc1234"]
    assert shell.post("tap-wait:101:abc1234")["text"] == f"#101 at abc1234 waits on the owner's tap since {since}"


def test_a_tap_wait_posts_once_per_head_across_passes_even_when_its_state_flaps(config):
    merge_mode(config, "ask")
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101")])
    shell.doc = tap_board(tap_row("101", written=140))

    watch(shell, config)
    shell.rows[0] = row("101", test_state="failure")
    watch(shell, config)
    shell.rows[0] = row("101")
    watch(shell, config)

    assert shell.topics() == ["tap-wait:101:abc1234"]


def test_heads_that_become_tap_waits_in_one_pass_post_one_digest_record(config):
    merge_mode(config, "ask")
    shell = FakeShell([*graded(1, "101", HEAD, 150), *graded(4, "102", OTHER, 150)], [row("101"), row("102", OTHER)])
    shell.doc = tap_board(tap_row("101"), tap_row("102", OTHER))

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == ["tap-wait:digest"]
    assert shell.post("tap-wait:digest") == {"to": "root", "kind": "state", "text": "2 heads wait on the owner's tap: #101 at abc1234 waits on the owner's tap since 10:30 AM; #102 at fedcba9 waits on the owner's tap since 10:30 AM"}


def test_a_pr_that_moved_less_than_two_hours_ago_is_not_stale_and_a_new_state_on_the_same_head_counts_from_the_pass_that_saw_it(config):
    shell = FakeShell([*graded(1, "101", HEAD, 100), *graded(4, "102", OTHER, 300)], [row("101", mergeable_state="dirty"), row("102", OTHER, mergeable_state="dirty")])
    shell.doc = {"blocks": [{"id": f"merge:101:{HEAD[:7]}", "type": "card", "children": []}, {"id": f"merge:102:{OTHER[:7]}", "type": "card", "children": []}]}

    watch(shell, config)
    shell.rows[1] = row("102", OTHER, test_state="failure")
    watch(shell, config)

    assert shell.topics() == ["stale:102:fedcba9"]
    assert opened(config) == {}
    assert {pr: (found["state"], found["since"], found["stale"]) for pr, found in stack(config).items()} == {
        "101": ("conflicts with its base", stamp(100), False),
        "102": ("red on a required check", stamp(0), False),
    }


def test_a_stale_stack_posts_once_from_its_lowest_stale_pr_a_held_pr_counts_and_a_lane_with_no_live_record_goes_to_root(config, tmp_path):
    records = [record(1, "gone-lane", "head", f"HEAD #201 {HEAD}", 200, "201"), *graded(1, "201", HEAD, 200)[1:], *graded(4, "202", OTHER, 200), *graded(7, "203", OLD, 200), head(10, "301", HEAD, 200), record(11, "gone-lane", "stand-down", "standing down", 150), hold(900, "hold #301 waits on the schema release", "301")]
    rows = [row("201", mergeable_state="dirty", lane="gone-lane"), row("202", OTHER, base="b201", test_state="pending"), row("203", OLD, base="b202", test_state="pending"), row("301")]
    shell = FakeShell(records, rows)
    shell.doc = {"blocks": [{"id": f"merge:201:{HEAD[:7]}", "type": "card", "children": []}]}

    watch(shell, config)

    assert shell.topics() == ["stale:201:abc1234", "stale:301:abc1234"]
    assert shell.post("stale:201:abc1234") == {
        "to": "root",
        "kind": "urgent",
        "text": "STALE #201 at abc1234, 200 min: conflicts with its base; 2 above wait on it: #202, #203; gone-lane has posted nothing live in 24 h | owner root | restack #201 at abc1234 and post HEAD",
    }
    assert "held: a hold record covers it | owner api-lane | lift the hold on #301 or do what it waits on" in shell.post("stale:301:abc1234")["text"]
    assert stack(config)["202"] | {"since": ""} == {
        "pr": "202",
        "title": "",
        "head": OTHER,
        "base": "b201",
        "below": "201",
        "waits": "201",
        "state": "waits on #201 below it",
        "next": "land #201 first",
        "owner": "api-lane",
        "actor": "api-lane",
        "since": "",
        "live": True,
        "stale": True,
    }


def desk_lane(config: Path, lane: str) -> None:
    raw = json.loads(config.read_text())
    raw["landing"]["desk_lane"] = lane
    config.write_text(json.dumps(raw))


@pytest.mark.parametrize(
    ("desk", "topic", "to"),
    [("", "fidelity:101:abc1234", "fidelity-desk@d1"), ("", "go:201:abc1234", "root"), ("landing-desk-3", "go:201:abc1234", "landing-desk-3@d1")],
    ids=["fidelity-desk", "runner-without-desk-lane", "runner-with-desk-lane"],
)
def test_a_stall_only_desks_own_goes_to_those_desks_and_not_to_root(config, desk, topic, to):
    if desk:
        desk_lane(config, desk)
    records = [head(1, "101", HEAD, 40), finder(2, "101", HEAD, 35)]
    records += [head(3, "201", HEAD, 30), finder(4, "201", HEAD, 25), fidelity(5, "201", HEAD, 25), record(6, "root", "go", f"go #201 at {HEAD[:7]}", 12)]
    step = {"root": "201", "lane": "api-lane", "members": {"201": HEAD}, "step": "check", "since": stamp(8), "budget": 180}
    config.with_name(f"{config.stem}.landing-stacks.json").write_text(json.dumps({"at": stamp(0), "stacks": {"201": step}}))
    shell = FakeShell(records, [row("101", test_state="pending"), row("201")])

    watch(shell, config)

    assert shell.post(topic)["to"] == to


def test_a_stall_that_clears_and_comes_back_posts_again_only_thirty_minutes_after_its_last_post(config):
    review = finder(3, "101", HEAD, 1)
    shell = FakeShell([head(1, "101", HEAD, 40), fidelity(2, "101", HEAD, 35)], [row("101", test_state="pending")])

    watch(shell, config)
    shell.records.append(review)
    watch(shell, config)
    shell.records.remove(review)
    shell.clock += timedelta(minutes=29)
    watch(shell, config)
    assert shell.topics() == ["finder:101:abc1234"]
    shell.clock += timedelta(minutes=1)
    watch(shell, config)
    assert shell.topics() == ["finder:101:abc1234", "finder:101:abc1234"]


BOTTOM = "e3ff0636bdb2e8c5b8b02852444fc2366ecd0416"
TOP = "09c18630ab2e8c5b8b02852444fc2366ecd04160"
MOVED = "a01e2016f80e1f4cae7683e8818ed80a072fcd54"
LANDING_BLOCKER = (
    "landing-desk-3 2:14 AM: stack-enqueue refuses two tapped stacks on Graphite records. p8a-step7: #44141 09c1863 sits on "
    "dar-p8a-step7-team-reds-listed-team (closed #44140's branch), which Graphite does not track, so #44139 cannot carry it; "
    "run ccx vcs stack submit from #44141's worktree to put it on #44139, post HEAD. p8f-replica-9: #43736 b57f618 is still on "
    "graphite-base/43736 after #44127 landed; restack it onto dev, post HEADs."
)


def tonight(config: Path) -> FakeShell:
    """#44141, go'd on #44139, sits on closed #44140's untracked branch; landing-desk-3 posts the blocker #173678 at NOW and p8a-step7 stays silent."""
    desk_lane(config, "landing-desk-3")
    rows = [
        row("44139", BOTTOM, lane="p8a-step7", branch="dar-p8a-step7-a"),
        row("44141", TOP, lane="p8a-step7", branch="dar-p8a-step7-b", base="dar-p8a-step7-team-reds-listed-team"),
    ]
    records = [
        record(173026, "root", "go", f"go #44139 at {BOTTOM[:7]}, #44141 at {TOP[:7]}", 40),
        record(173678, "landing-desk-3", "blocker", LANDING_BLOCKER, 0, to=["p8a-step7", "p8f-replica-9", "root"], status="open"),
    ]
    return FakeShell(records, rows)


def refusal_topics(shell: FakeShell) -> list[str]:
    return [topic for topic in shell.topics() if topic.startswith(("refusal:", "successor:"))]


def test_a_landing_refusal_no_lane_answers_escalates_to_root_and_its_lane_at_10_minutes_and_asks_root_for_a_successor_at_20(config):
    shell = tonight(config)
    shell.clock = NOW + timedelta(minutes=9)
    watch(shell, config)
    assert refusal_topics(shell) == []

    shell.clock = NOW + timedelta(minutes=10)
    watch(shell, config)
    assert refusal_topics(shell) == ["refusal:44141:09c1863:parent"]
    post = shell.post("refusal:44141:09c1863:parent")
    assert (post["kind"], post["to"]) == ("urgent", "root,p8a-step7@d1")
    assert post["text"].startswith("STALL refusal #44141 at 09c1863, 10 min: landing-desk-3 refused to queue it at ")
    assert "| owner p8a-step7 | p8a-step7: run ccx vcs stack submit from #44141's worktree to put it on #44139, post HEAD, then post HEAD or `cci post --re '#173678'`" in post["text"]

    shell.clock = NOW + timedelta(minutes=15)
    watch(shell, config)
    assert refusal_topics(shell) == ["refusal:44141:09c1863:parent"]

    shell.clock = NOW + timedelta(minutes=20)
    watch(shell, config)
    assert refusal_topics(shell) == ["refusal:44141:09c1863:parent", "successor:44141:09c1863:parent"]
    successor = shell.post("successor:44141:09c1863:parent")
    assert successor["to"] == "root"
    assert "| owner root | spawn a successor to p8a-step7, which has not acted on #173678 in 20 min; the successor's first step: run ccx vcs stack submit from #44141's worktree" in successor["text"]


def test_a_head_record_from_the_refused_lane_clears_its_refusal(config):
    shell = tonight(config)
    shell.records.append(record(174232, "p8a-step7", "head", f"HEAD #44141 {MOVED}: patch-identical, restacked onto #44139", -21, "44141"))
    shell.clock = NOW + timedelta(minutes=22)
    watch(shell, config)
    assert not [topic for topic in shell.topics() if topic.startswith(("refusal:", "successor:"))]


@pytest.mark.parametrize("cause", ["needs-restack", "conflicting against its base", "GitHub is computing mergeability", "TAP-REQUIRED rule/82317b0", "FOREIGN #44999"])
def test_each_landing_refusal_cause_is_tracked_and_routed_to_the_lane_that_acts(config, cause):
    desk_lane(config, "landing-desk-3")
    records = [*gone("101", HEAD), record(5, "desk-runner", "blocker", f"#101 {HEAD[:10]}: {cause}", 11, to=["api-lane", "root"])]
    shell = FakeShell(records, [row("101")])
    watch(shell, config)
    [topic] = [topic for topic in shell.topics() if topic.startswith("refusal:")]
    to = shell.post(topic)["to"]
    expected = {"computing": "landing-desk-3@d1", "TAP": "root", "FOREIGN": "root"}
    assert to == next((lanes for word, lanes in expected.items() if word in cause), "api-lane@d1")


def test_a_refusal_for_a_pr_no_go_or_tap_names_is_not_tracked(config):
    shell = FakeShell([head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(5, "desk-runner", "blocker", f"#101 {HEAD[:10]}: needs-restack", 11, to=["api-lane"])], [row("101")])
    watch(shell, config)
    assert not [topic for topic in shell.topics() if topic.startswith("refusal:")]


def desk_job(tmp_path: Path, desk: str, kind: str, pr: str, sha: str, started_minutes_ago: float | None) -> None:
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident(desk, desk, 0, stamp(60), {}))
    with store.transaction(desk, generation=0) as incident:
        incident.accept(f"{kind}:{pr}:{sha}", kind, json.dumps({"pr": pr, "head": sha, "lane": "api-lane", "seq": 1, "full": False}), "cci#1", NOW - timedelta(minutes=60))
        if started_minutes_ago is not None:
            incident.start(f"{kind}:{pr}:{sha}", NOW - timedelta(minutes=started_minutes_ago))


@pytest.mark.parametrize(("started", "stalls"), [(None, []), (10, []), (30, ["finder:101:abc1234"])])
def test_a_head_in_the_finder_queue_draws_no_finder_stall_until_its_pass_runs_past_25_minutes_and_then_only_the_desk_hears(config, tmp_path, started, stalls):
    desk_lane(config, "landing-desk-3")
    desk_job(tmp_path, "desk-finder", "finder", "101", HEAD, started)
    shell = FakeShell([head(1, "101", HEAD, 50), fidelity(2, "101", HEAD, 45)], [row("101", test_state="pending")])
    watch(shell, config)
    assert [topic for topic in shell.topics() if topic.startswith("finder:")] == stalls
    for topic in stalls:
        assert shell.post(topic)["to"] == "landing-desk-3@d1"


def test_green_heads_the_finder_desk_holds_queued_go_to_the_landing_desk_and_root_hears_them_in_one_line(config, tmp_path):
    """#224787, #224882 and #224883 sent root one line per green head the finder desk's queue held."""
    desk_lane(config, "landing-desk-3")
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("desk-finder", "desk-finder", 0, stamp(60), {}))
    with store.transaction("desk-finder", generation=0) as incident:
        for pr, sha in (("101", HEAD), ("102", OTHER)):
            incident.accept(f"finder:{pr}:{sha}", "finder", json.dumps({"pr": pr, "head": sha, "lane": "api-lane", "seq": 1, "full": False}), "cci#1", NOW - timedelta(minutes=60))
    shell = FakeShell([head(1, "101", HEAD, 30), fidelity(2, "101", HEAD, 25), head(3, "102", OTHER, 30), fidelity(4, "102", OTHER, 25)], [row("101", green_at=stamp(13)), row("102", OTHER, green_at=stamp(11))])

    watch(shell, config)
    watch(shell, config)

    assert [topic for topic in shell.topics() if topic.startswith("finder:")] == ["finder:101:abc1234", "finder:102:fedcba9"]
    for topic in ("finder:101:abc1234", "finder:102:fedcba9"):
        assert shell.post(topic)["to"] == "landing-desk-3@d1"
        assert "queued since 12:00 PM" in shell.post(topic)["text"]
    digests = [topic for topic in shell.topics() if topic.startswith("ungraded:")]
    assert len(digests) == 1 and shell.post(digests[0])["to"] == "root"
    assert shell.post(digests[0])["text"].startswith("STALL ungraded, 2 green heads past 10 min with no grade: #101 at abc1234 has no finder grade, green since 12:47 PM, owner desk-runner; #102 at fedcba9")


@pytest.mark.parametrize(("started", "stalls"), [(None, []), (30, ["fidelity:101:abc1234"])])
def test_a_head_in_the_fidelity_queue_draws_no_fidelity_stall_until_its_grade_runs_past_25_minutes(config, tmp_path, started, stalls):
    desk_lane(config, "landing-desk-3")
    desk_job(tmp_path, "desk-fidelity", "fidelity", "101", HEAD, started)
    shell = FakeShell([head(1, "101", HEAD, 50), finder(2, "101", HEAD, 45)], [row("101", test_state="pending")])
    watch(shell, config)
    assert [topic for topic in shell.topics() if topic.startswith("fidelity:")] == stalls
    for topic in stalls:
        assert "root" not in shell.post(topic)["to"].split(",")


def test_a_board_line_spares_root_and_goes_to_the_board_lane_while_the_board_refresher_posted_about_the_pr_in_the_last_ten_minutes(config):
    raw = json.loads(config.read_text())
    raw["landing"]["board_lane"] = "board-refresher"
    config.write_text(json.dumps(raw))
    records = [head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20), record(4, "board-refresher-2", "note", "row up for #101", 5, "101")]
    shell = FakeShell(records, [row("101")])
    watch(shell, config)
    assert [shell.post(topic)["to"] for topic in shell.topics() if topic.startswith("board:")] == ["board-refresher-2@d1"]


def test_a_runner_warning_that_holds_nothing_is_no_refusal(config):
    warning = f"#101 {HEAD[:10]}: WARNING `rulings.py check-pr` warns at {HEAD[:10]}, and the head is not held for it: CONTRACT-MISSING api-lane has no contract"
    shell = FakeShell([*gone("101", HEAD), record(5, "desk-runner", "note", warning, 30, to=["api-lane", "root"])], [row("101")])
    watch(shell, config)
    assert not [topic for topic in shell.topics() if topic.startswith(("refusal:", "successor:"))]


def test_no_successor_is_asked_for_while_the_lanes_handoff_already_posts(config):
    shell = tonight(config)
    shell.records.append(record(173700, "p8a-step7-handoff", "state", "handoff of p8a-step7 under way", -5))
    shell.clock = NOW + timedelta(minutes=20)
    watch(shell, config)
    assert refusal_topics(shell) == ["refusal:44141:09c1863:parent"]


def test_a_grader_desks_waiting_line_opens_no_gate(config):
    """Root #218445: gate:fidelity-desk-11:45103 re-fired hourly, though a grader opens no work when a PR lands."""
    said = record(1, "fidelity-desk-11", "state", "fidelity-desk-11: the grade waits on #43736", 120)
    landed = record(2, "desk-runner", "landed", "LANDED #43736 as 70ad7da45d: merged into dev", 31, "43736")
    shell = FakeShell([said, landed], [])
    watch(shell, config)
    assert [topic for topic in shell.topics() if topic.startswith("gate:")] == []


@pytest.mark.parametrize(
    ("landed_ago", "after", "topics"),
    [
        (31, [], ["gate:p8a-step7-2:43736:0"]),
        (9, [], []),
        (91, [], ["gate:p8a-step7-2:43736:1"]),
        (31, [record(4, "p8a-step7-3", "opened", "OPENED #45100 head 1234abc: parts 2 and 3", 5, "45100")], []),
        (31, [record(4, "p8a-step7-2", "head", "HEAD #44000 1234abc", 40, "44000")], ["gate:p8a-step7-2:43736:0"]),
        (31, [record(4, "root", "decide", "parts 2 and 3 are dropped", 5, resolves=1)], []),
        (31, [record(4, "p8a-step7-2", "state", "parts 2 and 3 are dropped; on #45200 now", 5)], []),
    ],
    ids=["landed-31-min-ago", "inside-10-min", "an-hour-later", "successor-opened", "head-before-the-landing", "resolved", "a-later-state-without-the-pr"],
)
def test_unopened_work_that_waits_on_a_landed_pr_stalls_to_its_live_lane_hourly(config, landed_ago, after, topics):
    """Root #218445: a gate cleared only on an OPENED or HEAD, so a resolved or superseded line re-fired hourly."""
    said = record(1, "p8a-step7-2", "state", "p8a-step7-2: parts 2 and 3 wait only on #43736; I open both when #43736 lands", 120)
    landed = record(2, "desk-runner", "landed", "LANDED #43736 as 70ad7da45d: merged into dev", landed_ago, "43736")
    shell = FakeShell([said, landed, *after], [])
    watch(shell, config)
    assert [topic for topic in shell.topics() if topic.startswith("gate:")] == topics
    if topics:
        post = shell.post(topics[0])
        assert post["to"] == "p8a-step7-2@d1" and post["text"].startswith(f"STALL gate p8a-step7-2 on #43736, {landed_ago} min: p8a-step7-2 said at cci #1 that work waits on #43736, and desk-runner posted it landed at cci #2")


@pytest.mark.parametrize(("go_ago", "stalls"), [(12, False), (17, True)], ids=["inside-fifteen-minutes", "past-fifteen-minutes"])
def test_a_go_named_head_no_runner_record_or_worker_names_stalls_to_root_as_a_runner_defect_fifteen_minutes_on(config, go_ago, stalls):
    """Owner 5:16 AM Oct 30 on #45286, admitted and unqueued for two hours with no record: "make a durable fix for this not jsut a done-off". A stack no worker held was backlog that never stalled, so the watch stayed as silent as the runner (root #208560)."""
    pr = "200"
    records = [head(1, pr, HEAD, 60), finder(2, pr, HEAD, 50), fidelity(3, pr, HEAD, 50), record(4, "root", "go", f"go #{pr} at {HEAD[:7]}", go_ago)]
    shell = FakeShell(records, [row(pr)])
    watch(shell, config)
    go_stalls = [topic for topic in shell.topics() if topic.startswith("go:")]
    assert go_stalls == ([f"go:{pr}:{HEAD[:7]}"] if stalls else [])
    if stalls:
        post = shell.post(go_stalls[0])
        assert post["to"].split(",")[0] == "root"
        assert f"the landing runner wrote no QUEUED, MERGED or blocker record for #200 at {HEAD[:7]} since the go at" in post["text"]


@pytest.mark.parametrize(("on_step", "stalls"), [(4, False), (8, True)], ids=["inside-its-budget", "past-its-budget"])
def test_a_go_named_stack_whose_runner_worker_sits_on_one_step_past_its_budget_stalls_naming_the_step(config, on_step, stalls):
    pr = "200"
    records = [head(1, pr, HEAD, 60), finder(2, pr, HEAD, 50), fidelity(3, pr, HEAD, 50), record(4, "root", "go", f"go #{pr} at {HEAD[:7]}", 31)]
    step = {"root": pr, "lane": "api-lane", "members": {pr: HEAD}, "step": "enqueue", "since": stamp(on_step), "budget": 300, "command": "stack-enqueue 200 --partial", "command_seconds": on_step * 60}
    config.with_name(f"{config.stem}.landing-stacks.json").write_text(json.dumps({"at": stamp(0), "stacks": {pr: step}}))
    shell = FakeShell(records, [row(pr)])
    watch(shell, config)
    go_stalls = [topic for topic in shell.topics() if topic.startswith("go:")]
    assert bool(go_stalls) is stalls
    if stalls:
        assert "the landing runner's worker for the stack from #200 has been on its enqueue step for 8 min, past its 5 min budget, running `stack-enqueue 200 --partial` for 480 s" in shell.post(go_stalls[0])["text"]


def missing(seq: int, pr: str, sha: str, minutes_ago: float) -> dict:
    return record(seq, "fidelity-desk", "review", f"fidelity: missing {sha[:7]} exception/E12 restores content without the 180 MiB cap", minutes_ago, pr)


@pytest.mark.parametrize(("graded", "stalls"), [(10, []), (25, ["missing:101:3"])])
def test_a_missing_grade_nobody_answers_for_twenty_minutes_alarms_root_and_the_prs_lane_like_a_red_build(config, graded, stalls):
    shell = FakeShell([head(1, "101", HEAD, 40), finder(2, "101", HEAD, 39), missing(3, "101", HEAD, graded)], [row("101", test_state="pending")])

    watch(shell, config)

    assert shell.topics() == stalls
    for topic in stalls:
        assert shell.post(topic)["to"] == "root,api-lane@d1"
        assert shell.post(topic)["text"].startswith(f"STALL missing #101 at abc1234, {graded} min: fidelity grade cci #3 by fidelity-desk reads missing since 12:35 PM: fidelity: missing abc1234 exception/E12")
        assert "nobody has answered it" in shell.post(topic)["text"]


@pytest.mark.parametrize(("answered", "stalls"), [(5, []), (20, ["regrade:101:3"])])
def test_a_missing_grade_a_lane_answered_with_no_regrade_for_fifteen_minutes_goes_to_the_grader_not_root(config, answered, stalls):
    reply = record(4, "api-lane", "report", "the cap lives in the relay; see followed.go", answered, "101", re=3)
    shell = FakeShell([head(1, "101", HEAD, 60), finder(2, "101", HEAD, 59), missing(3, "101", HEAD, 50), reply], [row("101", test_state="pending")])

    watch(shell, config)

    assert shell.topics() == stalls
    for topic in stalls:
        assert shell.post(topic)["to"] == "fidelity-desk@d1"
        assert "api-lane in cci #4 answered it at" in shell.post(topic)["text"]


def test_a_pr_comment_answers_a_missing_grade_and_a_bot_comment_does_not(config):
    shell = FakeShell([head(1, "101", HEAD, 60), finder(2, "101", HEAD, 59), missing(3, "101", HEAD, 50)], [row("101", test_state="pending")])
    shell.comments["101"] = [{"author": {"login": "forge-pr-reviewer[bot]"}, "createdAt": stamp(45), "body": "Held"}]

    watch(shell, config)
    assert shell.topics() == ["missing:101:3"]

    shell.comments["101"].append({"author": {"login": "yasyf"}, "createdAt": stamp(30), "body": "The plan holds: the cap lives in the relay."})
    watch(shell, config)
    assert shell.topics() == ["missing:101:3", "regrade:101:3"]
    assert "a PR comment by yasyf answered it at 12:30 PM, and no grade has followed" in shell.post("regrade:101:3")["text"]

    shell.records.append(fidelity(5, "101", HEAD, 1))
    watch(shell, config)
    assert "regrade:101:3" not in opened(config) and "missing:101:3" not in opened(config)


def test_a_red_no_record_classes_alarms_root_once_at_twenty_minutes_a_real_class_with_no_new_head_once_at_sixty_and_drive_reds_prints_the_register(config, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    lint = {"name": "buildkite/lint", "url": "https://buildkite.com/forge/lint/builds/4"}
    classed = record(1, "red-sweep", "red", "12:50 PM class=real head=fedcba9 owner=web-lane needs a schema fix; blocker sent", 10, "202")
    shell = FakeShell([classed, finder(2, "202", OTHER, 10), fidelity(3, "202", OTHER, 10)], [row("201", test_state="failure", red_at=stamp(25), failing_checks=json.dumps([lint])), row("202", OTHER, lane="web-lane", test_state="failure", red_at=stamp(25), failing_checks=json.dumps([lint]))])

    watch(shell, config)
    assert shell.topics() == ["unclassed:201:abc1234"]
    assert shell.post("unclassed:201:abc1234") == {
        "to": "root,api-lane@d1",
        "kind": "blocker",
        "text": 'STALL unclassed #201 at abc1234, 25 min: buildkite/lint red since 12:35 PM, and no `red` record classifies it | owner api-lane | classify it: `cci post --kind red --pr 201 --text "class=<inherited|flake|real|stale> head=abc1234 owner=<lane> <what was done>"`',
    }

    shell.clock = NOW + timedelta(minutes=5)
    watch(shell, config)
    assert len(shell.posts) == 1

    shell.clock = NOW + timedelta(minutes=51)
    watch(shell, config)
    assert shell.topics() == ["unclassed:201:abc1234", "unfixed:202:fedcba9"]
    assert shell.post("unfixed:202:fedcba9") == {
        "to": "root,web-lane@d1",
        "kind": "blocker",
        "text": "STALL unfixed #202 at fedcba9, 61 min: web-lane classed buildkite/lint a real failure at 12:50 PM and has pushed no new head: 12:50 PM needs a schema fix; blocker sent | owner web-lane | root gives the fix of #202 at fedcba9 to a live lane unless web-lane pushes it now",
    }

    shell.clock = NOW + timedelta(minutes=56)
    watch(shell, config)
    assert len(shell.posts) == 2

    registry = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "long-running/drives/durable.json"
    registry.write_text(json.dumps(json.loads(registry.read_text()) | {"state_dir": str(tmp_path)}))
    capsys.readouterr()
    assert drive.main(["reds", "--drive", "durable"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "reds at 1:56 PM Oct 27: 2 open PRs read red, 1 unclassed",
        "#201 abc1234 | buildkite/lint | unclassed | owner api-lane | red since 12:35 PM Oct 27 | no action recorded",
        "#202 fedcba9 | buildkite/lint | real | owner web-lane | red since 12:35 PM Oct 27 | 12:50 PM needs a schema fix; blocker sent",
    ]


def test_a_red_of_a_lane_that_handed_off_goes_to_its_successor(config):
    """Drive watchdog #234379: #45894 read red after its lane handed off to a successor, and the alarm still named the stopped lane."""
    handed = record(2, "api-lane-5-handoff", "handoff", "7:13 PM handoff api-lane-5: doc 364b0df; successor api-lane-6", 40)
    working = record(3, "api-lane-6", "note", "picked up the chain", 30)
    shell = FakeShell([handed, working], [row("201", lane="api-lane-5", test_state="failure", red_at=stamp(25), failing_checks=json.dumps([{"name": "buildkite/lint", "url": "u"}]))])

    watch(shell, config)

    assert shell.post("unclassed:201:abc1234")["to"] == "root,api-lane-6@d1"
    assert "| owner api-lane-6 |" in shell.post("unclassed:201:abc1234")["text"]


def test_reds_with_no_live_red_sweep_lane_post_one_blocker_to_root_until_a_red_sweep_lane_posts(config):
    shell = FakeShell([], [row("201", test_state="failure", red_at=stamp(25), failing_checks=json.dumps([{"name": "buildkite/lint", "url": "u"}]))])

    watch(shell, config)
    assert shell.post("sweep:red-sweep") == {
        "to": "root",
        "kind": "blocker",
        "text": "STALL sweep 1 red PR, 25 min: no live red-sweep lane holds them: #201 (unclassed) | owner root | root spawns red-sweep from the long-running skill's reference/red-sweep-brief.md",
    }

    shell.records.append(record(1, "red-sweep-2", "state", "red-sweep-2 started", 0))
    watch(shell, config)
    assert "sweep:red-sweep" not in opened(config) and len(shell.posts) == 2


@pytest.mark.parametrize(("minutes", "stalls"), [(9, False), (11, True)], ids=["inside-10-min", "past-10-min"])
def test_a_finder_fidelity_lane_or_gate_stall_posts_ten_minutes_on(config, tmp_path, minutes, stalls):
    """Owner, prompt/1026-190849: "if we've truly been stuck for 10 mins then go debug why" (narrow-reading-sweep-1 #211573); finder, fidelity, lane and gate stalls waited 30."""
    watch(finderless := FakeShell([head(1, "101", HEAD, minutes), fidelity(2, "101", HEAD, minutes - 1)], [row("101", test_state="pending")]), config)
    watch(ungraded := FakeShell([head(1, "102", OTHER, minutes), finder(2, "102", OTHER, minutes - 1)], [row("102", OTHER, test_state="pending")]), config)
    landed = FakeShell([record(1, "p8a-step7-2", "state", "p8a-step7-2: parts 2 and 3 wait only on #43736; I open both when #43736 lands", 120), record(2, "desk-runner", "landed", "LANDED #43736 as 70ad7da45d: merged into dev", minutes, "43736")], [])
    watch(landed, config)
    merge_mode(config, "ask")
    task(tmp_path, 1, "api-lane", minutes_ago=200)
    quiet = FakeShell([*graded(1, "103", HEAD, 150), record(4, "root", "decide", "api-lane: rebase #103 on dev", minutes, to=["api-lane@d1"])], [row("103")])
    quiet.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": 103, "head": HEAD[:7]}]}]}]}
    quiet.answer = lambda question: {"explained": False}
    watch(quiet, config)
    assert ("finder:101:abc1234" in finderless.topics()) is stalls
    assert ("fidelity:102:fedcba9" in ungraded.topics()) is stalls
    assert ("gate:p8a-step7-2:43736:0" in landed.topics()) is stalls
    assert ("lane:api-lane" in quiet.topics()) is stalls
