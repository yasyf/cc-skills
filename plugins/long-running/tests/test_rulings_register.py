from __future__ import annotations

import json
from pathlib import Path

import ledger
import pytest
import rulings

HEAD = "abc1234def5678"
OTHER = "fff9999000aaaa"
REPO = "/repo"


def row(key: str, **fields: str) -> dict:
    return {"key": key, "fields": fields}


def answer(id7: str, title: str, body: str, *tags: str) -> dict:
    return {"id": id7 + "0" * 33, "title": title, "body": body, "tags": list(tags)}


ROWS = [
    row("design/oct21", kind="design", capture="a0d2100", words="making all these error classes impossible"),
    row("scope/read-only", kind="scope", capture="a0d2100", words="start a new session this one is read only", paths="iris-desktop/src/readonly/"),
    row("reserved/old-cloud-agents", kind="reserved", capture="002a374", words="the 37 old-format cloud agents", ops="deleteOldCloudAgents"),
    row("reserved/user-data", kind="reserved", capture="36a298d", words="deletes of user data", ops="DeleteObject\n^Revert "),
    row("pick/fork", kind="pick", capture="2143044", words="A fork copies nothing", steps="4", paths="go/sand/internal/fork/*", tests="go/sand/internal/fork/fork_test.go::TestForkCopiesNothing"),
    row("order/merge-card", kind="order", capture="3f34533", words="nothing merges without it", steps="1.3", lanes="landing-desk", paths="plugins/long-running/bin/desk-runner.py", status="broken"),
    row("pick/journal", kind="pick", capture="8aeaaff", words="the journal crate", steps="3", paths="rust/crates/journal/", status="contested"),
    row("r2-d2-approval", kind="pick", capture="d5f9c22", words="No old cloud chat is deleted until a later card", steps="10", paths="api/src/jobs/oldCloudChats.ts", status="superseded"),
    row("capture/c0db6b7", capture="c0db6b7", words="a week on internal Macs"),
]
ANSWERS = [
    answer("a0d2100", "Design rule (Oct 21)", 'making all these error   classes impossible, (just say "start a new session this one is read only")'),
    answer("002a374", "Delete the old cloud agents?", "Delete the 37 old-format cloud agents and the conversion pass.", "authorizes:reserved/old-cloud-agents"),
    answer("36a298d", "What is reserved?", "Reserved rows are deletes of user data and reverts or rollbacks."),
    answer("5eed7a9", "merge card tap", "tapped merge", "authorizes:reserved/user-data", f"head:{HEAD[:7]}"),
    answer("c0db6b7", "Testing bar", "CI against the real previous release, a week on internal Macs and a 15-minute run of every fault scenario"),
]


@pytest.fixture
def register(tmp_path: Path) -> Path:
    path = tmp_path / "register.json"
    path.write_text(json.dumps({"rows": ROWS, "answers": ANSWERS}))
    return path


def fields_of(register: Path, key: str) -> dict:
    return next(entry for entry in json.loads(register.read_text())["rows"] if entry["key"] == key)["fields"]


def keys(text: str) -> list[str]:
    return [line.split()[1] for line in text.splitlines() if line.startswith("- ")]


def test_slice_carries_every_global_row_its_step_its_lane_and_the_unclassified_captures(register: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rulings.main(["slice", "--ledger", str(register), "--lane", "fork-lane", "--step", "4.2"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Register slice: lane=fork-lane step=4.2 rev=")
    assert keys(out) == ["design/oct21", "scope/read-only", "reserved/old-cloud-agents", "reserved/user-data", "pick/fork", "capture/c0db6b7"]
    assert "  tests: go/sand/internal/fork/fork_test.go::TestForkCopiesNothing\n" in out
    assert out.endswith("End register slice.\n")


def test_an_incident_slice_carries_only_the_global_rows_and_the_captures(register: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rulings.main(["slice", "--ledger", str(register), "--lane", "inc-fix", "--step", "incident"]) == 0
    assert keys(capsys.readouterr().out) == ["design/oct21", "scope/read-only", "reserved/old-cloud-agents", "reserved/user-data", "capture/c0db6b7"]


def test_a_lane_name_pulls_its_rows_whatever_the_step(register: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rulings.main(["slice", "--ledger", str(register), "--lane", "landing-desk", "--step", "9"]) == 0
    assert "order/merge-card" in keys(capsys.readouterr().out)


def test_refresh_rewrites_the_slice_in_place_and_its_rev_tracks_the_rows(register: Path, tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n\nDo the fork step.\n")
    argv = ["slice", "--ledger", str(register), "--lane", "fork-lane", "--step", "4", "--refresh", str(brief)]
    assert rulings.main(argv) == 0
    first = brief.read_text()
    data = json.loads(register.read_text())
    data["rows"][0]["fields"]["status"] = "broken"
    register.write_text(json.dumps(data))
    assert rulings.main(argv) == 0
    second = brief.read_text()
    assert first.count("Register slice:") == second.count("Register slice:") == 1
    assert second.startswith("# Brief\n\nDo the fork step.\n\nRegister slice: ")
    assert first.split("rev=")[1][:12] != second.split("rev=")[1][:12]
    assert "- design/oct21 [design/broken]" in second


def test_classify_writes_the_kind_and_anchors_when_the_words_are_in_the_capture(register: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rulings.main(["classify", "--ledger", str(register), "capture/c0db6b7", "--kind", "gate", "--block", "GO step 7", "--block", "cut 2"]) == 0
    assert fields_of(register, "capture/c0db6b7") == {"capture": "c0db6b7", "words": "a week on internal Macs", "kind": "gate", "blocks": "GO step 7\ncut 2"}


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["--kind", "pick"], "a pick row needs steps, tests, paths or symbols"),
        (["--kind", "reserved"], "a reserved row needs ops patterns"),
        (["--kind", "gate"], "a gate row needs the GO or cut it blocks"),
        (["--kind", "pick", "--step", "7", "--words", "a month on internal Macs"], "the words are not a substring of capture c0db6b7"),
    ],
)
def test_classify_refuses_a_row_the_graders_could_not_read(register: Path, capsys: pytest.CaptureFixture[str], argv: list[str], reason: str) -> None:
    assert rulings.main(["classify", "--ledger", str(register), "capture/c0db6b7", *argv]) == 1
    assert reason in capsys.readouterr().err
    assert "kind" not in fields_of(register, "capture/c0db6b7")


def test_classify_matches_the_owner_words_across_whitespace(register: Path) -> None:
    assert rulings.main(["classify", "--ledger", str(register), "design/oct21", "--kind", "design", "--words", "error classes\nimpossible"]) == 0


def diff(path: str, added: tuple[str, ...] = (), removed: tuple[str, ...] = (), deleted: bool = False) -> str:
    target = "/dev/null" if deleted else f"b/{path}"
    hunk = [*(f"-{line}" for line in removed), *(f"+{line}" for line in added)]
    return "\n".join([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ {target}", "@@ -1,1 +1,1 @@", *hunk]) + "\n"


def body(*cited: str) -> str:
    return "## Summary\n\nWork.\n\n## Rulings\n\n" + "".join(f"- `{key}`: honored\n" for key in cited) + "\n## Risk profile\n\n- scope/read-only\n"


MAJOR = [{"id": "f1", "severity": "major", "state": "open", "title": "drops a row"}]
FIXTURES = {
    "no-section": ("## Summary\n\nWork.\n", diff("docs/x.md", added=("x",)), [], HEAD, [("RULINGS-UNCITED", "-")]),
    "RULINGS-UNCITED": (body(), diff("iris-desktop/src/readonly/banner.tsx", added=("x",)), [], HEAD, [("RULINGS-UNCITED", "scope/read-only")]),
    "unknown-cite": (body("pick/forks"), diff("docs/x.md", added=("x",)), [], HEAD, [("RULINGS-UNCITED", "pick/forks")]),
    "RESERVED-NO-ANSWER": (body(), diff("go/sand/internal/blob/gc.go", added=("s3.DeleteObject(ctx, key)",)), [], OTHER, [("RESERVED-NO-ANSWER", "reserved/user-data")]),
    "TEST-REMOVED": (body("pick/fork"), diff("go/sand/internal/fork/fork_test.go", removed=("func TestForkCopiesNothing(t *testing.T) {",)), [], HEAD, [("TEST-REMOVED", "pick/fork")]),
    "test-file-deleted": (body("pick/fork"), diff("go/sand/internal/fork/fork_test.go", removed=("package fork",), deleted=True), [], HEAD, [("TEST-REMOVED", "pick/fork")]),
    "HELD-BROKEN": (body("order/merge-card"), diff("plugins/long-running/bin/desk-runner.py", added=("x = 1",)), [], HEAD, [("HELD-BROKEN", "order/merge-card")]),
    "RULING-CONTESTED": (body("pick/journal"), diff("rust/crates/journal/src/lib.rs", added=("fn x() {}",)), [], HEAD, [("RULING-CONTESTED", "pick/journal")]),
    "FINDER-MAJOR": (body(), diff("docs/x.md", added=("x",)), MAJOR, HEAD, [("FINDER-MAJOR", "f1")]),
}


def check(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, patch: str, findings: list[dict], head: str, *extra: str) -> tuple[int, list[tuple[str, str]]]:
    (tmp_path / "body.md").write_text(text)
    (tmp_path / "pr.diff").write_text(patch)
    (tmp_path / "findings.json").write_text(json.dumps(findings))
    argv = ["check-pr", "--ledger", str(register), "--body-file", str(tmp_path / "body.md"), "--diff-file", str(tmp_path / "pr.diff"), "--head", head]
    code = rulings.main([*argv, "--findings", str(tmp_path / "findings.json"), *extra])
    return code, [tuple(line.split("\t")[:2]) for line in capsys.readouterr().out.splitlines()]


@pytest.mark.parametrize("name", FIXTURES)
def test_each_refusal_fires_alone_on_its_fixture(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    text, patch, findings, head, expected = FIXTURES[name]
    assert check(register, tmp_path, capsys, text, patch, findings, head) == (1, expected)


@pytest.mark.parametrize("name", ["no-section", "RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"])
def test_an_incident_merge_is_still_refused_for_uncited_reserved_and_removed_tests(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    text, patch, findings, head, expected = FIXTURES[name]
    assert check(register, tmp_path, capsys, text, patch, findings, head, "--incident") == (1, expected)


def test_an_incident_fix_passes_a_broken_row_a_contested_row_and_a_finder_major(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    patch = diff("plugins/long-running/bin/desk-runner.py", added=("x = 1",)) + diff("rust/crates/journal/src/lib.rs", added=("fn x() {}",))
    text = body("order/merge-card", "pick/journal")
    assert check(register, tmp_path, capsys, text, patch, MAJOR, HEAD, "--incident") == (0, [])
    assert check(register, tmp_path, capsys, text, patch, MAJOR, HEAD) == (
        1,
        [("HELD-BROKEN", "order/merge-card"), ("RULING-CONTESTED", "pick/journal"), ("FINDER-MAJOR", "f1")],
    )


def test_a_merge_card_tap_authorizes_its_own_head_only(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    patch = diff("go/sand/internal/blob/gc.go", added=("s3.DeleteObject(ctx, key)",))
    assert check(register, tmp_path, capsys, body(), patch, [], HEAD) == (0, [])
    assert check(register, tmp_path, capsys, body(), patch, [], OTHER)[1] == [("RESERVED-NO-ANSWER", "reserved/user-data")]


def test_a_revert_title_is_a_reserved_touch(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "body.md").write_text(body())
    (tmp_path / "pr.diff").write_text(diff("docs/x.md", removed=("x",)))
    argv = ["check-pr", "--ledger", str(register), "--body-file", str(tmp_path / "body.md"), "--diff-file", str(tmp_path / "pr.diff"), "--head", OTHER]
    assert rulings.main([*argv, "--title", 'Revert "fork: copy nothing"']) == 1
    assert capsys.readouterr().out.startswith("RESERVED-NO-ANSWER\treserved/user-data\t")


def test_step_10s_data_job_passes_on_the_seed_label_and_the_superseded_round_2_pick(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    patch = diff("api/src/jobs/oldCloudChats.ts", added=("await deleteOldCloudAgents(team)",))
    assert check(register, tmp_path, capsys, body(), patch, [], OTHER) == (0, [])


def test_a_key_listed_under_a_later_section_is_not_a_cite(register: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    patch = diff("iris-desktop/src/readonly/banner.tsx", added=("x",))
    assert check(register, tmp_path, capsys, body(), patch, [], HEAD) == (1, [("RULINGS-UNCITED", "scope/read-only")])
    assert check(register, tmp_path, capsys, body("scope/read-only"), patch, [], HEAD) == (0, [])


def test_summary_counts_kinds_and_names_the_rows_a_card_must_carry(register: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rulings.main(["summary", "--ledger", str(register)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:8] == [
        "design\tstanding\t1",
        "order\tbroken\t1",
        "pick\tcontested\t1",
        "pick\tstanding\t1",
        "pick\tsuperseded\t1",
        "reserved\tstanding\t2",
        "scope\tstanding\t1",
        "unclassified\tstanding\t1",
    ]
    assert lines[8:] == [
        "authorizations\treserved/old-cloud-agents\t1",
        "authorizations\treserved/user-data\t1",
        "attention\torder/merge-card\tbroken",
        "attention\tpick/journal\tcontested",
        "attention\tcapture/c0db6b7\tunclassified",
    ]


class NotesShell(ledger.Shell):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.rows = json.loads(json.dumps(ROWS))

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        self.calls.append(argv)
        match argv:
            case ["ccn", "-R", REPO, "ledger", "list", "--label", "register:release-v3", "--json"]:
                return json.dumps([{"id": "4f1c9ad", "title": "register: release-v3"}])
            case ["ccn", "-R", REPO, "ledger", "show", "4f1c9ad", "--json"]:
                return json.dumps({"id": "4f1c9ad", "rows": self.rows})
            case ["ccn", "-R", REPO, "show", prefix, "--json"]:
                return json.dumps(next(entry for entry in ANSWERS if entry["id"].startswith(prefix)))
            case ["ccn", "-R", REPO, "answer", "list", "--label", label, "--limit", "0", "--json"]:
                return json.dumps([entry for entry in ANSWERS if label in entry["tags"]])
            case ["ccn", "-R", REPO, "ledger", "row", "set", "4f1c9ad", "--key", _, *fields, "--json"]:
                return json.dumps({"id": "4f1c9ad", "fields": fields})
            case ["gh", "pr", "view", "41500", "--json", "title,body,headRefOid"]:
                return json.dumps({"title": "fork: copy nothing", "body": body("pick/fork"), "headRefOid": OTHER})
            case ["gh", "pr", "diff", "41500"]:
                return diff("go/sand/internal/fork/fork_test.go", removed=("func TestForkCopiesNothing(t *testing.T) {",))
        raise AssertionError(argv)


def test_the_cc_notes_store_classifies_through_row_set_and_checks_a_live_pr(capsys: pytest.CaptureFixture[str]) -> None:
    shell = NotesShell()
    argv = ["classify", "--program", "release-v3", "--repo", REPO, "capture/c0db6b7", "--kind", "pick", "--step", "7", "--path", "infra/rows/*"]
    assert rulings.main(argv, shell) == 0
    assert shell.calls[-1][-4:] == ["--field=kind=pick", "--field=steps=7", "--field=paths=infra/rows/*", "--json"]
    capsys.readouterr()
    assert rulings.main(["check-pr", "--program", "release-v3", "--repo", REPO, "--pr", "41500"], shell) == 1
    assert capsys.readouterr().out == "TEST-REMOVED\tpick/fork\tgo/sand/internal/fork/fork_test.go::TestForkCopiesNothing\n"
