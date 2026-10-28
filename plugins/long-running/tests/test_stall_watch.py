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
    return {"pr": pr, "head": sha, "lane": "api-lane", "state": "open", "test_state": "success", "ai_review": "success", "branch": f"b{pr}", "base": "dev"} | fields


class FakeShell(stall_watch.Shell):
    def __init__(self, records: list[dict], rows: list[dict]) -> None:
        self.records = records
        self.rows = rows
        self.doc: dict = {"blocks": []}
        self.triage: dict = {}
        self.choices: dict = {}
        self.answer = lambda question: {}
        self.judge_code = 0
        self.ledger_code = 0
        self.posts: list[list[str]] = []
        self.prompts: list[str] = []
        self.review_titles: dict[str, str] = {}
        self.clock = NOW

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
            return stall_watch.Done(0, json.dumps({"doc": self.doc, "interactions": {"triage": self.triage, "choices": self.choices}}), "")
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


def graded(seq: int, pr: str, sha: str, minutes_ago: float) -> list[dict]:
    return [head(seq, pr, sha, minutes_ago), finder(seq + 1, pr, sha, minutes_ago - 1), fidelity(seq + 2, pr, sha, minutes_ago - 1)]


def stack(config: Path) -> dict[str, dict]:
    return {found["pr"]: found for found in json.loads((config.parent / "inbox/stack.json").read_text())["prs"]}


@pytest.mark.parametrize(
    ("fields", "state", "step"),
    [
        ({"test_state": "failure", "failing_checks": json.dumps([{"name": "buildkite/test"}])}, "red on buildkite/test", "fix the red build of #101 at abc1234"),
        ({"mergeable_state": "dirty"}, "conflicts with its base", "restack #101 at abc1234 and post HEAD"),
        ({"base": "graphite-base/101"}, "needs a restack: the PR below it landed", "restack #101 at abc1234 onto its new parent with `ccx vcs ship --tip-only` and post HEAD"),
        ({"ai_review": "failure"}, "the reviewer bot holds it", "answer each blocking finding on #101 with a fix or a one-line rebuttal"),
    ],
)
def test_a_pr_in_one_state_for_two_hours_posts_one_stale_record_naming_its_owner_and_next_step_until_its_state_moves(config, fields, state, step):
    shell = FakeShell(graded(1, "101", HEAD, 150), [row("101", **fields)])
    shell.doc = {"blocks": [{"id": f"merge:101:{HEAD[:7]}", "type": "card", "children": []}]}

    watch(shell, config)
    watch(shell, config)

    assert shell.topics() == ["stale:101:abc1234"]
    assert shell.post("stale:101:abc1234") == {"to": "root,api-lane@d1", "kind": "urgent", "text": f"STALE #101 at abc1234, 150 min: {state} | owner api-lane | {step}"}
    shell.rows[0] = row("101", OTHER, **fields)
    shell.records += graded(4, "101", OTHER, 5)
    watch(shell, config)
    assert opened(config) == {}


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
    [({}, []), ({"test_state": "failure"}, ["stale:201:abc1234", "stale:202:abc1234", "stale:303:abc1234"])],
    ids=["gates-on-the-tap-list", "a-gate-is-red"],
)
def test_a_held_batch_whose_holds_line_names_prs_on_or_stacked_above_the_tap_list_posts_one_state_record_per_head_and_a_red_gate_stales_it(config, tmp_path, gate, stales):
    merge_mode(config, "ask")
    (tmp_path / "holds.md").write_text("#201 held (root #159418): lands after #301 and #302\n#202 held (root #159418): same batch as #201\n")
    records = [*graded(1, "201", HEAD, 150), *graded(4, "202", HEAD, 150), *graded(7, "301", HEAD, 150), *graded(10, "302", HEAD, 150), *graded(13, "303", HEAD, 150)]
    shell = FakeShell(records, [row("201"), row("202"), row("301"), row("302", base="b303"), row("303", **gate)])
    shell.doc = {"blocks": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "prs": [{"number": number, "head": HEAD[:7]} for number in (301, 302, 303)]}]}]}

    watch(shell, config)
    watch(shell, config)

    assert sorted(topic for topic in shell.topics() if topic.startswith("stale:")) == stales
    if not stales:
        assert [topic for topic in shell.topics() if topic.startswith("tap-wait:")] == ["tap-wait:digest"]
        assert shell.post("tap-wait:digest")["text"].startswith("4 heads wait on the owner's tap: #201 at abc1234 is held on #301, #303, each waiting on the owner's tap, his board answer, or the merge queue, since 10:30 AM; #202 at abc1234 is held on #301, #303")


@pytest.mark.parametrize(
    ("line", "pr_block", "answer", "topic"),
    [
        ("#401 off the merge card until his pick on the eviction-card question", True, None, "tap-wait:401:abc1234"),
        ("#401 waits on eviction-route-choice", False, None, "tap-wait:401:abc1234"),
        ("#401 off the merge card until his pick on the eviction-card question", True, {"optionIds": ["eviction-route-app"]}, "stale:401:abc1234"),
    ],
    ids=["card-holding-the-pr-unanswered", "block-id-unanswered", "card-answered"],
)
def test_a_hold_on_an_owner_card_waits_while_the_card_is_unanswered_and_stales_once_he_answers(config, tmp_path, line, pr_block, answer, topic):
    merge_mode(config, "ask")
    (tmp_path / "holds.md").write_text(line + "\n")
    shell = FakeShell(graded(1, "401", HEAD, 150), [row("401")])
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


@pytest.mark.parametrize(("ready", "stalls"), [(False, []), (True, ["board:101:abc1234"])], ids=["row-pulled", "row-due-after-ready"])
@pytest.mark.parametrize(("text", "stalls"), [("Owners for the reds: #101 stays with api-lane, READY", []), ("api-lane: rebase #101 on dev", ["lane:api-lane"])], ids=["listed-as-recipient", "given-an-order"])
def test_a_lane_owes_a_root_decide_only_when_it_gives_the_lane_an_order(config, tmp_path, text, stalls):
    task(tmp_path, 1, "api-lane", minutes_ago=90)
    shell = FakeShell([record(1, "api-lane", "report", f"READY #101 at {HEAD[:7]}", 60, "101"), record(2, "root", "decide", text, 45, to=["api-lane@d1", "fidelity-desk@d1"])], [])

    watch(shell, config)

    assert shell.topics() == stalls


def test_a_merge_row_root_pulled_by_a_decide_is_not_missing_until_the_lane_posts_ready(config, ready, stalls):
    records = [head(1, "101", HEAD, 40), finder(2, "101", HEAD, 20), fidelity(3, "101", HEAD, 20), record(4, "root", "decide", "Confirmed: pull #101's tap row until api-lane posts READY with the red fixed", 15, "101")]
    if ready:
        records.append(record(5, "api-lane", "report", f"READY #101 at {HEAD[:7]}", 5, "101"))
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
    (tmp_path / "holds.md").write_text("#301 waits on the schema release\n")
    records = [*graded(1, "201", HEAD, 200), *graded(4, "202", OTHER, 200), *graded(7, "203", OLD, 200), head(10, "301", HEAD, 200)]
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
    shell = FakeShell(records, [row("101"), row("201")])

    watch(shell, config)

    assert shell.post(topic)["to"] == to


def test_a_stall_that_clears_and_comes_back_posts_again_only_thirty_minutes_after_its_last_post(config):
    review = finder(3, "101", HEAD, 1)
    shell = FakeShell([head(1, "101", HEAD, 40), fidelity(2, "101", HEAD, 35)], [row("101")])

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
