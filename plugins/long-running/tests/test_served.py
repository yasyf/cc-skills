import json
import os
import subprocess
from collections.abc import Callable

import pytest
from conftest import LEAD, Answers, Judge
from drive import github, refs, served, settled, slack, stage, state
from drive.state import DriveError

KEY = "Forge-AI/monorepo#42"
URL = "https://github.com/Forge-AI/monorepo/pull/42"
READ_CHANGE = served.change
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
        out = json.dumps({"title": "Cleanup: move the fixtures", "body": "", "baseRefOid": "base1", "headRefOid": "head1"}).encode() if "view" in cmd else MOVE
        return subprocess.CompletedProcess(cmd, 0, out.decode() if text else out, "" if text else b"")

    monkeypatch.setattr(github.subprocess, "run", gh)
    read = READ_CHANGE(KEY)
    assert (read["head"], read["diff"], len(served.files(read["diff"]))) == ("head1", MOVE, 1_571)
    assert "repos/Forge-AI/monorepo/compare/base1...head1" in calls[1]


def test_the_judge_sees_source_before_fixtures_a_byte_utf8_cannot_read_and_a_capped_count_of_the_files_left_out() -> None:
    prompt = served.shown({KEY: {"title": "Cleanup: move the fixtures", "diff": MOVE}})
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
    stamp = {"head": "head1", "read": "head1", "orders": ["o1"], "lines": served.digests(SMALL)}
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


STORED_TAPS = {
    "before patch was a field": {"label": "Approve", "at": "2026-11-12T16:11:40-08:00", "head": "head0", "source": "pressed the button"},
    "a card press kept a patch": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "patch": "p0"},
    "a later head read beside the patch": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "patch": "p0", "read": "head1"},
    "adopted from its stack": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "via": "Forge-AI/monorepo#41"},
}


def moved_check(run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1", *LEAD)
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head2", "diff": SMALL})
    return {"head": "head2", "checked": {"head": "head1", "read": "head1", "orders": ["o1"], "lines": served.digests(SMALL)}}


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
    loaded = {"label": "Approve", "at": tap["at"], "head": "head0", "source": "pressed the button", "action_ts": tap.get("action_ts"), "via": tap.get("via")}
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
    assert (pr["checked"]["head"], pr["tap"] | {"at": ""}) == ("head2", {"label": "Approve", "at": "", "head": "head1", "source": link, "action_ts": None, "via": None})


GAPS = (
    'b3cbd28, 2026-11-12T02:15:56-08:00, asked "Do you accept the two wrong-id gaps the reviewer bot is holding the UUIDv7 row ids change on?":'
    ' "Accept both: keeps the id-less answer, so these two rare wrong-id gaps stay; your pick here becomes my owner ruling, no code change."'
)
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
        None,
    ),
    "Forge-AI/monorepo#54863": (
        {"created": "2026-11-12T04:33:42-08:00", "text": "why did it lag? we need to fix that"},
        ["Spread rewrites: the new primary rewrites a few teams per minute so the replica keeps pace"],
        [
            'ab71450, 2026-11-12T05:20:46-08:00, asked "How should a SandSQL release keep the replica caught up when a new primary rewrites every database?": "1+3"',
        ],
        None,
    ),
}


@pytest.mark.skipif(os.environ.get("DRIVE_REAL_JUDGE") != "1", reason="replays three merged PRs, read from GitHub, on the judge itself, which DRIVE_REAL_JUDGE=1 asks for")
@pytest.mark.parametrize("key", list(REPLAYS))
def test_the_judge_refuses_uuidv7_row_ids_printed_dashed_where_32_hex_stood_and_passes_the_32_hex_fix_and_a_fix_his_words_need(key: str, demo: str) -> None:
    order, parts, answered, refused_in = REPLAYS[key]
    said = served.words({"orders": {"o1": order | {"parts": dict.fromkeys(parts)}}}, {"order": "o1", "parts": parts, "carries": {}}, ["o1"], [], answered, [])
    found = JUDGE(demo, said, {key: READ_CHANGE(key)})
    assert any(refused_in in item["change"] for item in found) if refused_in else found == [], found


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
    assert run("pr", "add", URL, "--job", "j1")[0] == 0
    with state.edit(demo) as data:
        data["prs"][KEY]["head"] = "head1"
        state.cite(data["prs"][KEY], "DESIGN: the handoff metric stays, by {answer:11f8cf2}")
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: time each handoff", "body": "", "head": "head1", "diff": SMALL})
    recorded.shown["11f8cf2"] = {"id": "11f8cf2aa", "title": "Keep the handoff metric?", "body": "Land both", "tags": [], "created_at": "2026-11-13T11:07:37Z"}
    judge.against = [{"order": "o1", "words": "time each handoff", "file": "api/run.ts", "change": "api/run.ts drops the handoff metric"}]
    named = 'o1 "time each handoff": api/run.ts drops the handoff metric'
    outcome = served.verdict(demo, KEY)
    assert outcome.startswith(
        f'{served.NOT_READY}: {KEY} go against his words, judged from each PR\'s title and diff: {named}. His answers the check read, each whole:'
        f' 11f8cf2, {at("2026-11-13T11:07:37Z")}, asked "Keep the handoff metric?": "Land both". Reconcile each objection with his answers and your own read of the code'
    ), outcome
    assert state.against(state.load(demo)["prs"][KEY]) == named


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
    said = served.words({"orders": {}}, {"order": "o1", "parts": [], "carries": {}}, [], [], [FLAKE_RULING], [])
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
    return {"name": "demo", "roles": {"coordinator": {"cwd": "/tmp"}}, "jobs": {"j2274": {"questions": [question]}}}


@pytest.fixture
def slack_and_cci(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    read: list[str] = []
    monkeypatch.setattr(refs, "shown", lambda name, kind, seq: read.append(seq) or RECORDS[seq])
    monkeypatch.setattr(settled, "heard", lambda link, found: read.append(link) or "his")
    monkeypatch.setattr(slack, "message", lambda link: {"text": "Land it with the stack under it", "ts": f"{link[-16:-6]}.{link[-6:]}"})
    return read


def at(stamp: str) -> str:
    return state.parse_time(stamp).astimezone().isoformat(timespec="seconds")


def test_a_design_line_reads_his_whole_stack_pick_by_its_waits_id_and_each_answer_once_however_many_ways_it_is_named(slack_and_cci: list[str]) -> None:
    line = f"DESIGN: landing the stack whole, by his answer {{at:679494}} at {STACK_UNDER} and his pick {{at:679500}} on card {PICK}, measured in {{record:679510}}"
    closed = state.new_close("answered", WHOLE_STACK_WORDS, WHOLE_STACK, seq="679500", at="2026-11-12T14:42:12-08:00")
    assert served.answers(drive_holding(closed), [line], None) == [
        f'{STACK_UNDER}, {slack.sent_at({"ts": "1794520332.007829"}).isoformat(timespec="seconds")}: "Land it with the stack under it"',
        f'{PICK}, {at("2026-11-12T14:42:12-08:00")}, asked "{ASKED}": "{WHOLE_STACK_WORDS}"',
    ]
    assert slack_and_cci == [STACK_UNDER, "679494", "679500", "679510"]


def test_a_braced_answer_id_reads_as_cc_notes_keeps_it_and_a_card_answered_with_no_words_kept_reads_as_its_permalink(slack_and_cci: list[str]) -> None:
    assert served.named("asked:{answer:3da552e}; {card:demo:j2274-679400|the stack card}") == [("answer", "3da552e"), ("card", PICK)]
    assert served.answers(drive_holding(None), ["by {answer:3da552e}"], None) == [f'3da552e, {at("2026-11-12T11:33:00Z")}, asked "": ""']
    closed = state.new_close("answered", None, None, at="2026-11-12T14:42:12-08:00")
    assert served.answers(drive_holding(closed), [f"by {PICK}"], None) == [
        f'{WHOLE_STACK}, {slack.sent_at({"ts": "1794523024.098799"}).isoformat(timespec="seconds")}: "Land it with the stack under it"'
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
