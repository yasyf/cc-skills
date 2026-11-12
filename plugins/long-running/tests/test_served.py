import json
import subprocess
from collections.abc import Callable

import pytest
from drive import github, served, state

KEY = "Forge-AI/monorepo#42"
URL = "https://github.com/Forge-AI/monorepo/pull/42"
READ_CHANGE = served.change
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
