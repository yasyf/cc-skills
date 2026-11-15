import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import LEAD, Answers, Judge
from drive import cli, github, land, limits, refs, served, settled, slack, stage, state
from drive.state import DriveError

KEY = "Forge-AI/monorepo#42"
URL = "https://github.com/Forge-AI/monorepo/pull/42"
READ_CHANGE = served.change
SHOWN = settled.shown
JUDGE = served.judged
SOURCE = ["app/fixtures.go", "app/fixtures_load.go", "cmd/fixtures.go", "tools/move_fixtures.py"]
SMALL = b"diff --git a/api/run.ts b/api/run.ts\nindex 1111111111aa..2222222222bb 100644\n--- a/api/run.ts\n+++ b/api/run.ts\n@@ -10,3 +10,3 @@ export function run() {\n   const flag = staff();\n-  return flag;\n+  return !flag;\n }\n"


def added(path: str, body: bytes) -> bytes:
    return b"diff --git a/%s b/%s\nnew file mode 100644\nindex 000000000000..0123456789ab\n--- /dev/null\n+++ b/%s\n@@ -0,0 +1 @@\n+%s\n" % (path.encode(), path.encode(), path.encode(), body)


MOVE = b"".join(
    [
        *(added(f"app/testdata/fixtures/case-{n:04}/blob", b"\xb5" + b"x" * 2_000) for n in range(1_567)),
        *(added(path, b"package app") for path in SOURCE),
    ]
)


def test_a_pr_past_githubs_300_file_diff_reads_whole_as_bytes_from_the_compare_of_the_base_and_head_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def gh(cmd: list[str], text: bool = False, **_: object) -> subprocess.CompletedProcess:
        calls.append(cmd)
        out = json.dumps({"title": "Cleanup: move the fixtures", "body": "", "baseRefName": "dev", "baseRefOid": "base1", "headRefOid": "head1"}).encode() if "view" in cmd else MOVE
        return subprocess.CompletedProcess(cmd, 0, out.decode() if text else out, "" if text else b"")

    monkeypatch.setattr(github.subprocess, "run", gh)
    read = READ_CHANGE(KEY)
    assert (read["base"], read["head"], read["diff"], len(served.files(read["diff"]))) == ("dev", "head1", MOVE, 1_571)
    assert "repos/Forge-AI/monorepo/compare/base1...head1" in calls[1]


def test_the_judge_sees_source_before_fixtures_a_byte_utf8_cannot_read_and_a_capped_count_of_the_files_left_out() -> None:
    prompt = served.shown({KEY: {"title": "Cleanup: move the fixtures", "head": "head1", "diff": MOVE}})
    left_out = prompt.rpartition("\n")[2]
    assert all(f"+++ b/{path}" in prompt for path in SOURCE) and "+\ufffdxxx" in prompt
    assert left_out.startswith(f"{1_571 - prompt.count('diff --git a/')} files left out for length: {KEY} app/testdata/fixtures/")
    assert len(left_out) < served.LEFT_OUT_CHARS + 100 and len(prompt) < served.CHANGE_CHARS + served.LEFT_OUT_CHARS + 1_000


def test_a_restack_that_moves_the_hunks_and_the_lines_around_them_carries_the_check_and_one_that_changes_a_line_keeps_it_on_the_head_it_passed(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    stamp = {"head": "head1", "read": "head1", "orders": ["o1"], "lines": served.digests(SMALL), "beside": {}}
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "head2", "checked": stamp}
    restacked = SMALL.replace(b"@@ -10,3 +10,3 @@ export function run() {", b"@@ -14,3 +14,3 @@ export function start() {").replace(b"1111111111aa", b"3333333333cc")
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head2", "diff": restacked.replace(b"   const flag = staff();", b"   const flag = isStaff();")})
    assert served.carry(demo, [KEY]) == [KEY]
    assert state.load(demo)["prs"][KEY]["checked"] == stamp | {"head": "head2", "read": "head2"}
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "head3"}
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head3", "diff": SMALL.replace(b"+  return !flag;", b"+  return flag && !staff();")})
    assert served.carry(demo, [KEY]) == []
    pr = state.load(demo)["prs"][KEY]
    assert (pr["checked"], served.unread(pr), state.checked(state.load(demo), pr)) == (stamp | {"head": "head2", "read": "head3"}, False, False)


LAPSE = b"diff --git a/watch/lapse.go b/watch/lapse.go\nindex 4444444444dd..5555555555ee 100644\n--- a/watch/lapse.go\n+++ b/watch/lapse.go\n@@ -40,3 +40,6 @@ func (w *Watch) Checked() {\n \tw.mu.Lock()\n+\tif time.Since(w.changed) > w.limit {\n+\t\tw.cancel()\n+\t}\n \tdefer w.mu.Unlock()\n }\n"
WAKES = b"diff --git a/serve/wakes.go b/serve/wakes.go\nindex 6666666666ff..7777777777aa 100644\n--- a/serve/wakes.go\n+++ b/serve/wakes.go\n@@ -12,3 +12,4 @@ func wake() {\n \tw := next()\n+\ttext := description()\n \tsend(w)\n }\n"


def test_a_restack_keeps_the_checks_pass_on_each_file_whose_added_and_removed_lines_it_passed_and_judges_the_files_whose_lines_changed(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, judge: Judge
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "gets an inbox message when it changes")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    reads = {"head": "head1", "diff": LAPSE + WAKES}
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: watches wake their agent", "body": ""} | reads)
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "head1", "checked": None}
    assert served.verdict(demo, KEY).startswith(served.CHECKED)
    restacked = LAPSE.replace(b"@@ -40,3 +40,6 @@", b"@@ -52,3 +52,6 @@").replace(b" \tdefer w.mu.Unlock()", b" \tdefer w.unlock()").replace(b"4444444444dd", b"8888888888bb")
    reads |= {"head": "head2", "diff": restacked + WAKES.replace(b"description()", b"description(ctx)")}
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head2"
    assert served.carry(demo, [KEY]) == []
    lapsed = {"order": "o1", "words": "gets an inbox message when it changes", "file": "watch/lapse.go", "change": "watch/lapse.go cancels a watch that lapsed, which he asked for no such lapse"}
    woke = {"order": "o1", "words": "gets an inbox message when it changes", "file": "serve/wakes.go", "change": "serve/wakes.go drops its context"}
    judge.against = [lapsed, woke]
    refused = served.verdict(demo, KEY)
    assert refused.startswith(served.NOT_READY) and "serve/wakes.go drops its context" in refused and "lapse" not in refused
    judge.against = [lapsed]
    assert served.verdict(demo, KEY).startswith(f"{served.CHECKED} against the words of o1 at head2")
    run("order", "add", "--title", "Second", "--project", "The drive", "--workstream", "Drive tooling", "suspend a chat past its limit")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["carries"]["o2"] = []
        data["prs"][KEY]["head"] = "head3"
    reads |= {"head": "head3"}
    assert served.carry(demo, [KEY]) == [KEY]
    assert served.verdict(demo, KEY).startswith(served.NOT_READY)


CACHE_RULING = {"id": "c0de123" + "0" * 33, "title": "How should the cache key?", "body": '"Key the cache on the run id"\nNotes: cci owner 1', "tags": ["scope:durable", "code:go/sand/cache"], "created_at": "2026-11-11T19:10:00Z"}
WHERE_WRITTEN = {
    "id": f"{served.WRITES_RULING}6f39210d69a55a69a1c6114c830579517",
    "title": "When he settles how a part of the code must work, where does that rule get written down?",
    "body": f'"Add to our agents.md in the signal server code that signals are always light pings" then, 13 seconds later: "{served.WRITES_WORDS}"\nNotes: cci owner 2',
    "tags": ["scope:durable"],
    "created_at": "2026-11-11T19:18:20Z",
}
GUIDE_AT = re.compile(r'(g\d+_\d+): object\(expression: "refs/heads/([^:"]+):([^"]+)"\)')
OWED = f'by his answer {served.WRITES_RULING}, this change owes each of these rulings of his written, his words verbatim, into an AGENTS.md at or above the code it rules on'


def test_a_ruling_on_code_is_owed_once_by_a_change_to_that_code_its_missing_write_refused_unjudged_and_its_write_judged_as_his_words(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, judge: Judge, recorded: Answers
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "key the cache on the run")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    with state.edit(demo) as data:
        data["jobs"]["j1"]["brief_flags"] = {"ruling": ["c0de123"]}
    recorded.shown |= {"c0de123": CACHE_RULING, served.WRITES_RULING: WHERE_WRITTEN}
    home, based, asked = ["Forge-AI/monorepo"], {}, []
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: home[0])
    monkeypatch.setattr(github, "graphql", lambda text, what, max_age="0s": asked.append(GUIDE_AT.findall(text)) or {"data": {"repository": {alias: {"text": based[path]} if path in based else None for alias, _, path in asked[-1]}}})
    code = added("go/sand/cache/key.go", b"func key() string { return run.ID }")
    write = added("go/sand/AGENTS.md", b"- Ruled Nov 11: Key the cache on the run id")

    def judged_at(n: int, diff: bytes) -> str:
        with state.edit(demo) as data:
            data["prs"][KEY]["head"] = f"head{n}"
        monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: key the cache", "body": "", "base": "dev", "head": f"head{n}", "diff": diff})
        return served.verdict(demo, KEY)

    refused = judged_at(1, code)
    assert refused.startswith(
        f'{served.NOT_READY}: {KEY} go against his words, judged from each PR\'s title and diff: c0de123 "Key the cache on the run id": {KEY} changes go/sand/cache/key.go,'
        " under go/sand/cache, and no AGENTS.md at or above go/sand/cache holds these words of his at the tip of its base branch, at the head of another open PR or in its diff; add them verbatim with their date to the"
        f' AGENTS.md nearest go/sand/cache, by his ruling {served.WRITES_RULING}, "{served.WRITES_WORDS}". His answers its design cites, and each other one an objection names, each whole:'
    ), refused
    assert (WHERE_WRITTEN["title"] in refused, judge.asked, {(branch, path) for _, branch, path in asked[-1]}) == (
        True,
        [],
        {("dev", "go/sand/cache/AGENTS.md"), ("dev", "go/sand/AGENTS.md"), ("dev", "go/AGENTS.md"), ("dev", "AGENTS.md")},
    )
    assert judged_at(2, code + write).startswith(served.CHECKED)
    said = judge.asked[-1][0]
    assert f'{OWED}, since it changes that code and no AGENTS.md there holds them at the tip of its base branch or at the head of an open PR numbered below it, so that write is his words: c0de123 on go/sand/cache, "Key the cache on the run id"' in said
    assert served.WRITES_WORDS in said
    based["AGENTS.md"] = "# Agents\n\n> Ruled Nov 11: key the cache on\n> the run id.\n"
    assert judged_at(3, code).startswith(served.CHECKED) and OWED not in judge.asked[-1][0]
    reads = len(asked)
    assert judged_at(4, added(".claude/long-running.toml", b"[rules]") + write).startswith(served.CHECKED) and OWED not in judge.asked[-1][0]
    home[0] = "yasyf/cc-skills"
    del based["AGENTS.md"]
    assert judged_at(5, code).startswith(served.CHECKED) and OWED not in judge.asked[-1][0]
    assert len(asked) == reads


CACHE_CODE = added("go/sand/cache/key.go", b"func key() string { return run.ID }")
CACHE_WRITE = added("go/sand/AGENTS.md", b"- Ruled Nov 11: Key the cache on the run id")
CACHE_GUIDE = "# Agents\n\n- Ruled Nov 11: Key the cache on the run id\n"


def open_rows(demo: str, rows: dict[int, tuple[str, str, str]]) -> dict:
    with state.edit(demo) as data:
        for number, (branch, held, changes) in rows.items():
            data["prs"][f"Forge-AI/monorepo#{number}"] = data["prs"][KEY] | {
                "url": f"https://github.com/Forge-AI/monorepo/pull/{number}",
                "branch": branch,
                "state": held,
                "checked": {"head": "head1", "read": "head1", "orders": ["o1"], "lines": {changes: "d"}},
            }
    return state.load(demo)


@pytest.mark.parametrize(
    ("rows", "holding", "diff", "owes"),
    [
        pytest.param({45: ("fix/cache-b", "open", "AGENTS.md")}, {"fix/cache-b"}, CACHE_CODE + CACHE_WRITE, [True], id="a higher-numbered head holding it leaves the write with the lowest PR writing it"),
        pytest.param({40: ("fix/cache-a", "open", "AGENTS.md")}, {"fix/cache-a"}, CACHE_CODE + CACHE_WRITE, [], id="a lower-numbered head holding it owes this copy to nobody"),
        pytest.param({45: ("fix/cache-b", "open", "AGENTS.md")}, {"fix/cache-b"}, CACHE_CODE, [], id="any open head holding it owes nothing to a PR not writing it"),
        pytest.param(
            {40: ("fix/cache-a", "closed", "AGENTS.md"), 41: ("fix/cache-c", "merged", "AGENTS.md")}, {"fix/cache-a", "fix/cache-c"}, CACHE_CODE, [False], id="no head but an open one counts"
        ),
        pytest.param({40: ("fix/cache-a", "open", "AGENTS.md")}, {"dev"}, CACHE_CODE, [], id="the base branch's tip holding it owes it to nobody"),
        pytest.param({40: ("fix/cache-a", "open", "go/sand/cache/key.go")}, {"fix/cache-a"}, CACHE_CODE, [False], id="an open head whose diff writes no AGENTS.md there is never read"),
    ],
)
def test_a_ruling_held_at_the_base_branchs_tip_or_at_the_head_of_an_open_pr_whose_diff_changes_an_agents_md_above_its_code_is_owed_only_by_the_lowest_numbered_pr_whose_head_holds_it_read_in_one_query(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, recorded: Answers, rows: dict, holding: set[str], diff: bytes, owes: list[bool]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "key the cache on the run")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    recorded.shown |= {"c0de123": CACHE_RULING}
    asked: list[list[tuple[str, str, str]]] = []
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: "Forge-AI/monorepo")
    monkeypatch.setattr(
        github, "graphql", lambda text, what, max_age="0s": asked.append(GUIDE_AT.findall(text)) or {"data": {"repository": {alias: {"text": CACHE_GUIDE} if branch in holding and path == "AGENTS.md" else None for alias, branch, path in asked[-1]}}}
    )
    data = open_rows(demo, rows | {7: ("fix/elsewhere", "open", "go/other/AGENTS.md")})
    data["prs"]["yasyf/cc-skills#3"] = data["prs"][KEY] | {"branch": "fix/cache-other-repo", "checked": data["prs"]["Forge-AI/monorepo#7"]["checked"] | {"lines": {"AGENTS.md": "d"}}}
    found = served.owed(data, KEY, ["c0de123"], {"base": "dev", "head": "head1", "diff": diff})
    assert [each["written"] for each in found] == owes
    read = {branch for _, branch, _ in asked[0]}
    assert (len(asked), read) == (1, {"dev", *(branch for branch, held, changes in rows.values() if held == "open" and changes == "AGENTS.md")})


@pytest.mark.parametrize("tracked", [1, 100])
def test_the_agents_md_read_is_one_query_of_one_repository_naming_the_same_files_at_1_tracked_pr_and_at_100_since_only_the_heads_whose_diffs_change_them_are_read(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, recorded: Answers, tracked: int
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "key the cache on the run")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    recorded.shown |= {"c0de123": CACHE_RULING}
    asked: list[str] = []
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: "Forge-AI/monorepo")
    monkeypatch.setattr(github, "graphql", lambda text, what, max_age="0s": asked.append(text) or {"data": {"repository": {alias: None for alias, _, _ in GUIDE_AT.findall(text)}}})
    idle = {1000 + n: (f"fix/idle-{n}", "open", f"go/idle/{n}.go") for n in range(tracked - 1)}
    data = open_rows(demo, {40: ("fix/cache-a", "open", "go/sand/AGENTS.md")} | idle)
    found = served.owed(data, KEY, ["c0de123"], {"base": "dev", "head": "head1", "diff": CACHE_CODE})
    assert ([each["written"] for each in found], len(asked)) == ([False], 1)
    assert (asked[0].count("repository("), len(GUIDE_AT.findall(asked[0])), {branch for _, branch, _ in GUIDE_AT.findall(asked[0])}) == (1, 8, {"dev", "fix/cache-a"})


def test_an_answer_cc_notes_prints_with_no_tags_key_reads_as_ruling_no_code_so_a_pr_binding_it_owes_no_write(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "key the cache on the run")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    unlabelled = {"id": "fd79b94" + "0" * 33, "title": "Merge it?", "body": '"Merge it"', "created_at": "2026-11-14T19:10:00Z"}
    monkeypatch.setattr(settled, "shown", SHOWN)
    monkeypatch.setattr(settled, "ccn", lambda argv, text, cwd, failure: json.dumps(unlabelled))
    monkeypatch.setattr(github, "checkout_repo", lambda cwd: "Forge-AI/monorepo")
    assert served.owed(state.load(demo), KEY, ["fd79b94"], {"base": "dev", "head": "head1", "diff": CACHE_CODE}) == []


CLOCKS = ("self::sleep(seconds) is a clock timer that fires one inbox row", "self::watch sets a watch and then suspends", "each alarm is its own clock timer")


def clocks(run: Callable[..., tuple[int, str]]) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "inbox clocks wake an agent", "--parts", *CLOCKS)
    run("job", "add", "builder", "--order", "o1", *(word for part in CLOCKS for word in ("--part", part)), "--goal", "land")


def stacked(run: Callable[..., tuple[int, str]], demo: str, numbers: list[int], base: str) -> list[str]:
    for number in numbers:
        assert run("pr", "add", f"https://github.com/Forge-AI/monorepo/pull/{number}", "--job", "j1", *LEAD)[0] == 0
    with state.edit(demo) as data:
        for below, number in zip([base, *(f"b{number}" for number in numbers)], numbers, strict=False):
            data["prs"][f"Forge-AI/monorepo#{number}"] |= {"head": f"head{number}", "branch": f"b{number}", "base": below}
    return [f"Forge-AI/monorepo#{number}" for number in numbers]


def test_one_check_judges_a_whole_stack_and_keeps_its_verdict_on_each_pr_so_a_restack_carries_it_and_a_joining_or_pushed_pr_owes_the_stack_one_check(
    run: Callable[..., tuple[int, str]], demo: str, judge: Judge, checkers: list[str]
) -> None:
    clocks(run)
    keys = stacked(run, demo, [42, 43, 44], "main")
    assert served.verdict(demo, keys[0]).startswith(f"{served.CHECKED} against the words of o1 at head42")
    [(said, changes)] = judge.asked
    assert list(changes) == keys and f"this change delivers: {'; '.join(CLOCKS)}" in said and "other open PRs" not in said
    data = state.load(demo)
    assert [data["prs"][key]["checked"]["beside"] for key in keys] == [{keys[1]: "head43", keys[2]: "head44"}, {keys[0]: "head42", keys[2]: "head44"}, {keys[0]: "head42", keys[1]: "head43"}]
    assert all(data["prs"][key]["checker"]["outcome"].startswith(served.CHECKED) and served.stands(data, key, tuple(keys)) for key in keys)
    with state.edit(demo) as data:
        data["prs"][keys[2]]["head"] = "head44r"
    assert served.carry(demo, keys) == [keys[2]]
    data = state.load(demo)
    assert all(state.checked(data, data["prs"][key]) for key in keys) and data["prs"][keys[0]]["checked"]["beside"][keys[2]] == "head44r"
    joined = (*keys, *stacked(run, demo, [45], "b44"))
    data = state.load(demo)
    assert land.landing_stacks(data)[keys[0]] == joined and not any(served.stands(data, key, joined) for key in keys)
    with state.edit(demo) as data:
        data["prs"][keys[1]]["head"] = "head43b"
    data = state.load(demo)
    assert not any(state.checked(data, data["prs"][key]) for key in keys)
    served.start(demo, list(joined))
    assert checkers == [keys[0]]
    judge.against = [{"order": "o1", "words": "each alarm is its own clock timer", "file": "", "change": "no PR here builds the alarm rows"}]
    assert served.verdict(demo, keys[1]).startswith(f"{served.NOT_READY}: {', '.join(joined)} go against his words")
    data = state.load(demo)
    assert list(judge.asked[-1][1]) == list(joined)
    assert [state.against(data["prs"][key]) for key in joined] == ['o1 "each alarm is its own clock timer": no PR here builds the alarm rows'] * 4
    assert [event["number"] for event, _ in state.read_events(demo) if event["kind"] == "check" and event["change"].startswith(served.NOT_READY)] == [42]


def test_a_not_ready_judged_apart_from_the_stack_its_pr_sits_in_is_no_try_so_the_stack_owes_one_check_until_one_check_judged_it_together(
    run: Callable[..., tuple[int, str]], demo: str
) -> None:
    clocks(run)
    keys = stacked(run, demo, [42, 43], "main")
    with state.edit(demo) as data:
        for pid, key in enumerate(keys, start=100):
            pr = data["prs"][key]
            pr["checker"] = {
                "head": pr["head"], "pid": pid, "at": state.stamp(), "orders": state.served_orders(data, pr), "answers": served.binds(demo, data, pr), "outcome": f"{served.NOT_READY}: {key} go against his words", "against": ""
            }
    moment = state.parse_time(state.stamp())
    data = state.load(demo)
    assert [served.tried(demo, data, key, tuple(keys), moment) for key in keys] == [False, False]
    assert [served.tried(demo, data, key, (key,), moment) for key in keys] == [True, True]
    with state.edit(demo) as data:
        data["prs"][keys[1]]["checker"]["pid"] = 100
    data = state.load(demo)
    assert [served.tried(demo, data, key, tuple(keys), moment) for key in keys] == [True, True]


def test_a_pr_added_naming_its_part_is_judged_on_that_part_alone_and_one_naming_a_part_its_job_lacks_is_refused_with_nothing_added(
    run: Callable[..., tuple[int, str]], demo: str, judge: Judge, capsys: pytest.CaptureFixture[str]
) -> None:
    clocks(run)
    assert run("pr", "add", URL, "--job", "j1", "--part", CLOCKS[0], *LEAD)[0] == 0
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head1"
    assert served.verdict(demo, KEY).startswith(served.CHECKED)
    assert f"this change delivers: {CLOCKS[0]}\n  other changes deliver: {'; '.join(CLOCKS[1:])}" in judge.asked[-1][0]
    other = "https://github.com/Forge-AI/monorepo/pull/43"
    assert cli.main(["pr", "add", other, "--job", "j1", "--part", "a part nobody ordered", *LEAD]) == 2
    assert "Forge-AI/monorepo#43: refused, nothing added: its job j1 delivers no part a part nobody ordered" in capsys.readouterr().err
    assert "Forge-AI/monorepo#43" not in state.load(demo)["prs"]


STORED_TAPS = {
    "before patch was a field": {"label": "Approve", "at": "2026-11-12T16:11:40-08:00", "head": "head0", "source": "pressed the button"},
    "a card press kept a patch": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "patch": "p0"},
    "a later head read beside the patch": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "patch": "p0", "read": "head1"},
}


def moved_check(run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head2", "diff": SMALL})
    return {"head": "head2", "checked": {"head": "head1", "read": "head1", "orders": ["o1"], "lines": served.digests(SMALL), "beside": {}}}


@pytest.mark.parametrize("stored", list(STORED_TAPS))
def test_a_moved_check_carries_beside_his_approve_in_each_shape_a_drive_stored_it_on_another_head_which_loads_without_patch_or_read(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, stored: str
) -> None:
    tap = STORED_TAPS[stored]
    moved = moved_check(run, demo, monkeypatch)
    with state.edit(demo) as data:
        data["prs"][KEY] |= moved | {"tap": tap}
    assert served.carry(demo, [KEY]) == [KEY]
    pr = state.load(demo)["prs"][KEY]
    loaded = {"label": "Approve", "at": tap["at"], "head": "head0", "source": "pressed the button", "action_ts": tap.get("action_ts"), "picks": {}}
    assert (pr["checked"]["head"], pr["tap"], served.unread(pr)) == ("head2", loaded, False)


def test_his_approve_recorded_by_pr_set_tapped_stays_on_its_head_through_a_push_that_carries_the_check(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = moved_check(run, demo, monkeypatch)
    link = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794173216327589"
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head1"
    assert run("pr", "set", URL, "--tapped", "Approve", "--tap-source", link)[0] == 0
    with state.edit(demo) as data:
        data["prs"][KEY] |= moved
    assert served.carry(demo, [KEY]) == [KEY]
    pr = state.load(demo)["prs"][KEY]
    assert (pr["checked"]["head"], pr["tap"] | {"at": ""}) == ("head2", {"label": "Approve", "at": "", "head": "head1", "source": link, "action_ts": None, "picks": {}})


GAPS = (
    'b3cbd28, 2026-11-12T02:15:56-08:00, asked "Do you accept the two wrong-id gaps the reviewer bot is holding the UUIDv7 row ids change on?":'
    ' "Accept both: keeps the id-less answer, so these two rare wrong-id gaps stay; your pick here becomes my owner ruling, no code change."'
)
SEARCH_ASKED = "How should plat's search stop failing journal reads when one page fetch takes over 30 seconds?"
SEARCH_ASK = f"""ASK {SEARCH_ASKED}
Every "disk I/O error" behind the search worker's journal monitor is a page fetch the replicator left unanswered for 30 seconds, since lazy journal copies shipped.
Meanwhile chat pages are unaffected; search misses those chats' newest turns.
- Fetch before reading: search lands a journal's pages first, so a slow fetch delays indexing, never fails it (recommended)
  Before chat and run search read a journal, the Go reader asks the replicator to land the pages that range needs through its existing fill path and waits outside SQLite under the indexer's own budget, so SQLite reads only pages on disk. It touches chatsearch/journal.go, runsearch, sandsql/local/followed.go and the fill in lanes.rs, and indexing downloads each range before reading it.
- Parallel fetches: the replicator answers each reader fetch on its own, so one slow fetch stalls no other
  The replicator's fault server in lanes.rs runs each reader fetch as its own task, where today each copy has one reader fetch in flight on one shared task, and page landing moves off that task; the 30-second deadline stays. It touches lanes.rs and its tests, adds parallel bucket reads per host, and rests on queueing the logs do not prove.
- No deadline: a read waits for the replicator's own answer instead of 30 seconds
  The journal's file layer in sandsql-replica's disk.rs loses FAULT_TIMEOUT and its socket timeouts, so a view fails only when the replicator answers 503 or the copy is lost. It is one file, but a stalled replicator would hold journal read threads with no end, and his Nov 10 ruling calls a timer change standing in for a cause a hack.
Findings:
Holds: none: the order is a read-only diagnosis.
Root cause: a search read of a lazily restored copy fetches each absent segment inside SQLite under a fixed 30 s socket deadline.
First principles: Fetch before reading
Simplest: Fetch before reading
Interim fix: none: chat pages are not on this path.
"""
SEARCH_CARD = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794534105413699"
SEARCH_PICK = {"id": "38dfc32057e99cd970064f05adf6317042e9d7f1", "title": SEARCH_ASKED, "body": "1+2+fiure out why its taking more than 30 seconds thats the real bug", "tags": [], "created_at": "2026-11-13T01:47:14Z"}
ONE_PATH = {
    "id": "cb82cc364b6789445c52d5b5e8b484b3e9d245d7",
    "title": "Once followed copies are lazy, what keeps a host's unheld journal copies from filling its disk, as on the search worker's 8Gi cache disk?",
    "body": "1 and we can unify all logic on that right? Not keep all these random other code paths?\nNotes: his own words on the card",
    "tags": ["scope:durable"],
    "created_at": "2026-11-11T21:03:15Z",
}
LAZY_SLICES = {
    "id": "af02eaf9c4274a2a5a84fe7aa800c9fc76be758b",
    "title": "Search indexing, best effort: how should search text reach the indexer?",
    "body": "Journal, lazy slices: on each ping the worker mounts the journal lazily at that position and reads 200 entries past its cursor; nothing new.",
    "tags": ["scope:durable"],
    "created_at": "2026-11-11T21:18:07Z",
}
SEARCH_OPTIONS = f'the options his card "{SEARCH_ASKED}" offered, numbered as it drew them: {"; ".join(limits.offered(limits.redrawn(SEARCH_ASK)))}'
SEARCH_NEAR = f', ranked near the words of his card "{SEARCH_ASKED}"'
REPLAYS = {
    "Forge-AI/monorepo#54664": (
        {
            "created": "2026-11-10T19:40:59-08:00",
            "text": "also this should be built on top of both auto-splitting tbales from the DB when too large (is that live and working yhet?) and auto-paritioning big"
            " tables (you were suppsoed to get me some desing options for thart, iut shoiujld lok at usage patterns to decide when it croses the threshold should it"
            " be time or hash based partition, etc)",
        },
        [
            "sandsql: new row ids are UUIDv7s minted by the client. Waits on nothing; can sta",
            "sandsql: reads and writes reach a table with several parts (three placement colu",
            "sandsql: a table past 1.7 GB is partitioned on its own (roll-over, carve, rule, ",
        ],
        [GAPS],
        [],
        "identity.rs",
    ),
    "Forge-AI/monorepo#54901": (
        {
            "created": "2026-11-12T06:34:37-08:00",
            "text": "can we keep them as uuidv7 but just format them in the same 32 hex way without dashes so they are the same format? incident urgency, if so and wr dont"
            " loose anytjing make that change asap (and anyhtign that relies on it) and ,let himknow",
        },
        ["fix"],
        [GAPS],
        [],
        None,
    ),
    "Forge-AI/monorepo#54863": (
        {"created": "2026-11-12T04:33:42-08:00", "text": "why did it lag? we need to fix that"},
        ["Spread rewrites: the new primary rewrites a few teams per minute so the replica keeps pace"],
        [
            'ab71450, 2026-11-12T05:20:46-08:00, asked "How should a SandSQL release keep the replica caught up when a new primary rewrites every database?": "1+3"',
        ],
        [],
        None,
    ),
    "Forge-AI/monorepo#55465": (
        {
            "created": "2026-11-12T16:50:29-08:00",
            "text": "https://poetic-technology.slack.com/archives/C0822AHFY3G/p1794531029122869 Datadog monitor 337919227"
            ' "SandSQL journal failing to show cloud chats — prod,request request on plat" opened at warn',
        },
        ["option 1: search lands a journal's pages before reading"],
        [served.recorded(SEARCH_PICK)[1], SEARCH_OPTIONS],
        [line for _, line in sorted([served.recorded(ONE_PATH, SEARCH_NEAR), served.recorded(LAZY_SLICES, SEARCH_NEAR)])],
        "followed_reads.go",
    ),
}


@pytest.mark.skipif(os.environ.get("DRIVE_REAL_JUDGE") != "1", reason="replays four merged PRs, read from GitHub, on the judge itself, which DRIVE_REAL_JUDGE=1 asks for")
@pytest.mark.parametrize("key", list(REPLAYS))
def test_the_judge_refuses_dashed_uuidv7_ids_and_a_picked_option_built_as_whole_copies_and_passes_the_32_hex_fix_and_a_fix_his_words_need(key: str, demo: str) -> None:
    order, parts, answered, near, refused_in = REPLAYS[key]
    said = served.words({"orders": {"o1": order | {"parts": {part: {"posted": None} for part in parts}}}, "jobs": {"j1": {"order": "o1", "parts": parts, "carries": {}, "prs": []}}}, [{"job": "j1", "parts": []}], ["o1"], answered, near, [], [])
    found = JUDGE(demo, said, {key: READ_CHANGE(key)})
    assert any(refused_in in item["change"] for item in found) if refused_in else found == [], found


TEAMMATES_FIX = "https://github.com/Forge-AI/monorepo/pull/56212"
REGRESSION_TEST = "https://github.com/Forge-AI/monorepo/pull/56227"


@pytest.mark.parametrize(
    ("job_prs", "lines"),
    [
        ([REGRESSION_TEST], f"  other changes deliver: fix, which {TEAMMATES_FIX} merged; stop; cause"),
        ([TEAMMATES_FIX, REGRESSION_TEST], "  this change delivers: fix\n  other changes deliver: stop; cause"),
    ],
)
def test_a_part_a_merged_pr_outside_the_job_delivered_is_left_to_other_changes_naming_that_pr_and_one_its_own_pr_delivered_stays_its_own(job_prs: list[str], lines: str) -> None:
    order = {"created": "2026-11-13T18:40:00-08:00", "text": "fix the stuck chats", "parts": {"fix": {"posted": TEAMMATES_FIX}, "stop": {"posted": None}, "cause": {"posted": None}}}
    said = served.words({"orders": {"o1": order}, "jobs": {"j1": {"order": "o1", "parts": ["fix"], "carries": {}, "prs": job_prs}}}, [{"job": "j1", "parts": []}], ["o1"], [], [], [], [])
    assert said == f'o1, 2026-11-13T18:40:00-08:00: "fix the stuck chats"\n{lines}'


STATUS = (
    b"diff --git a/go/sand/cmd/iris_serve_session.go b/go/sand/cmd/iris_serve_session.go\nindex 1111111111aa..2222222222bb 100644\n"
    b"--- a/go/sand/cmd/iris_serve_session.go\n+++ b/go/sand/cmd/iris_serve_session.go\n@@ -1884,3 +1884,3 @@ func (s *session) statusLocked() string {\n"
    b"-\tif s.lease != nil {\n+\tif s.lease != nil && !s.hub.finished() {\n \t\treturn statusRunning\n"
)


def test_a_flake_fix_at_a_product_code_cause_is_judged_against_his_ruling_alone_and_keeps_his_tap_until_only_tests_change(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, judge: Judge, recorded: Answers
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "open a side chat beside the session")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    recorded.shown[state.FLAKE_RULING] = {"id": f"{state.FLAKE_RULING}314b29e4dd77f018524c99590519c91e7", "title": "Flaky tests in CI: retry, skip, or fix?", "body": state.FLAKE_WORDS, "tags": [], "created_at": "2026-11-04T06:04:01Z"}
    files = ["go/sand/cmd/iris_serve_session.go", "dashboard/e2e/irisServe.e2e.ts"]
    monkeypatch.setattr(github, "files", lambda key: files)
    assert run("pr", "add", URL, "--job", "j1", "--exempt", "flake fix", *LEAD)[0] == 0
    data = state.load(demo)
    pr = data["prs"][KEY]
    assert (pr["exempt"], pr["wants_tap"], stage.carded(pr), state.cleared(pr), state.served_orders(data, pr)) == ("flake fix", True, True, False, [])
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head1"
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: a chat reads idle once its run ends, so irisServe.e2e.ts stops flaking", "body": "", "head": "head1", "diff": STATUS})
    assert served.verdict(demo, KEY).startswith(f"{served.CHECKED} against his ruling {state.FLAKE_RULING} at head1")
    said = judge.asked[-1][0]
    assert state.FLAKE_WORDS in said and "side chat" not in said
    files[:] = ["dashboard/e2e/irisServe.e2e.ts"]
    assert run("pr", "add", URL, "--job", "j1", "--exempt", "flake fix")[0] == 0
    pr = state.load(demo)["prs"][KEY]
    assert (pr["wants_tap"], stage.carded(pr), state.cleared(pr), state.exempted(pr)) == (False, False, True, "flake fix")


def test_a_not_ready_line_carries_each_answer_his_design_line_cites_whole_for_its_builder_and_his_card_shows_the_objection_alone(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, judge: Judge, recorded: Answers
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "time each handoff")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    monkeypatch.setattr(github, "files", lambda key: ["api/run.ts"])
    assert run("pr", "add", URL, "--job", "j1", *LEAD)[0] == 0
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head1"
        state.cite(data["prs"][KEY], "DESIGN: the handoff metric stays, by {answer:11f8cf2}")
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: time each handoff", "body": "", "head": "head1", "diff": SMALL})
    recorded.shown["11f8cf2"] = {"id": "11f8cf2aa", "title": "Keep the handoff metric?", "body": "Land both", "tags": [], "created_at": "2026-11-13T11:07:37Z"}
    judge.against = [{"order": "o1", "words": "time each handoff", "file": "api/run.ts", "change": "api/run.ts drops the handoff metric"}]
    named = 'o1 "time each handoff": api/run.ts drops the handoff metric'
    outcome = served.verdict(demo, KEY)
    assert outcome.startswith(
        f'{served.NOT_READY}: {KEY} go against his words, judged from each PR\'s title and diff: {named}. His answers its design cites, and each other one an objection names, each whole:'
        f' 11f8cf2, {at("2026-11-13T11:07:37Z")}, asked "Keep the handoff metric?": "Land both". Reconcile each objection with his answers and your own read of the code'
    ), outcome
    assert state.against(state.load(demo)["prs"][KEY]) == named


def test_a_not_ready_line_leaves_out_each_answer_his_brief_binds_that_no_objection_names_and_carries_the_one_an_objection_names_whole(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch, judge: Judge, recorded: Answers
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "time each handoff")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    monkeypatch.setattr(github, "files", lambda key: ["api/run.ts"])
    assert run("pr", "add", URL, "--job", "j1", *LEAD)[0] == 0
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head1"
        data["jobs"]["j1"]["brief_flags"] = {"ruling": ["a11c0de", "b22c0de"]}
    recorded.shown |= {
        "a11c0de": {"id": "a11c0de" + "0" * 33, "title": "One code path for handoffs?", "body": "one path, never two", "tags": ["scope:durable"], "created_at": "2026-11-10T09:00:00Z"},
        "b22c0de": {"id": "b22c0de" + "0" * 33, "title": "Which chart for the dashboard?", "body": "bar charts only", "tags": ["scope:durable"], "created_at": "2026-11-09T09:00:00Z"},
    }
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: time each handoff", "body": "", "head": "head1", "diff": SMALL})
    judge.against = [{"order": "a11c0de", "words": "one path, never two", "file": "api/run.ts", "change": "api/run.ts adds a second handoff path"}]
    outcome = served.verdict(demo, KEY)
    assert "bar charts only" in judge.asked[-1][0] and outcome.startswith(
        f'{served.NOT_READY}: {KEY} go against his words, judged from each PR\'s title and diff: a11c0de "one path, never two": api/run.ts adds a second handoff path.'
        f' His answers its design cites, and each other one an objection names, each whole: a11c0de, {at("2026-11-10T09:00:00Z")}, asked "One code path for handoffs?": "one path, never two".'
    ), outcome
    assert "bar charts only" not in outcome


FLAKE_RULING = (
    '532cd3d, 2026-11-03T22:04:01-08:00, asked "Flaky tests in CI: retry, skip, or fix?": "Owner, Nov 3 2026 10:02 PM, verbatim: "and ffs fix these flaky tgests'
    ' how many times do I have to tell yoiu: https://poetic-technology.slack.com/archives/C0B75AL4XEW/p1793772091923669?thread_ts=1793772079.138749&cid=C0B75AL4XEW".'
    " Ruling, repeated: a flaky test is fixed at its cause the day it flakes; never a retry, a skip, a timeout bump or a rerun. A repeat of this order is a defect"
    ' report: the lane finds where the earlier orders failed to hold."'
)
RETRY = (
    b"diff --git a/dashboard/e2e/irisServe.e2e.ts b/dashboard/e2e/irisServe.e2e.ts\nindex 1111111111aa..2222222222bb 100644\n"
    b"--- a/dashboard/e2e/irisServe.e2e.ts\n+++ b/dashboard/e2e/irisServe.e2e.ts\n@@ -12,6 +12,8 @@ import { serve } from './irisServe';\n"
    b" \n test.describe('sand iris serve', () => {\n+  test.describe.configure({ retries: 3 });\n+\n   test('opens a side chat', async ({ page }) => {\n"
    b"-    await page.getByRole('button', { name: 'Add to this chat' }).click();\n+    await page.getByRole('button', { name: 'Add to this chat' }).click({ timeout: 60_000 });\n   });\n });\n"
)
FLAKE_FIXES = {
    "Forge-AI/monorepo#54983, closed unmerged as BLOCKED no order of his names this": ("Forge-AI/monorepo#54983", None),
    "Forge-AI/monorepo#55393, closed unmerged to answer a NOT READY": ("Forge-AI/monorepo#55393", None),
    "Forge-AI/monorepo#55619, judged overreach against its feature order": ("Forge-AI/monorepo#55619", None),
    "a retry and a timeout bump": ("Forge-AI/monorepo#1", {"title": "Fix: the side chat test in dashboard/e2e/irisServe.e2e.ts stops flaking", "diff": RETRY}),
}


@pytest.mark.skipif(os.environ.get("DRIVE_REAL_JUDGE") != "1", reason="replays three flake fixes, read from GitHub, on the judge itself, which DRIVE_REAL_JUDGE=1 asks for")
@pytest.mark.parametrize("case", list(FLAKE_FIXES))
def test_the_judge_passes_each_flake_fix_the_drive_turned_away_against_his_ruling_532cd3d_alone_and_refuses_a_retry_and_a_timeout_bump(case: str, demo: str) -> None:
    key, read = FLAKE_FIXES[case]
    said = served.words({"orders": {}, "jobs": {"j1": {"order": "o1", "parts": [], "carries": {}, "prs": []}}}, [{"job": "j1", "parts": []}], [], [FLAKE_RULING], [], [], [])
    found = JUDGE(demo, said, {key: read or READ_CHANGE(key)})
    assert {item["file"] for item in found} == {"dashboard/e2e/irisServe.e2e.ts"} if read else found == [], found


PICK = "demo:j2274-679400"
WHOLE_STACK = "https://acme.slack.com/archives/D123/p1794523024098799"
STACK_UNDER = "https://acme.slack.com/archives/D123/p1794520332007829"
WHOLE_STACK_WORDS = "Land whole stacks and read the gate: no schema change merges without its writer; a stack's bottom waits for its top"
ASKED = "What must a drive release prove before production, now that each is the regular release and never cancelled?"
RECORDS = {
    "679494": {"kind": "answer", "text": "Land it with the stack under it", "refs": {"url": STACK_UNDER}, "at": "2026-11-12T13:52:40-08:00"},
    "679500": {"kind": "answer", "text": WHOLE_STACK_WORDS, "refs": {"url": WHOLE_STACK}, "at": "2026-11-12T14:42:20-08:00"},
    "679510": {"kind": "measure", "text": "37 of 91 DESIGN: lines give the check no answer", "refs": {}, "at": "2026-11-12T15:00:00-08:00"},
}


def drive_holding(closed: dict | None, card: str | None = WHOLE_STACK) -> dict:
    question = state.new_question(None, ASKED, "2026-11-12T14:37:04-08:00", card=card, closed=closed) | {"waits": PICK}
    return {"name": "demo", "roles": {"coordinator": {"cwd": "/tmp"}}, "jobs": {"j2274": {"questions": [question]}}, "answers": {}}


@pytest.fixture
def slack_and_cci(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    read: list[str] = []
    monkeypatch.setattr(refs, "shown", lambda name, kind, seq: read.append(seq) or RECORDS[seq])
    monkeypatch.setattr(settled, "heard", lambda link, found, data: read.append(link) or "his")
    monkeypatch.setattr(slack, "message", lambda link: {"text": "Land it with the stack under it", "ts": f"{link[-16:-6]}.{link[-6:]}"})
    return read


def at(stamp: str) -> str:
    return state.parse_time(stamp).astimezone().isoformat(timespec="seconds")


def test_a_design_line_reads_his_whole_stack_pick_by_its_waits_id_and_each_answer_once_however_many_ways_it_is_named(slack_and_cci: list[str]) -> None:
    line = f"DESIGN: landing the stack whole, by his answer {{at:679494}} at {STACK_UNDER} and his pick {{at:679500}} on card {PICK}, measured in {{record:679510}}"
    closed = state.new_close("answered", WHOLE_STACK_WORDS, WHOLE_STACK, seq="679500", at="2026-11-12T14:42:12-08:00")
    assert served.answers(drive_holding(closed), [line], None)["cited"] == [
        f'{STACK_UNDER}, {slack.sent_at({"ts": "1794520332.007829"}).isoformat(timespec="seconds")}: "Land it with the stack under it"',
        f'{PICK}, {at("2026-11-12T14:42:12-08:00")}, asked "{ASKED}": "{WHOLE_STACK_WORDS}"',
    ]
    assert slack_and_cci == [STACK_UNDER, "679494", "679500", "679510"]


def test_a_braced_answer_id_reads_as_cc_notes_keeps_it_and_a_card_answered_with_no_words_kept_reads_as_its_permalink(slack_and_cci: list[str]) -> None:
    assert served.named("asked:{answer:3da552e}; {card:demo:j2274-679400|the stack card}") == [("answer", "3da552e"), ("card", PICK)]
    assert served.answers(drive_holding(None), ["by {answer:3da552e}"], None) == {"cited": [f'3da552e, {at("2026-11-12T11:33:00Z")}, asked "": ""'], "near": []}
    closed = state.new_close("answered", None, None, at="2026-11-12T14:42:12-08:00")
    assert served.answers(drive_holding(closed), [f"by {PICK}"], None)["cited"] == [
        f'{WHOLE_STACK}, {slack.sent_at({"ts": "1794523024.098799"}).isoformat(timespec="seconds")}: "Land it with the stack under it"'
    ]


def test_his_tap_recorded_hours_late_reads_before_his_words_typed_after_it_whether_its_drive_answer_or_its_record_is_cited(
    monkeypatch: pytest.MonkeyPatch, slack_and_cci: list[str], recorded: Answers
) -> None:
    tapped = "2026-11-12T14:42:12-08:00"
    tap_answer = {"id": "e614876a366cabeb211e9b1bd6c1612ead1f8f4b", "title": ASKED, "body": WHOLE_STACK_WORDS, "tags": [], "created_at": "2026-11-13T01:18:48Z"}
    typed = {"id": "bf042d6" + "0" * 33, "title": ASKED, "body": "the only one that lands whole is the schema stack", "tags": [], "created_at": "2026-11-12T22:50:00Z"}
    recorded.shown |= {"e614876": tap_answer, "bf042d6": typed}
    monkeypatch.setitem(RECORDS, "699068", {"kind": "answer", "text": WHOLE_STACK_WORDS, "refs": {"url": WHOLE_STACK}, "at": "2026-11-12T17:15:38-08:00"})
    data = drive_holding(state.new_close("answered", WHOLE_STACK_WORDS, WHOLE_STACK, seq="699068", at=tapped))
    data["answers"][tap_answer["id"]] = {"words": WHOLE_STACK_WORDS, "link": WHOLE_STACK, "at": "2026-11-12T17:18:48-08:00", "said": None, "cards": {}, "jobs": {}}
    assert served.answers(data, ["DESIGN: the stack lands whole, by {answer:bf042d6}, {answer:e614876} and {record:699068}"], None)["cited"] == [
        f'e614876, {at(tapped)}, asked "{ASKED}": "{WHOLE_STACK_WORDS}"',
        f'{WHOLE_STACK}, {at(tapped)}: "{WHOLE_STACK_WORDS}"',
        f'bf042d6, {at(typed["created_at"])}, asked "{ASKED}": "{typed["body"]}"',
    ]


@pytest.mark.parametrize(
    ("closed", "refused"),
    [
        (None, "its card still asks him"),
        (state.new_close("withdrawn", "not now", None), "its card was withdrawn"),
        (state.new_close("superseded", None, None), "its card was superseded"),
    ],
)
def test_a_waits_id_of_a_card_he_has_not_answered_is_no_answer_of_his(slack_and_cci: list[str], closed: dict | None, refused: str) -> None:
    with pytest.raises(DriveError, match=f"^{PICK} is not his answer: {refused}; "):
        served.answers(drive_holding(closed), [f"by {PICK}"], None)
    with pytest.raises(DriveError, match="^demo:j9-1 is not his answer: it names no card the drive asked him; "):
        served.answers(drive_holding(closed), ["by demo:j9-1"], None)


def test_words_naming_no_answer_of_his_name_none() -> None:
    assert served.named("asked:Also start stopped boxes, as built (his pick, 3:24 AM Nov 10)") == []


@pytest.mark.parametrize(
    ("closed", "design", "cited"),
    [
        pytest.param(
            {"answer": "38dfc32"},
            f"DESIGN: answer 38dfc32 (card {SEARCH_CARD})",
            [f'38dfc32, {at(SEARCH_PICK["created_at"])}, asked "{SEARCH_ASKED}": "{SEARCH_PICK["body"]}"'],
            id="his answer cc-notes recorded for the card",
        ),
        pytest.param({"noted": "38dfc32"}, f"DESIGN: card {SEARCH_CARD}", [], id="his words on the card the drive noted as a cc-notes answer"),
    ],
)
def test_a_cited_card_answer_reads_with_the_options_its_card_offered_and_apart_from_them_the_answers_cc_notes_ranks_near_its_words_never_his_own_answer_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack_and_cci: list[str], recorded: Answers, closed: dict, design: str, cited: list[str]
) -> None:
    ask = tmp_path / "j2513-dcd8e53f.md"
    ask.write_text(SEARCH_ASK)
    question = state.new_question(str(ask), SEARCH_ASKED, "2026-11-13T01:21:00Z", card=SEARCH_CARD, closed=state.new_close("answered", SEARCH_PICK["body"], SEARCH_CARD, at="2026-11-13T01:47:14Z", **closed)) | {
        "waits": "demo:j2513-1794535260000"
    }
    data = {"name": "demo", "roles": {"coordinator": {"cwd": "/tmp"}}, "jobs": {"j2513": {"questions": [question]}}, "answers": {}}
    recorded.shown |= {answer["id"][:7]: answer for answer in (SEARCH_PICK, ONE_PATH, LAZY_SLICES)}
    monkeypatch.setattr(settled, "matched", lambda texts, data: [{"id": answer["id"][:7]} for answer in (SEARCH_PICK, ONE_PATH, LAZY_SLICES)] if any("land the pages that range needs" in text for text in texts) else [])
    assert served.answers(data, [design], None) == {
        "cited": [f'{SEARCH_CARD}, {slack.sent_at({"ts": "1794534105.413699"}).isoformat(timespec="seconds")}: "Land it with the stack under it"', *cited, SEARCH_OPTIONS],
        "near": [
            f'cb82cc3, {at(ONE_PATH["created_at"])}{SEARCH_NEAR}, asked "{ONE_PATH["title"]}": "1 and we can unify all logic on that right? Not keep all these random other code paths?"',
            f'af02eaf, {at(LAZY_SLICES["created_at"])}{SEARCH_NEAR}, asked "{LAZY_SLICES["title"]}": "{LAZY_SLICES["body"]}"',
        ],
    }
    assert SEARCH_OPTIONS.count("Option ") == 3 and "Option 1 (recommended): Fetch before reading" in SEARCH_OPTIONS and "land the pages that range needs" in SEARCH_OPTIONS
