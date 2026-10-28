"""stall_watch.py over recorded cci records, ledger rows, a board, a task list, and a scripted Sonnet: every post and every judge call is recorded on the fake shell."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import actions
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
    return {"seq": seq, "drive": DRIVE, "lane": lane, "kind": kind, "at": stamp(minutes_ago), "text": text, "to": ["root"], "refs": {"prs": [f"{REPO}#{pr}"]} if pr else {}} | extra


def head(seq: int, pr: str, sha: str, minutes_ago: float, text: str = "") -> dict:
    return record(seq, "api-lane", "head", text or f"HEAD #{pr} {sha}", minutes_ago, pr)


def finder(seq: int, pr: str, sha: str, minutes_ago: float) -> dict:
    return record(seq, "api-lane", "review", f"finder: ok at {sha[:7]}", minutes_ago, pr)


def fidelity(seq: int, pr: str, sha: str, minutes_ago: float) -> dict:
    return record(seq, "fidelity-desk", "review", f"fidelity: ok {sha[:7]}", minutes_ago, pr)


def row(pr: str, sha: str = HEAD, **fields) -> dict:
    return {"pr": pr, "head": sha, "lane": "api-lane", "state": "open", "test_state": "success", "branch": f"b{pr}", "base": "dev"} | fields


class FakeShell(stall_watch.Shell):
    def __init__(self, records: list[dict], rows: list[dict]) -> None:
        self.records = records
        self.rows = rows
        self.doc: dict = {"blocks": []}
        self.triage: dict = {}
        self.answer = lambda question: {}
        self.judge_code = 0
        self.ledger_code = 0
        self.posts: list[list[str]] = []
        self.prompts: list[str] = []
        self.review_titles: dict[str, str] = {}

    def run(self, argv, stdin=None, env=None, timeout=stall_watch.COMMAND_SECONDS):
        if argv[:2] == ["cci", "tail"]:
            since = argv[argv.index("--since") + 1]
            kinds = {arg.split("=", 1)[1] for arg in argv if arg.startswith("--kind=")}
            page = [found for found in self.records if (not since.isdigit() or found["seq"] > int(since)) and (not kinds or found["kind"] in kinds)]
            return stall_watch.Done(0, "".join(json.dumps(found) + "\n" for found in page), "")
        if argv[:2] == ["cci", "post"]:
            self.posts.append(argv)
            return stall_watch.Done(0, f"posted #{9000 + len(self.posts)}", "")
        if argv[:2] == ["cc-present", "outcomes"]:
            assert argv[argv.index("--session") + 1] == SESSION
            return stall_watch.Done(0, json.dumps({"doc": self.doc, "interactions": {"triage": self.triage}}), "")
        if argv[0] == "claude":
            assert argv[argv.index("--model") + 1] == stall_watch.MODEL and argv[argv.index("--effort") + 1] == "low"
            self.prompts.append(stdin)
            questions = json.loads(stdin.split("Questions:\n", 1)[1])
            verdicts = [{"id": question["id"], "why": "the record says so"} | self.answer(question) for question in questions]
            return stall_watch.Done(self.judge_code, json.dumps({"structured_output": {"verdicts": verdicts}}) if self.judge_code == 0 else "", "boom" if self.judge_code else "")
        if argv[:3] == ["ccx", "vcs", "gh"]:
            endpoint = argv[argv.index("--") + 2]
            assert endpoint.endswith("/check-runs?check_name=ai-review")
            return stall_watch.Done(0, self.review_titles[endpoint.split("/")[4]] + "\n", "")
        if Path(argv[1]).name == "ledger.py":
            return stall_watch.Done(self.ledger_code, json.dumps(self.rows) if self.ledger_code == 0 else "", "ccn down" if self.ledger_code else "")
        raise AssertionError(f"unexpected command {argv}")

    def now(self):
        return NOW

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
                "landing": {"repo": REPO, "ledger": "abc", "checkout": str(tmp_path / "checkout"), "holds": str(tmp_path / "holds.md"), "fidelity_lane": "fidelity-desk", "merge_mode": "every"},
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
    assert stall_watch.main(["--config", str(config)], shell) == 0


def test_a_head_no_finder_names_posts_one_urgent_record_to_root_and_its_lane_until_a_review_clears_it(config):
    shell = FakeShell([head(1, "101", HEAD, 40), fidelity(2, "101", HEAD, 35), head(3, "102", OTHER, 20), fidelity(4, "102", OTHER, 15)], [row("101"), row("102", OTHER)])

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == ["finder:101:abc1234"]
    assert shell.post("finder:101:abc1234")["kind"] == "urgent"
    assert shell.post("finder:101:abc1234")["to"] == "root,api-lane@d1"
    assert shell.post("finder:101:abc1234")["text"].startswith("STALL finder #101 at abc1234, 40 min: no finder review names this head")
    shell.records.append(finder(5, "101", HEAD, 1))
    watch(shell, config)
    assert "finder:101:abc1234" not in opened(config)
    shell.rows[0] = row("101", OTHER)
    shell.records += [head(6, "101", OTHER, 31), fidelity(7, "101", OTHER, 25)]
    watch(shell, config)
    assert shell.topics() == ["finder:101:abc1234", "finder:101:fedcba9"]


@pytest.mark.parametrize(("named", "stalls"), [(5, []), (40, ["finder:101:abc1234"])])
def test_a_new_head_counts_from_the_first_record_naming_it_never_from_an_older_record_with_no_sha(config, named, stalls):
    bulk = record(1, "api-lane", "head", "HEAD api-lane: #101 conflict fix, then the stack above restacked onto it. Published 9:00 AM.", 600, "101")
    ready = record(2, "api-lane", "report", f"[101] clean READY #101 at {HEAD[:10]}", named, "101")
    shell = FakeShell([bulk, ready, fidelity(3, "101", HEAD, 1)], [row("101")])

    watch(shell, config)

    assert shell.topics() == stalls
    if stalls:
        assert "no finder review names this head, first named 12:20 PM in #2" in shell.post("finder:101:abc1234")["text"]


def test_a_source_that_does_not_read_keeps_its_stall_open_so_nothing_posts_twice(config):
    shell = FakeShell([head(1, "101", HEAD, 40), fidelity(2, "101", HEAD, 35)], [row("101")])

    watch(shell, config)
    shell.ledger_code = 1
    watch(shell, config)
    shell.ledger_code = 0
    watch(shell, config)

    assert shell.topics() == ["finder:101:abc1234"]


def test_a_root_go_no_queued_record_names_goes_to_whoever_holds_its_next_step(config):
    records = [head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(4, "root", "go", f"go #101 at {HEAD[:7]}", 12)]
    records += [head(5, "102", OTHER, 30), finder(6, "102", OTHER, 25), fidelity(7, "102", OTHER, 25), record(8, "root", "go", f"go #102 at {OTHER[:7]}", 12)]
    shell = FakeShell(records, [row("101"), row("102", OTHER, test_state="failure")])

    watch(shell, config)

    assert shell.topics() == ["go:101:abc1234", "go:102:fedcba9"]
    assert shell.post("go:101:abc1234")["to"] == "root"
    assert "owner desk-runner | the landing desk has not queued it" in shell.post("go:101:abc1234")["text"]
    assert shell.post("go:102:fedcba9")["to"] == "root,api-lane@d1"
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

    assert shell.post("go:101:abc1234")["to"] == "root,api-lane@d1"
    assert f"owner api-lane | {step}" in shell.post("go:101:abc1234")["text"]


def test_gos_stacked_on_one_parent_post_once_naming_the_parent_and_a_held_parent_draws_none(config, tmp_path):
    (tmp_path / "holds.md").write_text("#301 waits on the schema release\n")
    records = [record(1, "root", "go", f"go #202 at {OTHER[:7]}", 12), record(2, "root", "go", f"go #203 at {OLD[:7]}", 11), record(3, "root", "go", f"go #302 at {OTHER[:7]}", 12)]
    rows = [row("201"), row("202", OTHER, base="b201"), row("203", OLD, base="b202"), row("301"), row("302", OTHER, base="b301")]
    shell = FakeShell(records, rows)

    watch(shell, config)

    assert shell.topics() == ["go:201:abc1234"]
    assert shell.post("go:201:abc1234")["to"] == "root"
    assert "root gos name #202, #203 in the stack from #201, and no QUEUED record does | owner root | #201 at abc1234 has no go, and a stack enqueues from the bottom" in shell.post("go:201:abc1234")["text"]


def test_gos_above_a_green_unqueued_bottom_whose_own_builds_run_post_one_stall_naming_the_bottom(config):
    records = [record(1, "root", "go", f"go #401 at {HEAD[:7]}", 30), record(2, "root", "go", f"go #402 at {OTHER[:7]}", 30), record(3, "root", "go", f"go #403 at {OLD[:7]}", 30)]
    rows = [row("401"), row("402", OTHER, base="b401", test_state="pending"), row("403", OLD, base="b402", test_state="pending")]
    shell = FakeShell(records, rows)

    watch(shell, config)

    assert shell.topics() == ["go:401:abc1234"]
    assert "root gos name #401, #402, #403 in the stack from #401, and no QUEUED record does | owner desk-runner | the landing desk has not queued it" in shell.post("go:401:abc1234")["text"]


@pytest.mark.parametrize(("graded", "stalls"), [(3, []), (15, ["go:101:abc1234"])])
def test_a_go_a_fidelity_carry_moved_to_a_new_head_stalls_only_once_the_carry_grade_is_old(config, graded, stalls):
    carry = record(3, "fidelity-desk", "review", f"fidelity: ok {HEAD[:7]} carried from {OLD[:7]}, patch-identical replay onto dev.", graded, "101")
    shell = FakeShell([record(1, "root", "go", f"go #101 at {OLD[:7]}", 60), head(2, "101", HEAD, 40), carry], [row("101")])

    watch(shell, config)

    assert shell.topics() == stalls


def test_a_go_the_landing_desk_queued_at_its_head_is_no_stall(config):
    records = [head(1, "101", HEAD, 30), finder(2, "101", HEAD, 25), fidelity(3, "101", HEAD, 25), record(4, "root", "go", f"go #101 at {HEAD[:7]}", 12)]
    records.append(record(5, "desk-runner", "report", f"QUEUED #101 at {HEAD[:10]} (go #4): the landing runner put it in the merge queue at 12:52 PM", 8))
    shell = FakeShell(records, [row("101")])

    watch(shell, config)

    assert shell.topics() == []


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


def test_a_tap_at_an_earlier_head_asks_sonnet_once_whether_the_new_head_replays_it(config):
    restack = f"HEAD #101 {HEAD} (was {OLD}): restack, range-diff patch-identical (3 commits)"
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 20, restack)], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}
    shell.answer = lambda question: {"restack": True}

    watch(shell, config)
    watch(shell, config)

    assert len(shell.prompts) == 1
    assert restack in shell.prompts[0]
    assert shell.topics() == ["carry:101:abc1234"]
    assert shell.post("carry:101:abc1234")["to"] == "root,fidelity-desk@d1"
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


def test_a_failed_sonnet_call_leaves_the_carry_to_both_owners_and_asks_again_next_pass(config):
    shell = FakeShell([head(1, "101", OLD, 90), head(2, "101", HEAD, 20)], [row("101")])
    shell.triage = {"merge-prs": {"101": {"verdict": "approved", "head": OLD[:7], "at": stamp(60)}}}
    shell.judge_code = 1

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
        record(2, "lane-b", "state", "waiting on CI build 123, about an hour", 40),
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
        {"kind": "silence", "lane": "lane-b", "last_at": stamp(40), "minutes_silent": 40, "last_record": "waiting on CI build 123, about an hour", "id": "lane:lane-b:2"},
        {"kind": "silence", "lane": "lane-c", "last_at": stamp(20), "minutes_silent": 20, "last_record": "next state by 12:50 PM", "id": "lane:lane-c:3"},
    ]
    assert sorted(shell.topics()) == ["lane:lane-a", "lane:lane-c", "lane:lane-d"]
    assert shell.post("lane:lane-a")["to"] == "root,lane-a@d1"
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
    assert "answer it with `cci post --re 1`" in shell.post("ask:1")["text"]


def test_a_held_pr_draws_no_stall(config, tmp_path):
    (tmp_path / "holds.md").write_text("#101 waits on the schema release\n")
    shell = FakeShell([head(1, "101", HEAD, 40)], [row("101"), row("102", OTHER, hold_until=stamp(-60))])
    shell.records.append(head(2, "102", OTHER, 40))

    watch(shell, config)

    assert shell.topics() == []
