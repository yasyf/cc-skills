import json
import os
import subprocess
from collections.abc import Callable

import pytest
from drive import github, served, state

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


def test_a_restack_carries_a_check_kept_on_the_text_of_gh_pr_diff_to_the_compare_bytes_of_the_same_change(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1")
    kept = subprocess.run(["git", "patch-id", "--stable"], input=SMALL.decode(), capture_output=True, text=True, check=True).stdout.split(" ")[0]
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "head2", "checked": {"head": "head1", "orders": ["o1"], "patch": kept}}
    restacked = SMALL.replace(b"@@ -10,3 +10,3 @@", b"@@ -14,3 +14,3 @@").replace(b"1111111111aa", b"3333333333cc")
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head2", "diff": restacked})
    assert served.carry(demo, [KEY]) == [KEY]
    assert state.load(demo)["prs"][KEY]["checked"] == {"head": "head2", "orders": ["o1"], "patch": kept}
    with state.edit(demo) as data:
        data["prs"][KEY] |= {"head": "head3"}
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head3", "diff": SMALL.replace(b"+  return !flag;", b"+  return flag && !staff();")})
    assert served.carry(demo, [KEY]) == []
    assert state.load(demo)["prs"][KEY]["checked"] is None


STORED_TAPS = {
    "before patch was a field": {"label": "Approve", "at": "2026-11-12T16:11:40-08:00", "head": "head0", "source": "pressed the button"},
    "a card press kept a patch": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "patch": "p0"},
    "a later head read beside the patch": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "patch": "p0", "read": "head1"},
    "adopted from its stack": {"label": "Approve", "at": "2026-11-12T18:30:00-08:00", "head": "head0", "source": "pressed the button", "action_ts": "1.2", "via": "Forge-AI/monorepo#41"},
}


def moved_check(run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "land it")
    run("job", "add", "builder", "--order", "o1", "--goal", "land")
    run("pr", "add", URL, "--job", "j1")
    kept = subprocess.run(["git", "patch-id", "--stable"], input=SMALL.decode(), capture_output=True, text=True, check=True).stdout.split(" ")[0]
    monkeypatch.setattr(served, "change", lambda key: {"title": "Fix: run", "head": "head2", "diff": SMALL})
    return {"head": "head2", "checked": {"head": "head1", "orders": ["o1"], "patch": kept}}


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
