from __future__ import annotations

import json
import re
from pathlib import Path

import findings
import pytest

DRIVE = "brook-cci"
FINDING = "[finding:merge-audit-1] #45412 go/sand/vm/run.go:88 major owner port-runtime-3: the retry loop sleeps inside the request path"
PICK = "Owner on board d50e80e8, Where do agent worktrees live? (worktree-table-choice): picked “One table keyed by lane”, at 7:42 PM"
LOOSE = "3:16 AM [finding:local-state-sweep-1] #45400 dev host /_ssql route in ~/.iris/devhost/ssql/<route>.json: rows of a system table in root's one local control database (#205365 b). pr#45400"


def record(seq: int, kind: str, text: str, **extra) -> dict:
    return {"seq": seq, "kind": kind, "lane": extra.pop("lane", "lane-a"), "at": f"2026-10-30T10:{seq % 60:02d}:00Z", "text": text, "refs": {}, **extra}


class FakeShell:
    def __init__(self, records: list[dict]):
        self.records = records
        self.rows: dict[str, dict[str, str]] = {}
        self.posts: list[list[str]] = []

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if argv[:1] == ["ccn"]:
            verb = argv[3:5]
            if verb == ["ledger", "add"]:
                return json.dumps({"id": "f1nd1ng5"})
            if verb == ["ledger", "show"]:
                return json.dumps({"rows": [{"key": key, "fields": fields} for key, fields in self.rows.items()]})
            if verb == ["ledger", "sync"]:
                for row in json.loads(stdin or "[]"):
                    self.rows[row["key"]] = {**self.rows.get(row["key"], {}), **row["fields"]}
                return ""
        if argv[:2] == ["cci", "tail"]:
            since = argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1]
            kinds = {argv[i + 1] for i, flag in enumerate(argv) if flag == "--kind"}
            return "".join(json.dumps(r) + "\n" for r in self.records if (not since.isdigit() or r["seq"] > int(since)) and r["kind"] in kinds)
        if argv[:2] == ["cci", "grep"]:
            pattern, since = re.compile(argv[2]), int(argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1])
            return "".join(json.dumps(r) + "\n" for r in self.records if r["seq"] >= since and pattern.search(f"{r['kind']} {r['text']}" + (f" re #{r['re']}" if r.get("re") else "") + (f" resolves #{r['resolves']}" if r.get("resolves") else "")))
        if argv[:2] == ["cci", "show"]:
            return json.dumps(next(r for r in self.records if r["seq"] == int(argv[2])))
        if argv[:2] == ["cci", "post"]:
            self.posts.append(argv)
            return "#999\n"
        raise AssertionError(f"unexpected call {argv}")


@pytest.fixture
def entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    found = {"drive": "brook", "cci_drive": DRIVE, "state_dir": str(tmp_path), "checkout": str(tmp_path), "sessions": ["s"]}
    monkeypatch.setattr(findings.drive, "find", lambda name, session: found)
    return found


def states(shell: FakeShell) -> dict[str, tuple[str, str]]:
    return {key: (row["state"], row["by"]) for key, row in shell.rows.items()}


@pytest.mark.parametrize(
    ("later", "state"),
    [
        ([], ("open", "")),
        ([record(20, "answer", "on it", re=10)], ("routed", "20")),
        ([record(20, "answer", "on it", re=10), record(21, "head", "HEAD #45412 abc1234: no sleep in the request path")], ("fixed", "21")),
        ([record(21, "landed", "LANDED #45412 squash 1234567")], ("fixed", "21")),
        ([record(22, "decide", "not a bug, the loop is the backoff", resolves=10, lane="root")], ("closed", "22")),
        ([record(23, "head", "HEAD #45999 def5678: unrelated")], ("open", "")),
    ],
    ids=["unanswered", "answered", "answered-then-head", "landed", "resolved-without-fix", "head-on-another-pr"],
)
def test_a_finding_stays_open_until_a_head_or_landed_names_its_pr(entry, later, state):
    """Owner 3:06 AM Oct 30: sweep findings got lost; a finding is fixed only when a HEAD or LANDED names it (root #205185)."""
    shell = FakeShell([record(10, "defect", FINDING, lane="merge-audit-1"), *later])

    assert findings.main(["sync"], shell) == 0

    assert states(shell) == {"finding/10": state}
    row = shell.rows["finding/10"]
    assert (row["sweep"], row["pr"], row["line"], row["verdict"], row["owner"]) == ("merge-audit-1", "45412", "go/sand/vm/run.go:88", "major", "port-runtime-3")


def test_an_owner_pick_is_open_until_a_head_names_its_card_and_a_merge_tap_is_no_pick(entry):
    tap = "Owner on board d50e80e8, Which open pull requests may merge now, at the versions shown? (merge-prs): 45380 approved at 2780057, at 2:09 AM"
    shell = FakeShell([record(30, "owner", PICK, lane="board-watch"), record(31, "owner", tap, lane="board-watch")])

    findings.main(["sync"], shell)
    assert states(shell) == {"pick/30": ("open", "")}
    assert shell.rows["pick/30"]["text"] == "Where do agent worktrees live?: One table keyed by lane"

    shell.records.append(record(40, "head", "HEAD #45600 aaa1111: worktree-table-choice, one table keyed by lane"))
    findings.main(["sync"], shell)
    assert states(shell) == {"pick/30": ("fixed", "40")}


def test_a_finding_in_a_lanes_own_words_takes_its_pr_and_owner_from_the_record(entry):
    """local-state-sweep-1 #205428 posted its finding with a clock first and no owner word, addressed to the lanes that own the fix."""
    shell = FakeShell([record(10, "defect", LOOSE, lane="local-state-sweep-1", to=["root", "naming-diet-2", "p8a-step7"])])

    findings.main(["sync"], shell)

    row = shell.rows["finding/10"]
    assert (row["sweep"], row["pr"], row["owner"], row["state"]) == ("local-state-sweep-1", "45400", "naming-diet-2", "open")
    assert row["text"].startswith("#45400 dev host /_ssql route")


def test_sync_reads_only_after_its_cursor_and_a_tracked_old_pick_joins_the_ledger(entry):
    shell = FakeShell([record(5, "owner", PICK, lane="board-watch"), record(10, "defect", FINDING)])
    findings.main(["sync", "--since", "8"], shell)
    assert list(shell.rows) == ["finding/10"]
    assert json.loads((Path(entry["state_dir"]) / "findings.json").read_text()) == {"ledger": "f1nd1ng5", "cursor": 10}

    findings.main(["track", "5"], shell)
    assert sorted(shell.rows) == ["finding/10", "pick/5"]


def test_open_prints_the_live_rows_by_sweep_and_post_writes_the_finding_form(entry, capsys):
    shell = FakeShell([record(10, "defect", FINDING), record(11, "defect", "[finding:perf-sweep-1] - - minor owner tooling-misc-3: the poll sleeps 20 s"), record(12, "defect", "a defect that is no finding")])

    findings.main(["open", "--sweep", "perf-sweep-1"], shell)
    assert capsys.readouterr().out.splitlines() == ["perf-sweep-1:", "  #11 open Oct 30 3:11 AM owner tooling-misc-3: the poll sleeps 20 s"]

    findings.main(["post", "--sweep", "perf-sweep-1", "--owner", "port-runtime-3", "--verdict", "major", "--pr", "45412", "--line", "go/x.go:3", "it polls"], shell)
    text = shell.posts[0][shell.posts[0].index("--text") + 1]
    assert findings.row_of(record(50, "defect", text))[1]["owner"] == "port-runtime-3"
    assert text == "[finding:perf-sweep-1] #45412 go/x.go:3 major owner port-runtime-3: it polls"


def test_close_settles_a_one_shot_pick_with_its_reason(entry):
    shell = FakeShell([record(30, "owner", PICK, lane="board-watch")])
    findings.main(["sync"], shell)

    findings.main(["close", "--why", "release 10950 started at 6:13 PM", "30"], shell)

    assert states(shell) == {"pick/30": ("closed", "release 10950 started at 6:13 PM")}
