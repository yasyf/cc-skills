from __future__ import annotations

import fcntl
import hashlib
import io
import json
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import ledger
import pytest
import rulings
from conftest import gateway

REPO = "/repo"
REGISTER_ID = "0cf17c9" + "0" * 33
REGISTER_BODY = "# Register\n\n1. Pulumi state is the only truth (ec2881e).\n"
FLAGS = {"id": "1984bf6" + "0" * 33, "title": "May a migration ship behind a flag?", "body": "No.\nDelete or replace."}
TRUTH = {"id": "ec2881e" + "0" * 33, "title": "Is any ledger a source of truth?", "body": "No: Pulumi state is."}
SEED = {"id": "8075b46" + "0" * 33, "title": "How does a seed converge?", "body": "On every apply.\n" + "x" * 400}


DURABLE = ["answer", "list", "--label", "scope:durable", "--limit", "0"]
REGISTER_LEDGER = "f" * 40
DRIVE_LEDGER = "d" * 40
STAMP = "2026-10-23T12:00:00Z"
AT = "2026-10-23T12:30:00Z"
PROMPT_KEY = "prompt/1023-053000"
LIVE = [
    {"lane": "lane-a", "kind": "head", "text": "lane-a 1234567"},
    {"lane": "root", "kind": "decision", "text": "lane-b: go"},
    {"lane": "p2-credentials", "kind": "state", "text": "p2-credentials FINAL | verdict: done | #42518 landed"},
    {"lane": "lane-b", "kind": "state", "text": "lane-b up"},
    {"lane": "check-pr-grade", "kind": "landed", "text": "check-pr grade landed: acme/app#615"},
    {"lane": "desk-runner", "kind": "report", "text": "RULING-ASK ruling-ask:round-1"},
    {"lane": "lane-c", "kind": "review", "text": "finder: clean"},
    {"lane": "p2-credentials", "kind": "head", "text": "p2-credentials abcdef0"},
]
RELAY = [
    ("No relay code lands in relay-server.", "removes"),
    ("relay-server gains no plug-in point for relay code.", "removes"),
    ("Relay cleanup may live in the desktop app.", "permits"),
]
PLAN = """# Plan

### Step 1. Tooling

9. **Register**: cards quote the words; a parked worker is never kept.
   - The relay-server keeps its health probe (9999999).

10. Next item.

### Step 6. Pools

2. Drain on close.
"""
DIFF = """diff --git a/src/relay/pool.rs b/src/relay/pool.rs
index 1111111..2222222 100644
--- a/src/relay/pool.rs
+++ b/src/relay/pool.rs
@@ -1,3 +1,3 @@
 fn close() {
-    retry_forever(relay-server);
+    self.drain();
 }
"""
CARD_TS = "1790000000.000100"
TAP_TS = "1790000100.000200"
OWNER_TAP = {
    "id": "1111111" + "0" * 33,
    "title": "Merge #12?",
    "body": "Owner tapped Merge on https://acme.slack.com/archives/D1/p1790000000000100; merge also authorizes rule/aaaaaaa.",
    "tags": ["from:owner", "program:brook"],
}
QUOTING_CARD = "rule/aaaaaaa: “Relay code lives in relay-journal.” Merge also authorizes rule/aaaaaaa."


class FakeShell(ledger.Shell):
    def __init__(self, hits: list[str], registers: list[dict], branch: str = "yasyf/brook", program: list[dict] | None = None) -> None:
        self.hits = hits
        self.registers = registers
        self.branch = branch
        self.program = [FLAGS, TRUTH] if program is None else program
        self.queries: list[tuple[str, str]] = []

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        if argv[0] == "git":
            assert argv[1:] == ["-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"]
            return f"{self.branch}\n"
        if argv[0] == "ccx":
            assert argv[1:7] == ["code", "search", "--semantic", "--content", "docs", "-k"]
            self.queries.append((argv[-2], argv[-1]))
            return "# semantic (native)\n" + "".join(f"{hit}.md:1-3#abcd (score=0.016)\n# title\n" for hit in self.hits)
        assert argv[:3] == ["ccn", "-R", REPO], argv
        match argv[3:5]:
            case ["answer", "list"]:
                match argv[3:-1]:
                    case [*durable, "--label", "program:brook"] if durable == DURABLE:
                        return json.dumps(self.program)
                    case [*durable, "--branch", "yasyf/brook"] if durable == DURABLE:
                        return json.dumps([TRUTH, SEED])
            case ["doc", "list"]:
                assert argv[5:] == ["--label", "standing-rules:brook", "--full", "--json"], argv
                return json.dumps(self.registers)
        raise AssertionError(argv)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def match(shell: FakeShell, brief: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *extra: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(brief))
    assert rulings.main(["match", "--program", "brook", "--repo", REPO, *extra], shell) == 0
    return capsys.readouterr().out


def test_register_prints_the_newest_register_doc_or_null(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    older = {"id": "1" * 40, "updated_at": "2026-10-04T00:00:00Z", "body": "# Older register\n"}
    newest = {"id": REGISTER_ID, "updated_at": "2026-10-05T00:00:00Z", "body": REGISTER_BODY}

    assert rulings.main(["register", "--program", "brook", "--repo", REPO], FakeShell([], [newest, older])) == 0
    assert json.loads(capsys.readouterr().out) == {"id": REGISTER_ID, "body": REGISTER_BODY}

    assert rulings.main(["register", "--program", "brook", "--repo", REPO], FakeShell([], [])) == 0
    assert json.loads(capsys.readouterr().out) is None


def test_match_mirrors_the_corpus_and_quotes_uncited_hits_in_rank_order(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = home / ".claude" / "scratch" / "brook" / "rulings"
    corpus.mkdir(parents=True)
    (corpus / "dead000.md").write_text("# superseded\n")
    shell = FakeShell(["1984bf6", "ec2881e", "1984bf6", "8075b46"], [{"id": REGISTER_ID, "updated_at": "2026-10-05T00:00:00Z", "body": REGISTER_BODY}])

    out = match(shell, "Lane brief: add a --skip-tenant flag.", monkeypatch, capsys)

    assert out.startswith("- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n- 8075b46 How does a seed converge?\n")
    assert "ec2881e" not in out
    assert sorted(path.name for path in corpus.iterdir()) == ["1984bf6.md", "8075b46.md", "ec2881e.md"]
    assert (corpus / "1984bf6.md").read_text() == "# May a migration ship behind a flag?\n\nNo.\nDelete or replace.\n"
    assert shell.queries == [("Lane brief: add a --skip-tenant flag.", str(corpus))]


def test_match_drops_excluded_answers(home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    out = match(FakeShell(["1984bf6", "8075b46"], []), "brief", monkeypatch, capsys, "--exclude", "1984bf6")

    assert out.startswith("- 8075b46 How does a seed converge?\n")
    assert "1984bf6" not in out


def test_match_stops_at_the_budget(home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    out = match(FakeShell(["1984bf6", "8075b46"], []), "brief", monkeypatch, capsys, "--budget", "200")

    assert out == "- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n"


def test_match_with_no_candidate_answers_prints_nothing_and_skips_the_search(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = FakeShell(["1984bf6"], [], branch="HEAD", program=[])
    covered = FakeShell(["1984bf6"], [{"id": REGISTER_ID, "updated_at": "2026-10-05T00:00:00Z", "body": REGISTER_BODY}])

    assert match(empty, "brief", monkeypatch, capsys) == ""
    assert match(covered, "brief", monkeypatch, capsys, "--exclude", "1984bf6", "--exclude", "8075b46") == ""
    assert empty.queries == covered.queries == []


def test_a_drive_worker_resolves_its_program_from_the_registry(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drives = home / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    state = home / "state" / "d1-scratch"
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "program": "brook", "state_dir": str(state)}))
    shell = FakeShell(["1984bf6"], [])

    monkeypatch.setattr("sys.stdin", io.StringIO("brief"))
    assert rulings.main(["match", "--drive", "d1", "--repo", REPO], shell) == 0

    assert capsys.readouterr().out.startswith("- 1984bf6 ")
    assert shell.queries == [("brief", str(state / "rulings"))]


def test_a_registry_entry_without_a_program_resolves_it_from_its_state_dir(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drives = home / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    state = home / "state" / "brook"
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "state_dir": str(state)}))
    shell = FakeShell(["1984bf6"], [])

    monkeypatch.setattr("sys.stdin", io.StringIO("brief"))
    assert rulings.main(["match", "--drive", "d1", "--repo", REPO], shell) == 0

    assert capsys.readouterr().out.startswith("- 1984bf6 ")
    assert shell.queries == [("brief", str(state / "rulings"))]


def test_a_verb_without_program_or_drive_uses_the_sessions_drive_and_refuses_with_none(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drives = home / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    named, claimed = home / "state" / "named", home / "state" / "claimed"
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "program": "brook", "sessions": [], "state_dir": str(named)}))
    (drives / "d2.json").write_text(json.dumps({"drive": "d2", "program": "brook", "sessions": ["s-root"], "state_dir": str(claimed)}))

    def queried(env: dict[str, str], *flags: str) -> list[tuple[str, str]]:
        for key in ("CLAUDE_LONG_RUNNING_DRIVE", "CLAUDE_CODE_SESSION_ID"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr("sys.stdin", io.StringIO("brief"))
        shell = FakeShell(["1984bf6"], [])
        assert rulings.main(["match", "--repo", REPO, *flags], shell) == 0
        return shell.queries

    assert queried({"CLAUDE_LONG_RUNNING_DRIVE": "d1", "CLAUDE_CODE_SESSION_ID": "s-root"}) == [("brief", str(named / "rulings"))]
    assert queried({"CLAUDE_CODE_SESSION_ID": "s-root"}) == [("brief", str(claimed / "rulings"))]
    assert queried({"CLAUDE_LONG_RUNNING_DRIVE": "d1"}, "--drive", "d2") == [("brief", str(claimed / "rulings"))]
    assert queried({"CLAUDE_LONG_RUNNING_DRIVE": "d1"}, "--program", "brook") == [("brief", str(home / ".claude" / "scratch" / "brook" / "rulings"))]

    with pytest.raises(SystemExit) as refused:
        queried({"CLAUDE_CODE_SESSION_ID": "s-other"})
    assert refused.value.code == 2
    assert "rulings.py match: error: one of the arguments --program --drive is required" in capsys.readouterr().err


def test_match_reads_only_the_drives_program_and_branch_answers(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = home / ".claude" / "scratch" / "brook" / "rulings"

    match(FakeShell(["1984bf6"], []), "brief", monkeypatch, capsys)
    assert sorted(path.name for path in corpus.iterdir()) == ["1984bf6.md", "8075b46.md", "ec2881e.md"]

    out = match(FakeShell(["8075b46", "1984bf6"], [], branch="HEAD"), "brief", monkeypatch, capsys)
    assert out == "- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n"
    assert sorted(path.name for path in corpus.iterdir()) == ["1984bf6.md", "ec2881e.md"]


def implied(items: list[tuple[str, str]]) -> str:
    return json.dumps([{"n": n, "text": text, "direction": direction, "state": "proposed", "proof": "aaaaaaa"} for n, (text, direction) in enumerate(items, 1)])


def seeded(package: Path) -> dict[str, dict[str, str]]:
    return {
        "rule/aaaaaaa": {
            "kind": "scope",
            "status": "in-force",
            "global": "yes",
            "words": "Relay code lives in relay-journal. Never in relay-server.",
            "answer": '["aaaaaaa"]',
            "at": "Oct 21 9:15 PM PT",
            "said": "1",
            "implications": implied(RELAY),
            "names": '["relay-server", "parked worker"]',
            "surfaces": '["relay-server", "relay-journal"]',
            "steps": '["all"]',
            "lanes": '["all"]',
            "tests": '["C01"]',
            "paths": "none",
            "evidence": "census",
        },
        "rule/bbbbbbb": {
            "kind": "pick",
            "status": "in-force",
            "global": "no",
            "words": "Drain the pool when a run closes.",
            "answer": '["bbbbbbb"]',
            "at": "Oct 20 3:00 PM PT",
            "said": "1",
            "steps": '["6.2"]',
            "lanes": '["all"]',
            "paths": '["src/relay/pool.rs:42 @ 1234567abc"]',
            "symbols": '["Pool.drain"]',
            "tests": '["C02"]',
            "evidence": "landed in acme/app#12",
        },
        "rule/ccccccc": {
            "kind": "pick",
            "status": "in-force",
            "global": "no",
            "words": "Cards quote the words they ask about.",
            "answer": '["ccccccc"]',
            "at": "Oct 22 1:00 PM PT",
            "said": "1",
            "steps": '["1.9"]',
            "lanes": '["all"]',
            "paths": "none",
            "symbols": "none",
            "proof": "proof:board:fedcba9876543210",
        },
        "rule/ddddddd": {
            "kind": "order",
            "status": "superseded",
            "global": "no",
            "words": "Retry every failed upload forever.",
            "answer": '["ddddddd"]',
            "steps": "all",
            "lanes": '["all"]',
            "paths": "none",
            "superseded_by": '["rule/bbbbbbb"]',
        },
        "rule/eeeeeee": {
            "kind": "gate",
            "status": "at-risk",
            "global": "no",
            "words": "Two outages stop the drive.",
            "answer": '["eeeeeee"]',
            "at": "Oct 18 9:03 PM PT",
            "steps": "all",
            "lanes": '["all"]',
            "paths": "none",
        },
        "exception/E1": {
            "kind": "exception",
            "status": "pending-owner",
            "global": "yes",
            "words": "The pass hands out work.",
            "answer": "[]",
            "steps": '["8.1"]',
            "lanes": '["all"]',
            "paths": "none",
        },
        "rule/9999999": {
            "kind": "pick",
            "status": "in-force",
            "global": "no",
            "words": "The relay-server may keep its health probe.",
            "answer": '["9999999"]',
            "at": "Oct 22 4:00 PM PT",
            "said": "1",
            "steps": '["7.1"]',
            "lanes": '["all"]',
            "paths": "none",
            "symbols": "none",
        },
        "no/0123456789abcdef": {"no": "chatter", "at": STAMP},
        "corpus/b-tests": {"kind": "corpus", "path": str(package / "plan" / "B-tests.md"), "status": "in-force"},
    }


class RegisterShell(ledger.Shell):
    def __init__(
        self,
        rows: dict[str, dict[str, str]],
        answers: list[dict] | None = None,
        diff: str = "",
        records: list[dict] | None = None,
        board: dict | None = None,
        present: frozenset[str] = frozenset(),
        blocks: dict[str, str] | None = None,
    ) -> None:
        self.board = board or {}
        self.blocks = blocks or {}
        self.rows = {key: dict(fields) for key, fields in rows.items()}
        self.present = present
        self.answers = [dict(answer, tags=list(answer["tags"])) if "tags" in answer else dict(answer) for answer in answers or []]
        self.diff = diff
        self.records = records or []
        self.calls: list[list[str]] = []
        self.status = "active"

    def writes(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[3:6] == ["ledger", "row", "set"]]

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        argv = gateway(argv)
        self.calls.append(argv)
        match argv:
            case ["git", "-C", "/repo", "for-each-ref", "--format=%(refname) %(objectname)", rulings.LEDGER_REFS]:
                return f"{rulings.LEDGER_REFS}{REGISTER_LEDGER} {hashlib.sha1(json.dumps(self.rows, sort_keys=True).encode()).hexdigest()}\n"
            case ["ccn", "-R", "/repo", "ledger", "list", "--label", "register", "--label", label, "--json"]:
                return json.dumps([{"id": REGISTER_LEDGER, "updated_at": STAMP}] if label == "program:brook" else [])
            case ["ccn", "-R", "/repo", "ledger", "show", identity, "--json"] if identity == REGISTER_LEDGER:
                return json.dumps({"id": REGISTER_LEDGER, "status": self.status, "rows": [{"key": key, "fields": fields, "updated_at": STAMP} for key, fields in self.rows.items()]})
            case ["ccn", "-R", "/repo", "ledger", "row", "set", identity, "--key", key, *flags] if identity == REGISTER_LEDGER:
                for flag, value in zip(flags[::2], flags[1::2], strict=True):
                    assert flag == "--field"
                    name, _, text = value.partition("=")
                    self.rows.setdefault(key, {})[name] = text
                return ""
            case ["ccn", "-R", "/repo", "doc", "list", "--label", "standing-rules:brook", "--full", "--json"]:
                return "[]"
            case ["ccn", "-R", "/repo", "answer", "list", *labels, "--limit", "0", "--json"]:
                wanted = set(labels[1::2])
                return json.dumps([answer for answer in self.answers if wanted <= set(answer["tags"])])
            case ["ccn", "-R", "/repo", "answer", "show", identity, "--json"]:
                return json.dumps(next(answer for answer in self.answers if answer["id"].startswith(identity)))
            case ["ccn", "-R", "/repo", "answer", "edit", identity, "--add-label", label]:
                next(answer for answer in self.answers if answer["id"] == identity)["tags"].append(label)
                return ""
            case ["ccn", "-R", "/repo", "answer", "supersede", _, "--by", _]:
                return ""
            case ["ccn", "-R", "/repo", "answer", "add", title, "--body", body, *labels, "--json"]:
                made = {"id": "4444444" + "0" * 33, "title": title, "body": body, "tags": labels[1::2]}
                self.answers.append(made)
                return json.dumps(made)
            case ["cc-present", "outcomes", "--all", "--session", "s-root"]:
                return json.dumps(self.board)
            case ["cc-present", "block", "show", block, "--session", "s-root"] if block in self.blocks:
                return self.blocks[block]
            case ["gh", "api", "repos/acme/app/compare/main...cafef00d", "-H", rulings.DIFF_MEDIA, "--allow-escape-sequences"]:
                return self.diff
            case ["git", "-C", "/repo", "ls-remote", "--exit-code", "origin", ref]:
                return f"{BASE_TIP}\t{ref}\n"
            case ["git", "-C", "/repo", "--no-lazy-fetch", "cat-file", "--batch-check"]:
                return "".join(f"{sha} commit 1\n" if sha in self.present else f"{sha} missing\n" for sha in (stdin or "").split())
            case ["git", "-C", "/repo", "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *_]:
                return ""
            case ["gh", "api", "repos/acme/app/commits/cafef00d", "--jq", ".sha"]:
                return f"{FULL_HEAD}\n"
            case ["git", "-C", "/repo", "merge-base", _, _]:
                return hashlib.sha1(self.diff.encode()).hexdigest() + "\n"
            case ["git", "-C", "/repo", "diff", *_, output, _]:
                Path(output.removeprefix("--output=")).write_text(self.diff)
                return ""
            case ["gh", "pr", "view", "12", "--repo", "acme/app", "--json", "baseRefName"]:
                return json.dumps({"baseRefName": "main"})
            case ["gh", "api", "repos/acme/app"]:
                return json.dumps({"default_branch": "main"})
            case ["git", "-C", "/repo", "remote", "get-url", "origin"]:
                return "git@github.com:acme/app.git\n"
            case ["cci", "post", *_]:
                return "#1\n"
            case ["cc-present", *_]:
                return ""
            case ["cci", "grep", "--drive", "brook-cci", "--json", *_]:
                return "".join(json.dumps(record) + "\n" for record in self.records)
            case ["cci", "show", seq, "--json"]:
                return "".join(json.dumps({"drive": "brook-cci"} | record) + "\n" for record in self.records if str(record["seq"]) == seq)
        raise AssertionError(argv)


@pytest.fixture
def package(home: Path) -> Path:
    root = home / "package"
    (root / "plan").mkdir(parents=True)
    (root / "plan" / "plan.md").write_text(PLAN)
    (root / "plan" / "B-tests.md").write_text("# Tests\n")
    (root / "boards").mkdir()
    (root / "boards" / "card-confirm.json.pre-spikes").write_text(json.dumps({"options": [{"label": "Keep the parked worker"}]}))
    (root / "designs" / "relay").mkdir(parents=True)
    (root / "designs" / "relay" / "design.md").write_text("The relay-server stays small.\n")
    return root


class Judge:
    """The drive's judge, scripted: it records each question and answers with `verdict(kind, artifact)`, `None` by default, as a failed call."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, str, dict]] = []
        self.verdict = lambda kind, artifact: None

    def __call__(self, kind: str, evidence: str, artifact: str, **kwargs) -> rulings.judge.Verdict | None:
        self.asked.append((kind, evidence, artifact, kwargs))
        return self.verdict(kind, artifact)


@pytest.fixture(autouse=True)
def judged(monkeypatch: pytest.MonkeyPatch) -> Judge:
    stub = Judge()
    monkeypatch.setattr(rulings.judge, "judge", stub)
    return stub


@pytest.fixture
def state(home: Path) -> Path:
    path = home / ".claude" / "scratch" / "brook"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def registered(home: Path, state: Path) -> Path:
    drives = home / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    entry = {"drive": "brook-drive", "program": "brook", "sessions": ["s-root"], "state_dir": str(state), "cci_drive": "brook-cci", "checkout": REPO, "ledger": DRIVE_LEDGER}
    (drives / "brook-drive.json").write_text(json.dumps(entry))
    return drives


def captured(state: Path, *entries: dict) -> None:
    (state / "owner-prompts.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def capture(item: str, text: str, source: str = "prompt", block: str | None = None) -> dict:
    return {"digest": item * 16, "at": AT, "session": "s-root", "source": source, "text": text} | ({"block": block} if block else {})


def ran(capsys: pytest.CaptureFixture[str], shell: ledger.Shell, verb: str, *argv: str) -> tuple[int, str]:
    code = rulings.main([verb, "--program", "brook", "--repo", REPO, *argv], shell)
    return code, capsys.readouterr().out


def classify(tmp_path: Path, capsys: pytest.CaptureFixture[str], shell: RegisterShell, *entries: dict) -> tuple[int, str]:
    path = tmp_path / "classified.json"
    path.write_text(json.dumps(list(entries)))
    return ran(capsys, shell, "classify", str(path))


def hashed_correctly(text: str) -> bool:
    body, _, last = text.rstrip("\n").rpartition("\n")
    return last == f"slice-hash: {hashlib.sha256((body + chr(10)).encode()).hexdigest()[:12]}"


def test_rows_prints_every_row_with_its_update_time_and_the_classified_digests(home: Path, package: Path, state: Path, capsys) -> None:
    code, out = ran(capsys, RegisterShell(seeded(package)), "rows", "--json")

    printed = json.loads(out)
    assert code == 0
    assert (printed["ledger"], printed["program"], printed["state_dir"]) == (REGISTER_LEDGER, "brook", str(state))
    assert printed["rows"]["rule/aaaaaaa"]["updated_at"] == STAMP
    assert printed["rows"]["rule/aaaaaaa"]["words"] == "Relay code lives in relay-journal. Never in relay-server."
    assert printed["classified"] == ["0123456789abcdef", "fedcba9876543210"]


def test_rows_reads_the_ledger_once_until_a_ledger_ref_moves(home: Path, package: Path, state: Path, capsys) -> None:
    """cci #200150: the plain-words hook timed out after 20 s on `rows`, whose two ccn reads scan 67,000 cc-notes refs and wait behind a concurrent write."""
    shell = RegisterShell(seeded(package))
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]] == [["ledger", "list"], ["ledger", "show"]]
    shell.rows["rule/aaaaaaa"]["words"] = "Relay code lives in relay-journal."
    code, out = ran(capsys, shell, "rows", "--json")
    assert code == 0 and json.loads(out)["rows"]["rule/aaaaaaa"]["words"] == "Relay code lives in relay-journal."


def test_a_stale_register_copy_rebuilds_from_its_own_ledger_until_that_ledger_is_archived(home: Path, package: Path, state: Path, capsys) -> None:
    """cci #215987: `ccn ledger list` folds every ledger's history and took 80 to 180 s on a Sprite after each cc-notes fetch, so every finder pass there timed out reading the plan."""
    shell = RegisterShell(seeded(package))
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    shell.rows["rule/aaaaaaa"]["words"] = "Relay code lives in relay-journal."
    code, out = ran(capsys, shell, "rows", "--json")
    assert code == 0 and json.loads(out)["rows"]["rule/aaaaaaa"]["words"] == "Relay code lives in relay-journal."
    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]] == [["ledger", "list"], ["ledger", "show"], ["ledger", "show"]]

    shell.status = "archived"
    shell.rows["rule/aaaaaaa"]["words"] = "Relay code lives in relay-journal, never the server."
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]][3:] == [["ledger", "show"], ["ledger", "list"], ["ledger", "show"]]


class SlowShowShell(RegisterShell):
    def __init__(self, rows: dict[str, dict[str, str]]) -> None:
        super().__init__(rows)
        self.showing = threading.Event()
        self.release = threading.Event()

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if argv[3:5] == ["ledger", "show"]:
            self.showing.set()
            self.release.wait(10)
        return super().run(argv, stdin, cwd)


def test_concurrent_readers_of_a_stale_register_copy_scan_the_ledger_once(home: Path, package: Path, state: Path) -> None:
    """cci #214457: eight finder passes on one Sprite each ran an 830 MB `ccn` scan of the same moved ledger at once and exhausted its memory."""
    shell = SlowShowShell(seeded(package))
    readers = [threading.Thread(target=rulings.main, args=(["rows", "--json", "--program", "brook", "--repo", REPO], shell)) for _ in range(3)]
    readers[0].start()
    assert shell.showing.wait(10)
    for reader in readers[1:]:
        reader.start()
    time.sleep(0.5)
    shell.release.set()
    for reader in readers:
        reader.join(10)

    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]] == [["ledger", "list"], ["ledger", "show"]]


@pytest.mark.parametrize("verb", [["slice", "--lane", "lane-a", "--step", "1.9"], ["plan"]], ids=["slice", "plan"])
def test_a_read_takes_the_last_register_copy_while_another_process_rebuilds_it(home: Path, package: Path, state: Path, capsys, monkeypatch: pytest.MonkeyPatch, verb: list[str]) -> None:
    """p8a-step7 #220209: fidelity passes failed after 120 s waiting on the register rebuild a classify held; a slice reads the last copy instead (root #220219)."""
    monkeypatch.setattr(ledger, "LOCK_WAIT_SECONDS", 2)
    shell = RegisterShell(seeded(package))
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    shell.rows["rule/aaaaaaa"]["words"] = "Relay code lives in relay-journal."
    with (state / "register-brook.lock").open("a+") as rebuilding:
        fcntl.flock(rebuilding, fcntl.LOCK_EX)
        started = time.monotonic()
        code, out = ran(capsys, shell, *verb)

    assert code == 0 and time.monotonic() - started < 1
    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]] == [["ledger", "list"], ["ledger", "show"]]
    if verb[0] == "slice":
        assert "> Relay code lives in relay-journal. Never in relay-server." in out.splitlines()


def test_plan_reads_any_kept_register_copy_without_rebuilding_it(home: Path, package: Path, state: Path, capsys) -> None:
    """remote-desks-3 #227003: every ledger ref move made each Sprite rebuild its register before a pass could read the plan path, and the cold rebuild ran past the pass's 300 s timeout."""
    shell = RegisterShell(seeded(package))
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    shell.rows["rule/aaaaaaa"]["words"] = "Relay code lives in relay-journal."

    code, out = ran(capsys, shell, "plan")

    assert code == 0 and out.strip().endswith("plan.md")
    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]] == [["ledger", "list"], ["ledger", "show"]]


def test_a_sprite_reads_the_shipped_register_snapshot_however_its_ledger_refs_moved(home: Path, package: Path, state: Path, capsys) -> None:
    """Owner 5:19 PM Oct 30 through root #228532: every Sprite finder pass failed waiting 120 s on a cold register rebuild; sprite-job.sh ships this machine's copy as a snapshot, and no read on the Sprite rebuilds it."""
    shell = RegisterShell(seeded(package))
    assert ran(capsys, shell, "rows", "--json")[0] == 0
    kept = state / "register-brook.json"
    kept.write_text(json.dumps({**json.loads(kept.read_text()), "snapshot": True}))
    shell.rows["rule/aaaaaaa"]["words"] = "Relay code lives in relay-journal."

    code, _ = ran(capsys, shell, "slice", "--lane", "lane-a", "--step", "1.9")

    assert code == 0
    assert [argv[3:5] for argv in shell.calls if argv[:1] == ["ccn"]] == [["ledger", "list"], ["ledger", "show"]]


def test_classify_refuses_a_scope_row_without_three_implications_or_any_names(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("1", "keep the uploader in relay-journal and out of the shell"))
    shell = RegisterShell(seeded(package))
    two = [{"text": "The shell gains no uploader.", "direction": "removes"}, {"text": "The journal keeps the uploader.", "direction": "permits"}]

    code, out = classify(tmp_path, capsys, shell, {"digest": "1" * 16, "kind": "scope", "words": "keep the uploader in relay-journal", "implications": two})

    assert code == 1
    assert out.splitlines() == [
        f"SCOPE-IMPLICATIONS {'1' * 16}: a scope row carries 3 to 6 implications, not 2",
        f"SCOPE-NAMES {'1' * 16}: a scope row lists every name the thing has in the plan, the boards and the code",
    ]
    assert shell.writes() == []


def test_classify_refuses_an_order_for_repeat_or_placement_words(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("2", "I told you the uploader goes in relay-journal"), capture("3", "the uploader goes in relay-journal"))
    shell = RegisterShell(seeded(package))

    code, out = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "2" * 16, "kind": "order", "words": "I told you the uploader goes in relay-journal"},
        {"digest": "3" * 16, "kind": "order", "words": "the uploader goes in relay-journal"},
    )

    assert code == 1
    refused = out.splitlines()
    assert refused[0].startswith(f"ORDER-IS-SCOPE {'2' * 16}: the words carry I told you;")
    assert refused[1].startswith(f"REPEAT {'2' * 16}: the words carry I told you;")
    assert refused[2] == f"ORDER-IS-SCOPE {'3' * 16}: the words carry a placement rule (goes in); a repeat extends its row and a placement is a scope row"
    assert shell.writes() == []


@pytest.mark.parametrize(
    "words",
    ["put yourself in the humans shoes", "put it in the humans shoes", "make sure the dashboard liv-updates in terms of state it can poll locally"],
)
def test_an_idiom_with_no_code_object_is_no_placement(home: Path, package: Path, state: Path, tmp_path: Path, capsys, words: str) -> None:
    captured(state, capture("6", words))
    shell = RegisterShell(seeded(package))

    code, out = classify(tmp_path, capsys, shell, {"digest": "6" * 16, "kind": "order", "words": words})

    assert code == 0, out
    assert shell.rows[PROMPT_KEY]["words"] == words


def test_put_in_a_crate_path_is_a_placement(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    words = "put the mint in rust/crates/relay-api"
    captured(state, capture("6", words))

    code, out = classify(tmp_path, capsys, RegisterShell(seeded(package)), {"digest": "6" * 16, "kind": "order", "words": words})

    assert (code, out.strip()) == (
        1,
        f"ORDER-IS-SCOPE {'6' * 16}: the words carry a placement rule (put the mint in); a repeat extends its row and a placement is a scope row",
    )


def test_classify_refuses_words_that_are_not_in_the_capture(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("3", "the uploader goes in relay-journal"))

    code, out = classify(tmp_path, capsys, RegisterShell(seeded(package)), {"digest": "3" * 16, "kind": "pick", "words": "the uploader belongs to the journal"})

    assert code == 1
    assert out.startswith(f"NOT-VERBATIM {'3' * 16}: ")


def test_classify_refuses_a_repeat_that_adds_a_second_row(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("6", "how many times do I have to say no parked worker"))
    three = [{"text": f"Line {n}.", "direction": "removes"} for n in range(3)]

    code, out = classify(
        tmp_path,
        capsys,
        RegisterShell(seeded(package)),
        {"digest": "6" * 16, "kind": "scope", "words": "how many times do I have to say no parked worker", "implications": three, "names": ["parked worker"]},
    )

    assert code == 1
    assert out.strip() == f"REPEAT {'6' * 16}: the words carry how many times; extend rule/aaaaaaa with extends=<key>, never a second row"


@pytest.mark.parametrize("quote", ["direct or proxy via API", "The pass hands out work"])
def test_a_quoted_phrase_no_in_force_row_carries_is_no_repeat_so_classify_adds_the_row(
    home: Path, package: Path, state: Path, tmp_path: Path, capsys, quote: str
) -> None:
    words = f'for the uploads use "{quote}" and nothing else'
    captured(state, capture("4", words))
    shell = RegisterShell(seeded(package))

    code, out = classify(tmp_path, capsys, shell, {"digest": "4" * 16, "kind": "order", "words": words})

    assert code == 0, out
    assert shell.rows[PROMPT_KEY]["words"] == words


def test_classify_keys_a_captured_owner_pick_by_its_cc_notes_answer(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    picked = "c0ffee1d" * 5
    captured(state, capture("9", "Keep the nightly report after cutover?\nPicked: Keep it", source="answer") | {"answer": picked})
    shell = RegisterShell(seeded(package))

    code, out = classify(tmp_path, capsys, shell, {"digest": "9" * 16, "kind": "pick", "words": "Keep it"})

    assert code == 0, out
    assert json.loads(shell.rows[f"rule/{picked[:7]}"]["answer"]) == [picked]


def test_a_quoted_phrase_from_a_rows_words_is_a_repeat(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    words = 'remember "Relay code lives in relay-journal" for the uploader too'
    captured(state, capture("5", words))
    shell = RegisterShell(seeded(package))

    code, out = classify(tmp_path, capsys, shell, {"digest": "5" * 16, "kind": "pick", "words": words})

    assert code == 1
    assert out.strip() == (
        f'REPEAT {"5" * 16}: the words carry a quoted sentence "Relay code lives in relay-journal" from rule/aaaaaaa; '
        "extend the row it repeats with extends=<key>, never a second row"
    )
    assert shell.writes() == []


SYNC_ROW = {
    "rule/9999999": {
        "kind": "scope",
        "status": "in-force",
        "global": "yes",
        "words": "the local sessions never block on remote syncing, even when it is slow or failing",
        "answer": '["9999999"]',
        "at": "Oct 22 6:11 PM PT",
        "said": "1",
        "names": '["remote syncing", "sync", "local experience"]',
        "steps": '["all"]',
        "lanes": '["all"]',
        "paths": "none",
    }
}


def test_one_shared_plain_word_is_no_repeat_so_classify_adds_the_row_and_notes_the_candidate(
    home: Path, package: Path, state: Path, tmp_path: Path, capsys
) -> None:
    words = "Explain what the sync loop fixes actually are and if they are fixed in the latest litestream"
    captured(state, capture("8", words))
    shell = RegisterShell(seeded(package) | SYNC_ROW)

    code, out = classify(tmp_path, capsys, shell, {"digest": "8" * 16, "kind": "order", "words": words})

    assert code == 0, out
    assert json.loads(out.splitlines()[0])["repeat_candidates"] == ["rule/9999999"]
    assert json.loads(shell.rows[PROMPT_KEY]["repeat_candidates"]) == ["rule/9999999"]
    assert json.loads(shell.rows["rule/9999999"].get("repeats") or "[]") == []


def test_plain_word_names_of_one_row_are_no_repeat_but_a_phrase_name_is(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    plain = "the sync loop and its replication lag stay on the follow-up list"
    captured(state, capture("7", plain))
    shell = RegisterShell(seeded(package) | SYNC_ROW | {"rule/9999999": SYNC_ROW["rule/9999999"] | {"names": '["remote syncing", "sync", "replication", "local experience"]'}})

    code, out = classify(tmp_path, capsys, shell, {"digest": "7" * 16, "kind": "order", "words": plain})

    assert code == 0, out
    assert json.loads(out.splitlines()[0])["repeat_candidates"] == ["rule/9999999"]

    words = "the sync loop must not hurt the local experience"
    captured(state, capture("8", words))

    three = [{"text": f"Line {n}.", "direction": "removes"} for n in range(3)]
    code, out = classify(tmp_path, capsys, RegisterShell(seeded(package) | SYNC_ROW), {"digest": "8" * 16, "kind": "scope", "words": words, "names": ["sync loop"], "implications": three})

    assert (code, out.strip()) == (1, f"REPEAT {'8' * 16}: the words carry names of rule/9999999; extend rule/9999999 with extends=<key>, never a second row")


def test_classify_refuses_two_new_rows_under_one_key_in_one_batch(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("1", "the shell never uploads"), capture("2", "the journal keeps the queue"))
    shell = RegisterShell(seeded(package))

    code, out = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "1" * 16, "kind": "pick", "words": "the shell never uploads"},
        {"digest": "2" * 16, "kind": "pick", "words": "the journal keeps the queue"},
    )

    assert (code, out.strip()) == (1, f"KEY-TAKEN {'2' * 16}: {PROMPT_KEY} already exists or comes twice in this batch; extend it or pass another key")
    assert shell.writes() == []


def test_classify_refuses_a_restated_mechanism_and_a_supersede_that_names_nothing_it_covers(
    home: Path, package: Path, state: Path, tmp_path: Path, capsys
) -> None:
    captured(state, capture("1", "keep the parked worker off the desktop app"), capture("2", "drain only on shutdown"))
    three = [{"text": f"Line {n}.", "direction": "removes"} for n in range(3)]

    code, out = classify(
        tmp_path,
        capsys,
        RegisterShell(seeded(package)),
        {"digest": "1" * 16, "key": "rule/1111111", "kind": "scope", "words": "keep the parked worker off the desktop app", "implications": three, "names": ["parked worker"]},
        {"digest": "2" * 16, "key": "rule/2222222", "kind": "pick", "words": "drain only on shutdown", "supersedes": "rule/aaaaaaa"},
    )

    assert code == 1
    assert out.splitlines() == [
        f"REPEAT {'1' * 16}: the words carry names of rule/aaaaaaa; extend rule/aaaaaaa with extends=<key>, never a second row",
        f"SUPERSEDES-UNNAMED {'2' * 16}: only owner words naming what rule/aaaaaaa covers supersede it",
    ]


def test_classify_counts_implications_across_extensions_in_one_batch(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("1", "no parked worker here"), capture("2", "no parked worker there"))
    two = [{"text": "One more line.", "direction": "removes"}, {"text": "Another line.", "direction": "removes"}]

    code, out = classify(
        tmp_path,
        capsys,
        RegisterShell(seeded(package)),
        {"digest": "1" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker here", "implications": two},
        {"digest": "2" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker there", "implications": two},
    )

    assert (code, out.strip()) == (1, f"SCOPE-IMPLICATIONS {'2' * 16}: a scope row carries 3 to 6 implications, not 7")


def test_a_pick_that_supersedes_a_row_it_names_supersedes_both_answers(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("1", "move relay-server cleanup to the desktop app"))
    shell = RegisterShell(seeded(package))

    code, _ = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "1" * 16, "answer": ["abcdef1"], "kind": "pick", "words": "move relay-server cleanup to the desktop app", "names": ["relay-server"], "supersedes": "rule/aaaaaaa"},
    )

    assert code == 0
    assert ["ccn", "-R", REPO, "answer", "supersede", "aaaaaaa", "--by", "abcdef1"] in shell.calls
    assert (shell.rows["rule/aaaaaaa"]["status"], shell.rows["rule/aaaaaaa"]["superseded_by"]) == ("superseded", '["rule/abcdef1"]')
    assert shell.rows["rule/abcdef1"]["supersedes"] == '["rule/aaaaaaa"]'


def test_classify_refuses_a_pick_that_widens_a_scope_row(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("a", "run the uploader beside relay-server too"))

    code, out = classify(
        tmp_path,
        capsys,
        RegisterShell(seeded(package)),
        {"digest": "a" * 16, "kind": "pick", "words": "run the uploader beside relay-server too", "names": ["relay-server"], "surfaces": ["desktop app"]},
    )

    assert code == 1
    assert out.strip() == f"PICK-WIDENS {'a' * 16}: the pick adds desktop app to rule/aaaaaaa; pass supersedes=rule/aaaaaaa with owner words naming each surface"


def test_classify_writes_a_scope_row_its_card_and_one_record_to_every_live_lane(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    (state / "briefs").mkdir()
    (state / "briefs" / "lane-a.md").write_text("Brief.\nThe desktop shell keeps its uploads.\n")
    sliced(state, {"lane-a": "1.1", "lane-b": "7.1"})
    captured(state, capture("4", "uploads stay out of the desktop shell. keep them in the journal"))
    shell = RegisterShell(seeded(package), records=LIVE)
    three = [
        {"text": "The desktop shell gains no uploader.", "direction": "removes"},
        {"text": "Shell upload code is deleted.", "direction": "removes"},
        {"text": "The journal may keep an upload queue.", "direction": "permits"},
    ]

    code, out = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "4" * 16, "kind": "scope", "words": "uploads stay out of the desktop shell.", "implications": three, "names": ["desktop shell"], "surfaces": ["desktop app"]},
    )

    assert code == 0
    result = json.loads(out)
    assert (result["key"], result["how"], result["kind"], result["status"]) == (PROMPT_KEY, "new", "scope", "in-force")
    assert result["hits"] == [f"open {PROMPT_KEY} {state / 'briefs' / 'lane-a.md'}:2 desktop shell"]
    row = shell.rows[PROMPT_KEY]
    assert (row["words"], row["global"], row["steps"], row["proof"], row["at"]) == ("uploads stay out of the desktop shell. keep them in the journal", "yes", '["all"]', f"proof:prompt:{'4' * 16}", "Oct 23 5:30 AM PT")
    assert [item["state"] for item in json.loads(row["implications"])] == ["proposed"] * 3
    card = json.loads((state / "cards" / "prompt:1023-053000.json").read_text())
    assert card == {
        "status": "needs_you",
        "title": "“uploads stay out of the desktop shell.”",
        "body": "This also means:\n1. The desktop shell gains no uploader. (removes)\n2. Shell upload code is deleted. (removes)\n3. The journal may keep an upload queue. (permits)",
        "options": ["Right", "Some wrong", "Open board"],
        "recommended": "Right",
        "waits": "prompt:1023-053000",
    }
    [post] = [argv for argv in shell.calls if argv[:2] == ["cci", "post"]]
    announced = Path(post[post.index("--path") + 1]).read_text().splitlines()
    assert announced[0] == f"Register classify, 1 rows: new {PROMPT_KEY}."
    assert f"Register new {PROMPT_KEY} (scope): the owner's words and what they mean, verbatim." in announced
    assert "> uploads stay out of the desktop shell. keep them in the journal" in announced
    assert "3. The journal may keep an upload queue. (permits, proposed, waits for the owner's tick)" in announced
    assert [post[index + 1] for index, flag in enumerate(post) if flag == "--to"] == ["lane-a", "lane-b"]


def test_classify_warns_of_each_new_implication_the_judge_reads_as_misstating_his_words_and_writes_it_anyway(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys, judged: Judge
) -> None:
    captured(state, capture("4", "uploads stay out of the desktop shell. keep them in the journal"))
    shell = RegisterShell(seeded(package), records=LIVE)
    three = [
        {"text": "The desktop shell gains no uploader.", "direction": "removes"},
        {"text": "Shell upload code is deleted.", "direction": "removes"},
        {"text": "The journal may keep an upload queue.", "direction": "permits"},
    ]
    binds = rulings.judge.Verdict("yes", "Shell upload code is deleted.", "uploads stay out of the desktop shell", "He keeps uploads out of the shell; he never asked for its code to be deleted.")
    judged.verdict = lambda kind, artifact: binds if artifact.startswith("Shell upload") else None
    path = tmp_path / "classified.json"
    path.write_text(json.dumps([{"digest": "4" * 16, "kind": "scope", "words": "uploads stay out of the desktop shell.", "implications": three, "names": ["desktop shell"], "surfaces": ["desktop app"]}]))

    assert rulings.main(["classify", "--program", "brook", "--repo", REPO, str(path)], shell) == 0

    assert sorted(artifact for _, _, artifact, _ in judged.asked) == sorted(f"{item['text']} ({item['direction']})" for item in three)
    assert {(kind, evidence, kwargs["drive"]) for kind, evidence, _, kwargs in judged.asked} == {
        ("implication", f'His words on ruling {PROMPT_KEY}, verbatim, oldest first:\n- Oct 23 5:30 AM PT: "uploads stay out of the desktop shell. keep them in the journal"', "brook-drive")
    }
    [warning] = [line for line in capsys.readouterr().err.splitlines() if line.startswith("WARN")]
    assert warning.startswith(f"WARN {PROMPT_KEY} implication 2 misstates the owner's words and is written anyway: He keeps uploads out of the shell;")
    assert len(json.loads(shell.rows[PROMPT_KEY]["implications"])) == 3


def test_classify_posts_one_record_per_batch_however_many_rows_and_captures_it_touches(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    sliced(state, {"lane-a": "1.1", "lane-b": "7.1"})
    captured(state, capture("1", "no parked worker here"), capture("2", "no parked worker there"), capture("3", "the probe stays on relay-server"))
    shell = RegisterShell(seeded(package), records=LIVE)
    one = [{"text": "One more line.", "direction": "removes"}]

    code, _ = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "1" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker here", "implications": one},
        {"digest": "2" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker there", "implications": one},
        {"digest": "3" * 16, "extends": "rule/9999999", "words": "the probe stays on relay-server"},
    )

    assert code == 0
    [post] = [argv for argv in shell.calls if argv[:2] == ["cci", "post"]]
    announced = Path(post[post.index("--path") + 1]).read_text().splitlines()
    assert announced[0] == "Register classify, 2 rows: extended rule/aaaaaaa, extended rule/9999999."
    assert "Register extended rule/aaaaaaa (scope): the owner's words and what they mean, verbatim." in announced
    assert "Register extended rule/9999999 (pick): the owner's words and what they mean, verbatim." in announced
    assert "> also, Oct 23 5:30 AM PT: no parked worker there" in announced
    assert [post[index + 1] for index, flag in enumerate(post) if flag == "--to"] == ["lane-a", "lane-b"]


def test_classify_announces_a_row_only_to_the_live_lanes_whose_slice_it_changes(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    sliced(state, {"lane-a": "1.1", "lane-b": "7.1"})
    captured(state, capture("3", "the probe stays on relay-server"))
    shell = RegisterShell(seeded(package), records=LIVE)

    code, _ = classify(tmp_path, capsys, shell, {"digest": "3" * 16, "extends": "rule/9999999", "words": "the probe stays on relay-server"})

    assert code == 0
    [post] = [argv for argv in shell.calls if argv[:2] == ["cci", "post"]]
    assert [post[index + 1] for index, flag in enumerate(post) if flag == "--to"] == ["lane-b"]


def sliced(state: Path, steps: dict[str, str]) -> None:
    (state / "briefs").mkdir(exist_ok=True)
    for lane, step in steps.items():
        (state / "briefs" / f"{lane}-slice.md").write_text(f"Brief.\nRegister slice: lane={lane} step={step}\n")


def test_classify_loads_the_register_once_and_walks_the_briefs_once_per_batch(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    walks: list[Path] = []
    monkeypatch.setattr(rulings, "briefs", lambda root: walks.append(root) or [])
    captured(state, capture("1", "no parked worker here"), capture("2", "no parked worker there"))
    shell = RegisterShell(seeded(package), records=LIVE)
    one = [{"text": "One more line.", "direction": "removes"}]

    code, _ = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "1" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker here", "implications": one},
        {"digest": "2" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker there", "implications": one},
    )

    assert code == 0
    assert [argv[3:5] for argv in shell.calls].count(["ledger", "show"]) == 1
    assert walks == [state]


def test_classify_walks_the_briefs_once_for_its_announce_and_sweeps_nothing_for_a_row_with_no_names(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    walks: list[Path] = []
    monkeypatch.setattr(rulings, "briefs", lambda root: walks.append(root) or [])
    captured(state, capture("1", "drain the pool when a run closes, every time"))

    code, out = classify(tmp_path, capsys, RegisterShell(seeded(package), records=LIVE), {"digest": "1" * 16, "extends": "rule/bbbbbbb", "words": "drain the pool when a run closes"})

    assert code == 0
    assert json.loads(out)["hits"] == []
    assert walks == [state]


@pytest.mark.parametrize("count", [2, 32])
def test_classify_reads_each_unchanged_document_once_for_all_rows(home: Path, package: Path, state: Path, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    prompts = [capture(f"{number:x}", f"no parked worker here {number}") for number in range(1, count + 1)]
    for number, prompt in enumerate(prompts):
        prompt["digest"] = f"{number:016x}"
    captured(state, *prompts)
    shell = RegisterShell(seeded(package))
    files = rulings.default_files(rulings.Register(shell, REPO, "brook", state, REGISTER_LEDGER, seeded(package)), package / "plan" / "plan.md")
    read = Path.read_bytes
    reads: list[Path] = []

    def counted(path: Path) -> bytes:
        if path in files:
            reads.append(path)
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    code, out = classify(tmp_path, capsys, shell, *({"digest": prompt["digest"], "extends": "rule/aaaaaaa", "words": prompt["text"]} for prompt in prompts))

    print(json.dumps({"rows": count, "corpus_bytes": sum(path.stat().st_size for path in files), "logical_read_bytes": sum(path.stat().st_size for path in reads), "document_reads": len(reads), "legacy_document_reads": count * len(files)}))
    assert code == 0
    assert len(out.splitlines()) == count
    assert reads == files


@pytest.mark.parametrize("name,text", [("parked worker", "A parked worker waits."), ("worker", "Workers wait; workeres wait."), ("skip", "skİp"), ("skip", "skıp"), ("skıp", "skip"), ("s", "ſ"), ("ſ", "s"), ("k", "K"), ("K", "k"), ("σ", "ς"), ("ς", "σ"), ("+", "Use + here."), ("relay-server", "Keep relay-server small."), ("a.b", "Keep a.b here.")])
def test_indexed_document_hits_equal_plain_sweeper_for_every_name_form(tmp_path: Path, name: str, text: str) -> None:
    path = tmp_path / "brief.md"
    path.write_text(f"Unrelated words.\n{text}\n/path/{name}\n~~{name}~~\n")
    loaded = rulings.Register(RegisterShell({}), REPO, "brook", tmp_path, REGISTER_LEDGER, {})
    rows = {"rule/one": {"names": json.dumps([name])}}
    documents = rulings.DocumentIndex(rulings.sweep_words, rulings.sweep_starts([name]))

    assert rulings.Sweeper(loaded, rows, documents).files([path]) == rulings.Sweeper(loaded, rows).files([path])


def test_indexed_documents_refresh_replaced_inplace_deleted_and_binary_content(tmp_path: Path) -> None:
    path = tmp_path / "brief.md"
    path.write_text("worker first\n")
    index = rulings.DocumentIndex(rulings.sweep_words, {"worker"})
    assert index.lines(path, {"worker"}) == [(1, "worker first")]
    path.write_text("worker newer\n")
    assert index.lines(path, {"worker"}) == [(1, "worker newer")]
    staged = tmp_path / "replacement"
    staged.write_text("worker final\n")
    staged.replace(path)
    assert index.lines(path, {"worker"}) == [(1, "worker final")]
    path.write_bytes(b"worker\0binary")
    assert index.lines(path, {"worker"}) == []
    path.unlink()
    assert index.lines(path, {"worker"}) == []


def test_document_reuse_keeps_each_rows_current_authorizers(tmp_path: Path) -> None:
    path = tmp_path / "brief.md"
    path.write_text("Keep worker here (1234567).\n")
    loaded = rulings.Register(RegisterShell({}), REPO, "brook", tmp_path, REGISTER_LEDGER, {})
    rows = {"rule/one": {"names": '["worker"]'}}
    documents = rulings.DocumentIndex(rulings.sweep_words, {"worker", "workers", "workeres"})
    assert rulings.Sweeper(loaded, rows, documents).files([path])[0].status == "open"
    loaded.rows["rule/two"] = {"kind": "order", "answer": '["1234567"]', "words": "Keep worker here"}
    assert rulings.Sweeper(loaded, rows, documents).files([path])[0].status == "authorized 1234567"


def test_briefs_skip_a_copied_source_tree_and_anything_below_three_levels(state: Path) -> None:
    (state / "briefs").mkdir()
    (state / "briefs" / "lane-a.md").write_text("Brief.\n")
    (state / "lane-b" / "sub").mkdir(parents=True)
    (state / "lane-b" / "sub" / "agent-brief.md").write_text("Brief.\n")
    copied = state / "finder-1" / "review-source"
    (copied / "reference").mkdir(parents=True)
    (copied / ".claude-plugin").mkdir()
    (copied / "reference" / "orca-lane-brief.md").write_text("Not a drive brief.\n")
    (state / "desk" / "pr-1" / "head" / "tree").mkdir(parents=True)
    (state / "desk" / "pr-1" / "head" / "tree" / "brief.md").write_text("Too deep.\n")

    assert rulings.briefs(state) == [state / "briefs" / "lane-a.md", state / "lane-b" / "sub" / "agent-brief.md"]


@pytest.mark.parametrize("at", [AT, "2026-10-23 5:30 AM PT"])
def test_classify_reads_a_capture_stamped_in_iso_or_in_the_hooks_older_pacific_form(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys, at: str
) -> None:
    captured(state, capture("4", "uploads stay out of the desktop shell. keep them in the journal") | {"at": at})
    shell = RegisterShell(seeded(package))
    three = [
        {"text": "The desktop shell gains no uploader.", "direction": "removes"},
        {"text": "Shell upload code is deleted.", "direction": "removes"},
        {"text": "The journal may keep an upload queue.", "direction": "permits"},
    ]

    code, out = classify(tmp_path, capsys, shell, {"digest": "4" * 16, "kind": "scope", "words": "uploads stay out of the desktop shell.", "implications": three, "names": ["desktop shell"]})

    assert code == 0
    assert json.loads(out)["key"] == PROMPT_KEY
    assert shell.rows[PROMPT_KEY]["at"] == "Oct 23 5:30 AM PT"


def fidelity_desks(state: Path) -> None:
    (state / "runner.json").write_text(json.dumps({"landing": {"fidelity_lane": ["fidelity-desk", "fidelity-desk-2"]}}))


def sweep_posts(shell: RegisterShell, state: Path) -> list[list[str]]:
    return [argv for argv in shell.calls if argv[:2] == ["cci", "post"] and "--path" in argv and Path(argv[argv.index("--path") + 1]).parent == state / "sweep" / "repeats"]


def test_a_repeat_extends_its_row_counts_it_and_posts_its_sweep_hits_to_the_fidelity_desks(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    fidelity_desks(state)
    (state / "briefs").mkdir()
    (state / "briefs" / "lane-a.md").write_text("Run a parked worker on the relay hook.\n")
    captured(state, capture("5", "how many times: no parked worker on relay-server, and no relay hook either"))
    shell = RegisterShell(seeded(package), records=LIVE)
    entry = {
        "digest": "5" * 16,
        "extends": "rule/aaaaaaa",
        "words": "no parked worker on relay-server, and no relay hook either",
        "names": ["relay hook"],
        "implications": [{"text": "relay-server exposes no relay hook.", "direction": "removes"}],
    }

    code, out = classify(tmp_path, capsys, shell, entry)

    assert code == 0
    assert set(shell.rows) == set(seeded(package))
    row = shell.rows["rule/aaaaaaa"]
    assert row["said"] == "2"
    assert json.loads(row["names"]) == ["relay-server", "parked worker", "relay hook"]
    assert [(item["n"], item["text"]) for item in json.loads(row["implications"])][-1] == (4, "relay-server exposes no relay hook.")
    assert json.loads(row["repeats"]) == [
        {"key": PROMPT_KEY, "answer": [], "at": "Oct 23 5:30 AM PT", "words": "how many times: no parked worker on relay-server, and no relay hook either", "proof": f"proof:prompt:{'5' * 16}", "repeat": True}
    ]
    brief = state / "briefs" / "lane-a.md"
    hits = json.loads(out)["hits"]
    assert f"open rule/aaaaaaa {brief}:1 parked worker" in hits and f"open rule/aaaaaaa {brief}:1 relay hook" in hits
    [swept] = sweep_posts(shell, state)
    assert [swept[index + 1] for index, flag in enumerate(swept) if flag == "--to"] == ["fidelity-desk", "fidelity-desk-2"]
    posted = Path(swept[swept.index("--path") + 1]).read_text().splitlines()
    assert posted[0].startswith("The owner repeated rule/aaaaaaa.") and posted[1:] == hits
    assert not list((state / "register").glob("repeat-*.json"))


class RefusingPostShell(RegisterShell):
    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if argv[:2] == ["cci", "post"] and "--path" in argv:
            self.calls.append(argv)
            raise subprocess.CalledProcessError(1, argv, "", "cci: no live lane fidelity-desk-15")
        return super().run(argv, stdin, cwd)


def test_a_repeat_whose_sweep_post_cci_refuses_warns_and_finishes_the_classify(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    """register-seed-6 #203695: cci refused the sweep post to fidelity desks that had not started, and the CalledProcessError aborted classify mid-batch."""
    fidelity_desks(state)
    captured(state, capture("5", "how many times: no parked worker on relay-server, and no relay hook either"))
    shell = RefusingPostShell(seeded(package), records=LIVE)
    entry = {"digest": "5" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker on relay-server, and no relay hook either", "names": ["relay hook"]}

    classified = tmp_path / "classified.json"
    classified.write_text(json.dumps([entry]))

    code = rulings.main(["classify", "--program", "brook", "--repo", REPO, str(classified)], shell)

    said = capsys.readouterr()
    assert code == 0, said.out
    assert shell.rows["rule/aaaaaaa"]["said"] == "2"
    [swept] = sweep_posts(shell, state)
    assert "--force" in swept
    assert "WARN the sweep hits of rule/aaaaaaa did not post to the fidelity desks (cci: no live lane fidelity-desk-15)" in said.err


def test_a_repeat_in_a_drive_with_no_landing_runner_writes_its_sweep_hits_and_posts_them_to_no_one(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    captured(state, capture("5", "how many times: no parked worker on relay-server, and no relay hook either"))
    shell = RegisterShell(seeded(package), records=LIVE)
    entry = {"digest": "5" * 16, "extends": "rule/aaaaaaa", "words": "no parked worker on relay-server, and no relay hook either", "names": ["relay hook"]}

    code, out = classify(tmp_path, capsys, shell, entry)

    assert code == 0, out
    assert shell.rows["rule/aaaaaaa"]["said"] == "2"
    assert (state / "sweep" / "repeats" / "rule-aaaaaaa.txt").read_text().startswith("The owner repeated rule/aaaaaaa.")
    assert sweep_posts(shell, state) == []


def test_a_repeat_folds_onto_an_order_row_whose_own_names_and_repeat_words_it_carries(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    fidelity_desks(state)
    words = "how many times: use your judgement and act like a frontier model, not a literal machine"
    captured(state, capture("7", words))
    register = seeded(package) | {
        "rule/fffffff": {
            "kind": "order",
            "status": "in-force",
            "global": "yes",
            "words": "Use your judgement, act like a frontier model.",
            "answer": '["fffffff"]',
            "at": "Oct 23 9:00 AM PT",
            "said": "1",
            "names": '["frontier model", "literal machine"]',
            "steps": '["all"]',
            "lanes": '["all"]',
            "paths": "none",
        }
    }
    shell = RegisterShell(register, records=LIVE)

    code, out = classify(tmp_path, capsys, shell, {"digest": "7" * 16, "extends": "rule/fffffff", "words": words})

    assert code == 0, out
    row = shell.rows["rule/fffffff"]
    assert row["kind"] == "order"
    assert row["said"] == "2"
    assert [(repeat["words"], repeat["repeat"]) for repeat in json.loads(row["repeats"])] == [(words, True)]


@pytest.mark.parametrize("again", ["again", "agajn", "agian", "agin", "aagin"])
def test_an_extension_opened_with_and_again_or_its_one_edit_misspelling_is_a_repeat(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys, again: str
) -> None:
    fidelity_desks(state)
    words = f"and {again} fkr sikans foxes we dont want to preserve rheir shoae ... use your brain and best judgement here do not be a retarded machine act like a frontier model"
    captured(state, capture("8", words))
    shell = RegisterShell(seeded(package), records=LIVE)

    code, out = classify(tmp_path, capsys, shell, {"digest": "8" * 16, "extends": "rule/aaaaaaa", "words": words})

    assert code == 0, out
    assert shell.rows["rule/aaaaaaa"]["said"] == "2"
    assert [repeat["repeat"] for repeat in json.loads(shell.rows["rule/aaaaaaa"]["repeats"])] == [True]


def test_and_gain_is_no_repeat_marker(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    fidelity_desks(state)
    words = "keep the parked worker off relay-server and gain the headroom back"
    captured(state, capture("9", words))
    shell = RegisterShell(seeded(package), records=LIVE)

    code, out = classify(tmp_path, capsys, shell, {"digest": "9" * 16, "extends": "rule/aaaaaaa", "words": words})

    assert code == 0, out
    assert shell.rows["rule/aaaaaaa"]["said"] == "1"


def test_an_extension_without_repeat_words_is_no_repeat_and_posts_no_sweep(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    fidelity_desks(state)
    captured(state, capture("6", "also the parked worker stays off relay-server's health probe"))
    shell = RegisterShell(seeded(package), records=LIVE)
    entry = {
        "digest": "6" * 16,
        "extends": "rule/aaaaaaa",
        "words": "also the parked worker stays off relay-server's health probe",
        "implications": [{"text": "relay-server's health probe starts no parked worker.", "direction": "removes"}],
    }

    code, _ = classify(tmp_path, capsys, shell, entry)

    assert code == 0
    assert shell.rows["rule/aaaaaaa"]["said"] == "1"
    assert [repeat["repeat"] for repeat in json.loads(shell.rows["rule/aaaaaaa"]["repeats"])] == [False]
    assert sweep_posts(shell, state) == []


def test_names_removes_a_name_and_a_wrongly_attached_repeat_citing_the_answer(home: Path, package: Path, state: Path, capsys) -> None:
    rows = seeded(package)
    rows["rule/aaaaaaa"] |= {
        "said": "2",
        "repeats": json.dumps(
            [
                {"key": "prompt/1023-090000", "answer": [], "at": "Oct 23 9:00 AM PT", "words": "no parked worker", "proof": "proof:prompt:1"},
                {"key": "prompt/1023-091000", "answer": [], "at": "Oct 23 9:10 AM PT", "words": "the parked worker again", "proof": "proof:prompt:2", "repeat": False},
            ]
        ),
    }
    shell = RegisterShell(rows)

    code, out = ran(
        capsys, shell, "names", "rule/aaaaaaa", "--remove", "parked worker", "--drop-repeat", "prompt/1023-090000", "--drop-repeat", "prompt/1023-091000", "--why", "c4cdc50"
    )

    assert (code, out) == (0, "rule/aaaaaaa narrowed under c4cdc50: removed parked worker, prompt/1023-090000, prompt/1023-091000\n")
    row = shell.rows["rule/aaaaaaa"]
    assert (json.loads(row["names"]), json.loads(row["repeats"]), row["said"]) == (["relay-server"], [], "1")
    [narrowed] = json.loads(row["narrowed"])
    assert (narrowed["removed"], narrowed["repeats"], narrowed["why"]) == (["parked worker"], ["prompt/1023-090000", "prompt/1023-091000"], "c4cdc50")


def test_names_refuses_a_name_the_row_does_not_carry(home: Path, package: Path, state: Path, capsys) -> None:
    shell = RegisterShell(seeded(package))

    assert ran(capsys, shell, "names", "rule/aaaaaaa", "--remove", "relay hook", "--why", "c4cdc50") == (1, "NOT-ON-ROW rule/aaaaaaa: relay hook is not on the row\n")
    assert shell.writes() == []


def queue_card(state: Path, key: str) -> None:
    (state / "cards").mkdir(exist_ok=True)
    card = {"status": "needs_you", "title": f"\u201c{key}\u201d", "body": f"This also means:\n1. {key} holds.", "options": rulings.CARD_OPTIONS, "waits": rulings.card_id(key)}
    (state / "cards" / f"{rulings.card_id(key)}.json").write_text(json.dumps(card))


def test_round_puts_at_most_ten_rulings_on_the_board_foundational_first_and_skips_the_owners_own_picks(
    home: Path, package: Path, state: Path, capsys
) -> None:
    rows = seeded(package) | {f"prompt/1023-0600{index:02d}": {"kind": "order", "status": "in-force", "words": f"Order {index}."} for index in range(9)}
    shell = RegisterShell(rows)
    for key in ["rule/bbbbbbb", "rule/eeeeeee", *(f"prompt/1023-0600{index:02d}" for index in range(9)), "exception/E1", "rule/aaaaaaa"]:
        queue_card(state, key)

    code, out = ran(capsys, shell, "round")

    taken = ["rule/aaaaaaa", "exception/E1", "rule/eeeeeee", *(f"prompt/1023-0600{index:02d}" for index in range(7))]
    assert (code, out) == (0, "round 1: 10 rulings on the board, foundational rows first\n")
    assert json.loads((state / "cards" / "rounds" / "round-1.json").read_text()) == {"round": 1, "keys": taken}
    presented = [argv for argv in shell.calls if argv[0] == "cc-present"]
    assert presented[0] == ["cc-present", "round", "--title", "Your rulings, read back: round 1"]
    blocks = [json.loads(Path(argv[2]).read_text()) for argv in presented[1:]]
    assert [[*argv[:2], *argv[3:]] for argv in presented[1:]] == [["cc-present", "update-block", "--round", "current"]] * len(taken)
    assert [block["id"] for block in blocks] == [rulings.card_id(key) for key in taken]
    assert blocks[0]["title"] == "Did I read \u201crule/aaaaaaa\u201d right?"
    assert blocks[0]["children"] == [
        {"id": "rule:aaaaaaa-context", "type": "markdown", "md": "This also means:\n1. rule/aaaaaaa holds."},
        {"id": "rule:aaaaaaa-answer", "type": "choice", "prompt": "Is this what you meant?", "options": rulings.ROUND_PICKS},
    ]
    assert sorted(path.name for path in (state / "cards").glob("*.json")) == ["prompt:1023-060007.json", "prompt:1023-060008.json"]
    assert [path.name for path in (state / "cards" / "rounds" / "skipped").iterdir()] == ["rule:bbbbbbb.json"]


def test_a_round_card_quoting_a_long_sentence_stays_inside_the_ask_title_limit() -> None:
    card = {"waits": "rule:aaaaaaa", "title": "\u201c" + "w" * 200 + "\u201d", "body": "You said: it."}
    title = rulings.ask_card(card)["title"]
    assert len(title) <= 140 and title.endswith("\u2026\u201d right?")


def test_round_with_no_queued_card_opens_no_round(home: Path, package: Path, state: Path, capsys) -> None:
    shell = RegisterShell(seeded(package))

    assert ran(capsys, shell, "round") == (0, "no ruling card queues\n")
    assert not [argv for argv in shell.calls if argv[0] == "cc-present"]


def test_a_right_pick_on_a_round_card_ticks_every_line(home: Path, package: Path, state: Path, capsys) -> None:
    block = rulings.answer_id(rulings.card_id("rule/aaaaaaa"))
    captured(state, capture("4", "Right", source="board", block=block))
    shell = RegisterShell(seeded(package))

    code, _ = ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "4" * 16, "--tick", "all")

    assert code == 0
    assert {item["state"] for item in json.loads(shell.rows["rule/aaaaaaa"]["implications"])} == {"ticked"}


def test_an_exception_without_owner_words_naming_its_mechanism_waits_for_the_owner(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("7", "the cleaner may run nightly"))
    shell = RegisterShell(seeded(package))

    code, _ = classify(
        tmp_path,
        capsys,
        shell,
        {"digest": "7" * 16, "key": "exception/E9", "kind": "exception", "words": "the cleaner may run nightly", "names": ["sweep pass"]},
    )

    assert code == 0
    assert shell.rows["exception/E9"]["status"] == "pending-owner"


def test_classify_records_a_no_line_and_lists_only_unclassified_captures(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    captured(state, capture("8", "thanks"), capture("9", "the shell never uploads"), capture("0", "noted"))
    shell = RegisterShell(seeded(package))

    code, out = classify(tmp_path, capsys, shell, {"digest": "8" * 16, "no": "no ruling"})

    assert (code, json.loads(out)) == (0, {"digest": "8" * 16, "no": "no ruling"})
    assert shell.rows[f"no/{'8' * 16}"] == {"no": "no ruling", "at": AT}
    code, out = ran(capsys, shell, "classify")
    assert [json.loads(line)["digest"] for line in out.splitlines()] == ["9" * 16, "0" * 16]
    assert classify(tmp_path, capsys, shell, {"digest": "8" * 16, "no": "again"}) == (
        1,
        f"CLASSIFIED {'8' * 16}: this capture is already in the register or earlier in this batch\n",
    )


def test_a_relayed_capture_the_owner_repeated_in_root_chat_leaves_the_unclassified_list(home: Path, package: Path, state: Path, capsys) -> None:
    captured(state, capture("8", "the shell never uploads", source="relay"), capture("9", "the shell never uploads, ever") | {"supersedes": ["8" * 16]})

    code, out = ran(capsys, RegisterShell(seeded(package)), "classify")

    assert (code, [json.loads(line)["digest"] for line in out.splitlines()]) == (0, ["9" * 16])


def test_implications_show_what_binds_now_and_what_waits_for_a_tick(home: Path, package: Path, state: Path, capsys) -> None:
    code, out = ran(capsys, RegisterShell(seeded(package)), "implications", "rule/aaaaaaa")

    assert (code, out.splitlines()) == (
        0,
        [
            "1. [removes, proposed, binds now] No relay code lands in relay-server.",
            "2. [removes, proposed, binds now] relay-server gains no plug-in point for relay code.",
            "3. [permits, proposed, waits for the owner's tick] Relay cleanup may live in the desktop app.",
        ],
    )


def test_confirm_takes_only_a_right_tap_or_a_reply_naming_the_lines_on_the_rows_card(home: Path, package: Path, state: Path, capsys) -> None:
    captured(
        state,
        capture("b", "Right", source="slack-action", block="rule:aaaaaaa"),
        capture("c", "sounds good"),
        capture("e", "Some wrong", source="slack-action", block="rule:aaaaaaa"),
        capture("f", "2 is wrong, 1 and 3 stand", source="slack-reply", block="rule:aaaaaaa"),
        capture("7", "Right (Recommended)", source="slack-action", block="rule:aaaaaaa"),
        capture("8", "Other", source="slack-action", block="rule:aaaaaaa"),
    )
    shell = RegisterShell(seeded(package))

    assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "c" * 16, "--tick", "all") == (
        1,
        f"CAPTURE-NOT-ON-CARD {'c' * 16}: confirm takes only a captured tap or reply on rule/aaaaaaa's card\n",
    )
    assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "e" * 16, "--strike", "2")[1].startswith(f"CONFIRM-TAP {'e' * 16}: ")
    assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "b" * 16, "--tick", "1", "--strike", "2")[1].startswith(f"CONFIRM-TAP {'b' * 16}: ")
    assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "f" * 16, "--tick", "all")[1].startswith(f"CONFIRM-UNNAMED {'f' * 16}: ")
    assert shell.writes() == []

    code, out = ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "f" * 16, "--tick", "1,3", "--strike", "2")

    assert code == 0
    assert [(item["state"], item["proof"]) for item in json.loads(shell.rows["rule/aaaaaaa"]["implications"])] == [
        ("ticked", "f" * 16),
        ("struck", "f" * 16),
        ("ticked", "f" * 16),
    ]
    assert out.splitlines()[2] == "3. [permits, ticked, binds now] Relay cleanup may live in the desktop app."

    assert ran(capsys, RegisterShell(seeded(package)), "confirm", "rule/aaaaaaa", "--capture", "b" * 16, "--tick", "all")[0] == 0
    assert ran(capsys, RegisterShell(seeded(package)), "confirm", "rule/aaaaaaa", "--capture", "7" * 16, "--tick", "all")[0] == 0
    assert ran(capsys, RegisterShell(seeded(package)), "confirm", "rule/aaaaaaa", "--capture", "8" * 16, "--tick", "all")[1].startswith(f"CONFIRM-TAP {'8' * 16}: ")


OWNER_CORRECTION = "mo tnose ones are fine to ask me asshole i mesnt anout the regressikn tests"


def test_confirm_takes_an_owner_chat_capture_classified_onto_the_row_and_refuses_one_classified_onto_another(
    home: Path, package: Path, state: Path, tmp_path: Path, capsys
) -> None:
    captured(state, capture("1", OWNER_CORRECTION), capture("2", "cards quote the words") | {"digest": "fedcba9876543210"})
    shell = RegisterShell(seeded(package))
    assert classify(tmp_path, capsys, shell, {"digest": "1" * 16, "extends": "rule/aaaaaaa", "words": "i mesnt anout the regressikn tests"})[0] == 0

    for row in ("rule/ccccccc", "rule/aaaaaaa"):
        shell.rows[row]["proof"] = "proof:prompt:fedcba9876543210"
        assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "fedcba9876543210", "--strike", "3") == (
            1,
            "CAPTURE-NOT-ON-CARD fedcba9876543210: confirm takes only a captured tap or reply on rule/aaaaaaa's card\n",
        )
    for strike in ([], ["--strike", "4"]):
        assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "1" * 16, *strike, "--implication", "Only test cleanup.")[1].startswith(f"CONFIRM-NARROW {'1' * 16}: ")

    narrowed = "Only relay test cleanup may live in the desktop app."
    code, out = ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "1" * 16, "--strike", "3", "--implication", narrowed)

    assert code == 0
    assert out.splitlines()[2:] == [
        "3. [permits, struck, struck] Relay cleanup may live in the desktop app.",
        f"4. [permits, proposed, waits for the owner's tick] {narrowed}",
    ]
    struck = json.loads(shell.rows["rule/aaaaaaa"]["implications"])[2]
    assert (struck["proof"], struck["words"]) == ("1" * 16, OWNER_CORRECTION)


def test_a_lane_slice_carries_every_global_row_and_every_matched_row_verbatim(home: Path, package: Path, state: Path, capsys) -> None:
    code, out = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-a", "--step", "1.9")

    assert code == 0
    assert out.startswith("Register slice: lane=lane-a step=1.9\n")
    assert hashed_correctly(out)
    lines = out.splitlines()
    for line in (
        "### rule/aaaaaaa | scope | in-force | said 1 | Oct 21 9:15 PM PT",
        "> Relay code lives in relay-journal. Never in relay-server.",
        "This also means:",
        "1. No relay code lands in relay-server. (removes, proposed, binds now)",
        "3. Relay cleanup may live in the desktop app. (permits, proposed, waits for the owner's tick)",
        "Names: relay-server, parked worker",
        "Surfaces: relay-server, relay-journal",
        "Tests: C01",
        "### exception/E1 | exception | pending-owner | said 1 | ",
        "### rule/eeeeeee | gate | at-risk | said 1 | Oct 18 9:03 PM PT",
        "### rule/ccccccc | pick | in-force | said 1 | Oct 22 1:00 PM PT",
        "> Cards quote the words they ask about.",
        "9. **Register**: cards quote the words; a parked worker is never kept.",
        f"open rule/aaaaaaa {package / 'plan' / 'plan.md'}:5 parked worker",
        f"authorized 9999999 rule/aaaaaaa {package / 'plan' / 'plan.md'}:6 relay-server",
    ):
        assert line in lines
    assert "10. Next item." not in lines
    assert "rule/bbbbbbb" not in out
    assert "rule/ddddddd" not in out


def test_a_slice_row_says_what_it_protects_and_when_it_does_not_apply(home: Path, package: Path, state: Path, capsys) -> None:
    """Root #204765: the 5:41 PM idle rule was applied to work that shares nothing with a request, because the row carried his words alone."""
    rows = seeded(package)
    rows["rule/aaaaaaa"] |= {"protects": "the relay hot path from code it does not need", "not_when": "a test fixture names relay-server"}

    _, out = ran(capsys, RegisterShell(rows), "slice", "--lane", "lane-a", "--step", "1.9")

    lines = out.splitlines()
    words = lines.index("> Relay code lives in relay-journal. Never in relay-server.")
    assert lines[words + 1 : words + 3] == ["Protects: the relay hot path from code it does not need", "Does not apply when: a test fixture names relay-server"]


def test_a_lane_slice_matches_anchored_rows_by_the_lanes_paths(home: Path, package: Path, state: Path, capsys) -> None:
    _, out = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-b", "--step", "2", "--paths", "src/relay/*")

    assert out.startswith("Register slice: lane=lane-b step=2 paths=src/relay/*\n")
    assert "### rule/bbbbbbb | pick | in-force | said 1 | Oct 20 3:00 PM PT" in out.splitlines()


def test_a_lane_slice_matches_a_row_anchored_by_glob_to_a_lanes_concrete_path(home: Path, package: Path, state: Path, capsys) -> None:
    rows = seeded(package)
    rows["rule/bbbbbbb"]["paths"] = '["src/relay/*.rs"]'

    _, out = ran(capsys, RegisterShell(rows), "slice", "--lane", "lane-b", "--step", "2", "--paths", "src/relay/pool.rs")

    assert "### rule/bbbbbbb | pick | in-force | said 1 | Oct 20 3:00 PM PT" in out.splitlines()


def test_the_root_slice_carries_every_scope_row(home: Path, package: Path, state: Path, capsys) -> None:
    _, out = ran(capsys, RegisterShell(seeded(package)), "slice", "--root")

    assert out.startswith("Register slice: lane=root step=all\n")
    assert hashed_correctly(out)
    assert "> Relay code lives in relay-journal. Never in relay-server." in out.splitlines()
    assert "exception/E1" not in out
    assert "rule/ccccccc" not in out


def test_an_incident_slice_carries_every_global_row_and_the_unclassified_captures(home: Path, package: Path, state: Path, capsys) -> None:
    captured(state, capture("9", "the shell never uploads"), capture("0", "chatter"))
    shell = RegisterShell(seeded(package) | {"no/" + "0" * 16: {"no": "chatter", "at": AT}})

    _, out = ran(capsys, shell, "slice", "--lane", "fix-relay", "--step", "incident")

    lines = out.splitlines()
    assert lines[0] == "Register slice: lane=fix-relay step=incident"
    assert "### rule/aaaaaaa | scope | in-force | said 1 | Oct 21 9:15 PM PT" in lines
    assert "## Owner captures not yet classified (1)" in lines
    assert "> the shell never uploads" in lines
    assert "rule/ccccccc" not in out
    assert "rule/eeeeeee" not in out


def test_refresh_fills_a_bare_slice_line_and_rewrites_only_a_stale_slice(home: Path, package: Path, state: Path, capsys) -> None:
    brief = state / "briefs" / "lane-a.md"
    brief.parent.mkdir()
    brief.write_text("Brief for lane-a.\nRegister slice: lane=lane-a step=1.9\n\nDo the work.\n")
    rows = seeded(package)

    code, out = ran(capsys, RegisterShell(rows), "slice", "--refresh", str(brief))
    first = brief.read_text()
    fresh = rulings.SLICE_HASH.search(first)["hash"]

    assert (code, out) == (0, f"refreshed {brief} slice-hash none -> {fresh}\n")
    assert first.startswith("Brief for lane-a.\nRegister slice: lane=lane-a step=1.9\n")
    assert first.endswith(f"slice-hash: {fresh}\n\nDo the work.\n")
    assert ran(capsys, RegisterShell(rows), "slice", "--refresh", str(brief)) == (0, f"fresh {brief} slice-hash {fresh}\n")
    brief.write_text(f"Brief for lane-a.\nRegister slice: lane=lane-a step=1.9\nslice-hash: {fresh}\n\nDo the work.\n")
    assert ran(capsys, RegisterShell(rows), "slice", "--refresh", str(brief)) == (0, f"refreshed {brief} slice-hash {fresh} -> {fresh}\n")
    assert brief.read_text() == first

    rows["rule/ccccccc"]["words"] = "Cards quote the words they ask about, verbatim."
    code, out = ran(capsys, RegisterShell(rows), "slice", "--refresh", str(brief))
    second = brief.read_text()

    assert out == f"refreshed {brief} slice-hash {fresh} -> {rulings.SLICE_HASH.search(second)['hash']}\n"
    assert second.count("Register slice:") == 1
    assert second.count("slice-hash:") == 1
    assert "> Cards quote the words they ask about, verbatim." in second.splitlines()


def required(out: str) -> list[str]:
    return re.findall(r"^### (\S+) \|", out, re.MULTILINE)


def test_the_required_slice_prints_every_global_pending_and_touched_row_with_its_words_implications_and_names(home: Path, package: Path, state: Path, capsys) -> None:
    rows = seeded(package) | {"rule/1234567": {"kind": "order", "status": "in-force", "words": "Log every retry.", "paths": "pending", "lanes": '["all"]'}}

    code, out = ran(capsys, RegisterShell(rows, diff=DIFF), "slice", "--required", "--pr", "acme/app#12", "--head", "cafef00d", "--base", "main")
    lines = out.splitlines()

    assert code == 0
    assert hashed_correctly(out)
    assert lines[0] == "Required rows for acme/app#12 at cafef00d"
    assert required(out) == ["exception/E1", "rule/1234567", "rule/aaaaaaa", "rule/bbbbbbb"]
    assert "> Relay code lives in relay-journal. Never in relay-server." in lines
    assert "1. No relay code lands in relay-server. (removes, proposed, binds now)" in lines
    assert "Names: relay-server, parked worker" in lines
    assert "> Log every retry." in lines


class SiblingBase(RegisterShell):
    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        match gateway(argv):
            case ["gh", "pr", "view", "12", "--repo", "acme/app", "--json", "baseRefName"]:
                return json.dumps({"baseRefName": "lane-b"})
            case ["gh", "api", "repos/acme/app/compare/lane-b...cafef00d", "-H", rulings.DIFF_MEDIA, "--allow-escape-sequences"]:
                return self.diff
            case ["gh", "api", "repos/acme/app/compare/main...cafef00d", "-H", rulings.DIFF_MEDIA, "--allow-escape-sequences"]:
                raise AssertionError("the required slice compared against main, not the PR's base branch")
        return super().run(argv, stdin, cwd)


def test_the_required_slice_compares_against_the_base_branch_the_pr_names(home: Path, package: Path, state: Path, capsys) -> None:
    code, out = ran(capsys, SiblingBase(seeded(package), diff=DIFF), "slice", "--required", "--pr", "acme/app#12", "--head", "cafef00d")

    assert code == 0
    assert "rule/bbbbbbb" in required(out)


@pytest.mark.parametrize("pr", ["acme/app#12", "12"])
def test_the_required_slice_reads_the_drive_register_whatever_repo_the_caller_names(home: Path, package: Path, state: Path, registered: Path, capsys, pr: str) -> None:
    code, out = ran(capsys, RegisterShell(seeded(package), diff=DIFF), "slice", "--required", "--pr", pr, "--head", "cafef00d", "--repo", "acme/app")

    assert code == 0
    assert "rule/aaaaaaa" in required(out)


FIXTURES_DIFF = (
    b"diff --git a/fixtures/page.png b/fixtures/page.png\nnew file mode 100644\nindex 0000000..3333333\nBinary files /dev/null and b/fixtures/page.png differ\n"
    b"diff --git a/fixtures/blob b/fixtures/blob\nnew file mode 100644\nindex 0000000..4444444\n--- /dev/null\n+++ b/fixtures/blob\n@@ -0,0 +1 @@\n+\xb5\xff\n"
)


class LargeDiff(RegisterShell):
    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        match gateway(argv):
            case ["gh", "api", path, "-H", _, "--allow-escape-sequences"] if "/compare/" in path:
                raise subprocess.CalledProcessError(1, argv, stderr="HTTP 406: Sorry, the diff exceeded the maximum number of files (300).")
            case ["git", "-C", "/repo", "diff", *_, output, spec] if spec == f"{BASE_TIP}...{FULL_HEAD}":
                Path(output.removeprefix("--output=")).write_bytes(DIFF.encode() + FIXTURES_DIFF)
                self.calls.append(argv)
                return ""
        return super().run(argv, stdin, cwd)


FULL_HEAD = "cafef00d" + "0" * 32
BASE_TIP = "ba5e0000" + "0" * 32


def test_the_drive_checkout_diff_fetches_the_full_sha_an_abbreviated_head_names(home: Path, package: Path, state: Path, registered: Path, capsys) -> None:
    """long-running 0.7.257 on Oct 23: the landing runner passes check-pr a 10-character head, and git fetch refuses an abbreviated sha, so every head read nothing."""
    shell = RegisterShell(seeded(package), diff=DIFF)

    code, out = ran(capsys, shell, "slice", "--required", "--pr", "acme/app#12", "--head", "cafef00d", "--base", "main")

    assert code == 0
    assert "rule/bbbbbbb" in required(out)
    assert ["git", "-C", REPO, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", BASE_TIP, FULL_HEAD] in shell.calls


def test_the_drive_checkout_diff_fetches_nothing_the_checkout_already_holds(home: Path, package: Path, state: Path, registered: Path, capsys) -> None:
    """Oct 27: every slice and grade fetched the base and head again, 17 to 100 seconds each in the shared monorepo checkout, although the lanes pushed both from worktrees of that same repository."""
    shell = RegisterShell(seeded(package), diff=DIFF, present=frozenset({BASE_TIP, FULL_HEAD}))

    code, out = ran(capsys, shell, "slice", "--required", "--pr", "acme/app#12", "--head", FULL_HEAD, "--base", "main")

    assert code == 0
    assert "rule/bbbbbbb" in required(out)
    assert not [argv for argv in shell.calls if "fetch" in argv]


def test_the_required_slice_diffs_a_large_pr_in_the_drive_checkout_and_counts_a_binary_file_by_name(home: Path, package: Path, state: Path, registered: Path, capsys) -> None:
    rows = seeded(package) | {"rule/2345678": {"kind": "order", "status": "in-force", "words": "Keep page fixtures small.", "paths": '["fixtures/page.png"]'}}
    shell = LargeDiff(rows)

    code, out = ran(capsys, shell, "slice", "--required", "--pr", "acme/app#12", "--head", "cafef00d", "--base", "main")

    assert code == 0
    assert {"rule/bbbbbbb", "rule/2345678"} <= set(required(out))


def test_sweep_reads_the_drive_register_whatever_checkout_repo_names(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    tree = tmp_path / "cc-skills"
    tree.mkdir()
    (tree / "notes.md").write_text("Keep the parked worker.\n")

    assert ran(capsys, RegisterShell(seeded(package)), "sweep", "--repo", str(tree), "--paths", "*.md") == (0, "open rule/aaaaaaa notes.md:1 parked worker\n")


def test_the_required_slice_includes_a_row_whose_symbol_a_caller_elsewhere_adds(home: Path, package: Path, state: Path, capsys) -> None:
    caller = "diff --git a/src/caller.rs b/src/caller.rs\n--- a/src/caller.rs\n+++ b/src/caller.rs\n@@ -1,1 +1,2 @@\n fn main() {\n+    pool.drain();\n"

    _, out = ran(capsys, RegisterShell(seeded(package), diff=caller), "slice", "--required", "--pr", "acme/app#12", "--head", "cafef00d", "--base", "main")

    assert "rule/bbbbbbb" in required(out)


def test_sweep_prints_each_hit_open_deleted_or_authorized(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    artifact = tmp_path / "card.md"
    artifact.write_text(
        "A parked worker waits on the relay-server.\n"
        "Parked workers are gone [STRUCK by the owner, answer 1234abc]; nothing else changed.\n"
        "Nothing of ours lands in relay-server (aaaaaaa).\n"
        "The relay-server keeps its health probe (9999999).\n"
        "~~old parked worker~~ keep the parked worker\n"
        "~~drop the parked worker~~ for good\n"
        "relay-servers2 is another word.\n"
    )

    code, out = ran(capsys, RegisterShell(seeded(package)), "sweep", "--artifact", str(artifact))

    assert (code, out.splitlines()) == (
        0,
        [
            f"open rule/aaaaaaa {artifact}:1 relay-server",
            f"open rule/aaaaaaa {artifact}:1 parked worker",
            f"deleted rule/aaaaaaa {artifact}:2 parked worker",
            f"open rule/aaaaaaa {artifact}:3 relay-server",
            f"authorized 9999999 rule/aaaaaaa {artifact}:4 relay-server",
            f"open rule/aaaaaaa {artifact}:5 parked worker",
            f"deleted rule/aaaaaaa {artifact}:6 parked worker",
        ],
    )


def test_sweep_hits_every_line_naming_a_scope_row_across_the_plan_boards_designs_and_briefs(home: Path, package: Path, state: Path, capsys) -> None:
    (package / "plan" / "plan.md").write_text("".join(f"relay-server line {n}\n" if n % 3 else f"plain line {n}\n" for n in range(1, 62)))
    (state / "briefs").mkdir()
    (state / "briefs" / "lane-a.md").write_text("Keep the parked worker.\n")

    _, out = ran(capsys, RegisterShell(seeded(package)), "sweep")

    hits = out.splitlines()
    assert len([hit for hit in hits if hit.endswith(" relay-server") and "plan.md" in hit]) == 41
    assert f"open rule/aaaaaaa {package / 'boards' / 'card-confirm.json.pre-spikes'}:1 parked worker" in hits
    assert f"open rule/aaaaaaa {package / 'designs' / 'relay' / 'design.md'}:1 relay-server" in hits
    assert f"open rule/aaaaaaa {state / 'briefs' / 'lane-a.md'}:1 parked worker" in hits


def test_sweep_skips_a_name_inside_a_path_or_cite(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    artifact = tmp_path / "plan.md"
    artifact.write_text(
        "0.6 checks `rust/crates/relay-server/tests/fork.rs` and spikes/parked-worker/findings.md.\n"
        "The relay-server keeps its hook.\n"
    )

    assert ran(capsys, RegisterShell(seeded(package)), "sweep", "--artifact", str(artifact)) == (0, f"open rule/aaaaaaa {artifact}:2 relay-server\n")


def test_sweep_of_a_diff_marks_removed_lines_deleted_and_added_lines_open(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    patch = tmp_path / "pr.diff"
    patch.write_text(DIFF.replace("+    self.drain();", "+    park(parked_worker, \"parked worker\");"))

    _, out = ran(capsys, RegisterShell(seeded(package)), "sweep", "--diff", str(patch))

    assert out.splitlines() == ["open rule/aaaaaaa src/relay/pool.rs:2 parked worker", "deleted rule/aaaaaaa src/relay/pool.rs:2 relay-server"]


def test_sweep_paths_resolve_under_root_and_an_absolute_glob_stands_alone(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    tree = tmp_path / "lane-tree"
    (tree / "notes").mkdir(parents=True)
    (tree / "notes" / "a.md").write_text("Keep the parked worker.\n")

    rooted = ran(capsys, RegisterShell(seeded(package)), "sweep", "--root", str(tree), "--paths", "notes/*.md")
    absolute = ran(capsys, RegisterShell(seeded(package)), "sweep", "--paths", str(tree / "notes" / "*.md"))

    assert rooted == (0, "open rule/aaaaaaa notes/a.md:1 parked worker\n")
    assert absolute == (0, f"open rule/aaaaaaa {tree / 'notes' / 'a.md'}:1 parked worker\n")


def test_readback_accepts_a_line_per_slice_row_and_a_verdict_per_hit(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    _, sliced = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-a", "--step", "1.9")
    current = rulings.SLICE_HASH.search(sliced)["hash"]
    hits = [line for line in sliced.splitlines() if line.startswith(("open ", "authorized "))]
    rows = ["rule/aaaaaaa: builds nothing in relay-server", "rule/ccccccc: quotes the words", "rule/eeeeeee: leaves the outage gate alone", "exception/E1: leaves the pass alone"]

    def read_back(*lines: str, lane: str = "lane-a") -> RegisterShell:
        body = tmp_path / "readback.md"
        body.write_text("\n".join(lines) + "\n")
        records = [{"seq": 501, "kind": "contract", "lane": lane, "text": "read-back", "at": AT}, {"seq": 500, "kind": "contract", "lane": lane, "text": "read-back", "refs": {"path": str(body)}, "at": AT}]
        return RegisterShell(seeded(package), records=records)

    marked = [f"{hit} - keep: the line states the ban" for hit in hits]
    header = ["Register slice: lane=lane-a step=1.9", f"slice-hash: {current}"]
    assert ran(capsys, read_back(*header, *rows, *marked), "readback", "--lane", "lane-a", "--contract", "500") == (
        0,
        f"readback request lane-a {current}: 4 rows, contract #500 at {tmp_path / 'readback.md'}; a fidelity desk grades it against each row's words and implications\n",
    )
    request = f"readback request lane-a {current}: 4 rows, contract #500 at {tmp_path / 'readback.md'}; a fidelity desk grades it against each row's words and implications\n"
    code, out = ran(capsys, read_back(*header, *rows[:2], *hits), "readback", "--lane", "lane-a", "--contract", "500")
    assert code == 0 and out.endswith(request)
    assert "WARNING READBACK-ROW-MISSING rule/eeeeeee: no read-back line names it" in out.splitlines()
    assert f"WARNING READBACK-HIT-UNMARKED {hits[0]}" in out.splitlines()
    assert ran(capsys, read_back("Register slice: lane=lane-a step=1.9", "slice-hash: 000000000000", *rows, *marked), "readback", "--lane", "lane-a", "--contract", "500") == (0, request)
    assert ran(capsys, read_back("Register slice: lane=lane-a step=1.9", *rows, *marked), "readback", "--lane", "lane-a", "--contract", "500") == (0, request)
    assert ran(capsys, read_back(*header, *rows), "readback", "--lane", "lane-a", "--contract", "500") == (
        0,
        "".join(f"WARNING READBACK-HIT-UNMARKED {hit}\n" for hit in hits) + request,
    )
    assert ran(capsys, read_back(*header, *rows, *marked, lane="lane-b"), "readback", "--lane", "lane-a", "--contract", "500")[1].startswith("READBACK-NOT-LANES #500: ")
    assert ran(capsys, read_back(*header, *rows, *marked, lane="lane-b"), "readback", "--lane", "lane-b", "--contract", "500")[1].startswith("READBACK-NOT-LANES #500: ")


def test_shapes_lists_rows_naming_a_count_or_topology_until_shape_records_a_test_or_no_shape(home: Path, package: Path, state: Path, registered: Path, capsys) -> None:
    rows = seeded(package) | {
        "rule/1111111": {"kind": "order", "status": "in-force", "words": "there is only one local replca along side one local primary and eveyhtni is a client of it", "answer": '["1111111"]'},
        "rule/2222222": {"kind": "order", "status": "in-force", "words": "in the app just run one process wtf.", "answer": '["2222222"]'},
        "rule/3333333": {"kind": "order", "status": "superseded", "words": "one upload process per chat", "answer": '["3333333"]'},
    }
    shell = RegisterShell(rows)

    assert ran(capsys, shell, "shapes", "--untested") == (0, "rule/1111111 untested | there is only one local replca along side one local primary and eveyhtni is a client of it\nrule/2222222 untested | in the app just run one process wtf.\n")
    assert ran(capsys, shell, "shape", "rule/1111111", "--test", "acme/app#9 tests/serve.rs::one_replica_for_every_chat") == (0, "rule/1111111 shape tested by acme/app#9 tests/serve.rs::one_replica_for_every_chat\n")
    assert ran(capsys, shell, "shape", "rule/2222222", "--not-shape", "it answers where one process runs, the browser or the app") == (0, "rule/2222222 is no shape ruling: it answers where one process runs, the browser or the app\n")
    assert ran(capsys, shell, "shapes", "--untested") == (0, "")
    assert ran(capsys, shell, "shapes") == (0, "rule/1111111 acme/app#9 tests/serve.rs::one_replica_for_every_chat | there is only one local replca along side one local primary and eveyhtni is a client of it\n")


def test_a_read_back_with_no_slice_line_names_the_roots_command_then_takes_the_step_into_the_lanes_brief(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    _, sliced = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-a", "--step", "1.9")
    current = rulings.SLICE_HASH.search(sliced)["hash"]
    hits = [f"{line} - keep: the line states the ban" for line in sliced.splitlines() if line.startswith(("open ", "authorized "))]
    body = tmp_path / "readback.md"
    body.write_text("\n".join(["rule/aaaaaaa: builds nothing in relay-server", "rule/ccccccc: quotes the words", "rule/eeeeeee: leaves the outage gate alone", "exception/E1: leaves the pass alone", *hits]) + "\n")
    records = [{"seq": 500, "kind": "contract", "lane": "lane-a", "text": "read-back", "refs": {"path": str(body)}, "at": AT}]
    brief = state / "briefs" / "lane-a.md"
    brief.parent.mkdir()
    brief.write_text("# lane-a\n\nBuild the pass.\n")
    request = f"readback request lane-a {current}: 4 rows, contract #500 at {body}; a fidelity desk grades it against each row's words and implications"

    assert ran(capsys, RegisterShell(seeded(package), records=records), "readback", "--lane", "lane-a", "--contract", "500") == (
        1,
        f"READBACK-NO-SLICE #500: neither the read-back nor {brief} names a `Register slice: lane=lane-a step=<n>` line; "
        "the root runs `rulings.py readback --program brook --lane lane-a --contract 500 --step <the lane's plan step>`\n",
    )
    code, out = ran(capsys, RegisterShell(seeded(package), records=records), "readback", "--lane", "lane-a", "--contract", "500", "--step", "1.9")
    assert code == 0
    assert out.splitlines() == [
        f"WARNING READBACK-SLICE-FROM-BRIEF #500: added `Register slice: lane=lane-a step=1.9` to {brief}: refreshed {brief} slice-hash none -> {current}",
        request,
    ]
    assert brief.read_text().startswith("# lane-a\n\nBuild the pass.\n\nRegister slice: lane=lane-a step=1.9\n")
    assert rulings.SLICE_HASH.search(brief.read_text())["hash"] == current
    code, out = ran(capsys, RegisterShell(seeded(package), records=records), "readback", "--lane", "lane-a", "--contract", "500")
    assert code == 0
    assert out.splitlines() == [
        f"WARNING READBACK-SLICE-FROM-BRIEF #500: the read-back names no `Register slice:` line; graded against the slice in {brief}",
        request,
    ]


def test_a_read_back_takes_its_slice_from_the_successor_brief_root_wrote_for_the_lane(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    """stack-prep-3 #199148: its slice line sat in root/successor-stack-prep-3.md, a name readback never scanned, so contract #199126 read as READBACK-NO-SLICE."""
    body = tmp_path / "readback.md"
    body.write_text("rule/aaaaaaa: builds nothing in relay-server\n")
    records = [{"seq": 500, "kind": "contract", "lane": "lane-a-3", "text": "read-back", "refs": {"path": str(body)}, "at": AT}]
    brief = state / "root" / "successor-lane-a-3.md"
    brief.parent.mkdir()
    brief.write_text("# lane-a-3\n\nRegister slice: lane=lane-a-3 step=landing\n")
    code, out = ran(capsys, RegisterShell(seeded(package), records=records), "readback", "--lane", "lane-a-3", "--contract", "500")
    assert code == 0
    assert out.splitlines()[0] == f"WARNING READBACK-SLICE-FROM-BRIEF #500: the read-back names no `Register slice:` line; graded against the slice in {brief}"


def test_a_step_for_a_lane_with_no_brief_writes_one_the_next_readback_reads(home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    _, sliced = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-a", "--step", "1.9")
    body = tmp_path / "readback.md"
    body.write_text("rule/aaaaaaa: builds nothing in relay-server\n")
    records = [{"seq": 500, "kind": "contract", "lane": "lane-a", "text": "read-back", "refs": {"path": str(body)}, "at": AT}]
    brief = state / "briefs" / "lane-a.md"

    assert ran(capsys, RegisterShell(seeded(package), records=records), "readback", "--lane", "lane-a", "--contract", "500", "--step", "1.9")[0] == 0
    assert brief.read_text().startswith("Register slice: lane=lane-a step=1.9\n")
    assert rulings.SLICE_HASH.search(brief.read_text())["hash"] == rulings.SLICE_HASH.search(sliced)["hash"]
    code, out = ran(capsys, RegisterShell(seeded(package), records=records), "readback", "--lane", "lane-a", "--contract", "500")
    assert code == 0
    assert out.splitlines()[0] == f"WARNING READBACK-SLICE-FROM-BRIEF #500: the read-back names no `Register slice:` line; graded against the slice in {brief}"


def test_a_read_back_whose_slice_gained_a_row_still_goes_to_the_desk_with_the_row_as_a_warning(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys
) -> None:
    _, sliced = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-a", "--step", "1.9")
    stamp = rulings.SLICE_HASH.search(sliced)["hash"]
    hits = [f"{line} - keep: the line states the ban" for line in sliced.splitlines() if line.startswith(("open ", "authorized "))]
    body = tmp_path / "readback.md"
    body.write_text("\n".join(["Register slice: lane=lane-a step=1.9", f"slice-hash: {stamp}", "rule/aaaaaaa: builds nothing in relay-server", "rule/ccccccc: quotes the words", "rule/eeeeeee: leaves the outage gate alone", "exception/E1: leaves the pass alone", *hits]) + "\n")
    records = [{"seq": 500, "kind": "contract", "lane": "lane-a", "text": "read-back", "refs": {"path": str(body)}, "at": AT}]
    added = seeded(package) | {"rule/fffffff": {"kind": "pick", "status": "in-force", "global": "no", "words": "Name the lane on every card.", "answer": '["fffffff"]', "steps": '["1.9"]', "lanes": '["all"]', "paths": "none"}}
    _, grown = ran(capsys, RegisterShell(added), "slice", "--lane", "lane-a", "--step", "1.9")

    code, out = ran(capsys, RegisterShell(added, records=records), "readback", "--lane", "lane-a", "--contract", "500")
    assert code == 0
    assert out.splitlines() == [
        "WARNING READBACK-ROW-MISSING rule/fffffff: no read-back line names it",
        f"readback request lane-a {rulings.SLICE_HASH.search(grown)['hash']}: 5 rows, contract #500 at {body}; a fidelity desk grades it against each row's words and implications",
    ]


@pytest.mark.parametrize(("posted", "warned"), [("2026-10-23T13:00:00Z", True), ("2026-10-23T12:00:00Z", False)])
def test_a_read_back_answers_only_for_rows_the_owner_said_before_it_was_posted(
    home: Path, package: Path, state: Path, registered: Path, tmp_path: Path, capsys, posted: str, warned: bool
) -> None:
    _, sliced = ran(capsys, RegisterShell(seeded(package)), "slice", "--lane", "lane-a", "--step", "1.9")
    stamp = rulings.SLICE_HASH.search(sliced)["hash"]
    hits = [f"{line} - keep: the line states the ban" for line in sliced.splitlines() if line.startswith(("open ", "authorized "))]
    body = tmp_path / "readback.md"
    body.write_text("\n".join(["Register slice: lane=lane-a step=1.9", f"slice-hash: {stamp}", "rule/aaaaaaa: builds nothing in relay-server", "rule/ccccccc: quotes the words", "rule/eeeeeee: leaves the outage gate alone", "exception/E1: leaves the pass alone", *hits]) + "\n")
    records = [{"seq": 500, "kind": "contract", "lane": "lane-a", "text": "read-back", "refs": {"path": str(body)}, "at": posted}]
    captured(state, capture("f", "Name the lane on every card."))
    added = seeded(package) | {"rule/fffffff": {"kind": "pick", "status": "in-force", "global": "no", "words": "Name the lane on every card.", "answer": '["fffffff"]', "steps": '["1.9"]', "lanes": '["all"]', "paths": "none", "proof": f"proof:prompt:{'f' * 16}"}}

    code, out = ran(capsys, RegisterShell(added, records=records), "readback", "--lane", "lane-a", "--contract", "500")

    assert code == 0
    assert ("WARNING READBACK-ROW-MISSING rule/fffffff: no read-back line names it" in out.splitlines()) is warned


def test_pr_block_quotes_the_owners_sentence_for_each_matched_row(home: Path, package: Path, state: Path, capsys) -> None:
    code, out = ran(capsys, RegisterShell(seeded(package), diff=DIFF), "pr-block", "--pr", "12", "--head", "cafef00d", "--lane", "lane-b")

    assert (code, out.splitlines()) == (
        0,
        [
            "## Rulings",
            "",
            "- rule/bbbbbbb: met by Pool.drain at src/relay/pool.rs:2",
            "",
            "### In your words",
            "",
            "- On Oct 20 you said “Drain the pool when a run closes.”; this change does it.",
        ],
    )


def test_pr_block_renders_from_the_pushed_head_before_the_pr_opens(home: Path, package: Path, state: Path, capsys) -> None:
    shell = RegisterShell(seeded(package), diff=DIFF)

    code, out = ran(capsys, shell, "pr-block", "--head", "cafef00d", "--lane", "lane-b")

    assert (code, out.splitlines()[:3]) == (0, ["## Rulings", "", "- rule/bbbbbbb: met by Pool.drain at src/relay/pool.rs:2"])
    assert not [argv for argv in shell.calls if argv[:3] == ["gh", "pr", "view"]]


def test_table_prints_one_plain_row_per_ruling(home: Path, package: Path, state: Path, capsys) -> None:
    code, out = ran(capsys, RegisterShell(seeded(package)), "table")

    lines = out.splitlines()
    assert code == 0
    assert lines[0] == "| What you said | Times said | What it means for us | Where it stands | PRs landed under it | Record |"
    assert lines[2] == "| “Two outages stop the drive.” (Oct 18) | 1 | nothing beyond your words | at risk | none yet | rule/eeeeeee |"
    assert (
        "| “Relay code lives in relay-journal.” (Oct 21) | 1 | 1 unconfirmed: No relay code lands in relay-server.; "
        "2 unconfirmed: relay-server gains no plug-in point for relay code.; 3 unconfirmed: Relay cleanup may live in the desktop app. "
        "| in force | none yet | rule/aaaaaaa |"
    ) in lines
    assert "| “Drain the pool when a run closes.” (Oct 20) | 1 | nothing beyond your words | in force | acme/app#12 | rule/bbbbbbb |" in lines
    assert "rule/ddddddd" not in out
    _, full = ran(capsys, RegisterShell(seeded(package)), "table", "--full")
    assert "| superseded on 2026-10-23 by rule/bbbbbbb | none yet | rule/ddddddd |" in full


def test_the_table_delta_names_changes_trouble_and_lines_unconfirmed_for_four_hours(home: Path, package: Path, state: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(rulings, "utc_now", lambda: datetime(2026, 10, 23, 18, 0, tzinfo=UTC))

    _, first = ran(capsys, RegisterShell(seeded(package)), "table", "--since-last-update")
    _, second = ran(capsys, RegisterShell(seeded(package)), "table", "--since-last-update")

    assert "rule/aaaaaaa" in first.split("**In trouble**")[0]
    sections = second.split("**")
    assert sections[1:3] == ["Changed since the last update", "\n\nNone.\n\n"]
    assert "rule/eeeeeee" in sections[4]
    assert "rule/aaaaaaa" in sections[6]
    assert json.loads((state / "register" / "table-cursor.json").read_text()) == {"at": "2026-10-23T18:00:00Z"}


def test_glossary_prints_the_product_nouns_then_the_owners_words(home: Path, package: Path, state: Path, capsys) -> None:
    captured(state, capture("9", "the shell never uploads"))

    _, out = ran(capsys, RegisterShell(seeded(package)), "glossary")

    lines = out.splitlines()
    assert lines[:12] == list(rulings.PRODUCT_NOUNS)
    assert {"relay-journal", "relay-server", "uploads", "drain"} <= set(lines[12:])
    assert "chat" not in lines[12:]


def transcript(home: Path, *entries: dict) -> None:
    path = home / ".claude" / "projects" / "-acme" / "s-root.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def card_posted(body: str, label: str = "Merge", action_id: str = "merge-12") -> list[dict]:
    tool = {"type": "tool_use", "id": "toolu_1", "name": "mcp__plugin_cc-slack_cc-slack__slack_dm_status", "input": {"card": {"title": "Merge #12?", "body": body}}}
    result = {"type": "tool_result", "tool_use_id": "toolu_1", "content": json.dumps({"ok": True, "ts": CARD_TS})}
    action = {"type": "slack.action", "ts": CARD_TS, "action_ts": TAP_TS, "label": label, "id": action_id}
    channel = f'<channel source="plugin:cc-slack:cc-slack" type="slack.action">\n{json.dumps(action, separators=(",", ":"))}\n</channel>'
    return [
        {"type": "assistant", "message": {"content": [tool]}},
        {"type": "user", "message": {"content": [result]}},
        {"type": "queue-operation", "operation": "enqueue", "content": channel},
    ]


def test_authorize_labels_the_answer_for_a_tap_on_a_card_quoting_the_row(home: Path, package: Path, state: Path, registered: Path, capsys) -> None:
    transcript(home, *card_posted(QUOTING_CARD))
    shell = RegisterShell(seeded(package), answers=[OWNER_TAP])

    assert ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--action", TAP_TS) == (0, "authorized rule/aaaaaaa by 1111111\n")
    assert shell.answers[0]["tags"] == ["from:owner", "program:brook", "authorizes:rule/aaaaaaa"]


def test_authorize_refuses_a_go_with_no_tap_a_card_or_answer_naming_no_row_and_an_unrelated_prompt(
    home: Path, package: Path, state: Path, registered: Path, capsys
) -> None:
    transcript(
        home,
        *card_posted(QUOTING_CARD),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "The pool drains on close now."}]}},
        {"type": "user", "message": {"content": "yes go ahead"}},
    )
    captured(state, capture("d", "yes go ahead"))
    silent = dict(OWNER_TAP, body="Owner tapped Merge on https://acme.slack.com/archives/D1/p1790000000000100.")

    def refused(answers: list[dict], *argv: str) -> str:
        shell = RegisterShell(seeded(package), answers=answers)
        code, out = ran(capsys, shell, "authorize", *argv)
        assert code == 1
        assert all("authorizes:" not in tag for answer in shell.answers for tag in answer["tags"])
        return out.split(" ", 1)[0]

    assert refused([OWNER_TAP], "--row", "rule/aaaaaaa", "--action", "1790000999.000000") == "AUTHORIZE-NO-TAP"
    assert refused([OWNER_TAP], "--row", "rule/bbbbbbb", "--action", TAP_TS) == "AUTHORIZE-CARD-NAMES-NO-ROW"
    assert refused([silent], "--row", "rule/aaaaaaa", "--action", TAP_TS) == "AUTHORIZE-ANSWER-NAMES-NO-ROW"
    assert refused([OWNER_TAP], "--row", "rule/aaaaaaa", "--prompt", "d" * 16) == "AUTHORIZE-PROMPT-NAMES-NO-ROW"


@pytest.mark.parametrize(
    ("prompt", "marks", "verdict"),
    [
        (QUOTING_CARD, {"verdict": "approved"}, "authorized"),
        ("One storage credential per Mac: the server issues one key.", {"verdict": "approved"}, "AUTHORIZE-PROMPT-NAMES-NO-ROW"),
        (QUOTING_CARD, {}, "AUTHORIZE-NOT-AFFIRMATIVE"),
        (QUOTING_CARD, {"verdict": "rejected", "row": "42"}, "AUTHORIZE-NOT-AFFIRMATIVE"),
    ],
    ids=["approval-quoting-the-row", "approval-of-a-merge-row-only", "note-quoting-the-row", "rejected-row-note"],
)
def test_authorize_takes_only_a_board_approval_whose_prompt_quotes_the_row(home: Path, package: Path, state: Path, registered: Path, capsys, prompt: str, marks: dict, verdict: str) -> None:
    transcript(home, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Board round posted."}]}})
    captured(state, capture("e", prompt, source="board", block="rulings-card") | marks)
    approval = dict(OWNER_TAP, body="Approved on the board: merge also authorizes rule/aaaaaaa.", tags=["from:owner", "program:brook", f"proof:board:{'e' * 16}"])
    shell = RegisterShell(seeded(package), answers=[approval])

    code, out = ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--prompt", "e" * 16)

    assert (code, out.split(" ", 1)[0]) == (0 if verdict == "authorized" else 1, verdict)
    assert ("authorizes:rule/aaaaaaa" in shell.answers[0]["tags"]) == (verdict == "authorized")


def test_authorize_refuses_a_merge_card_tap_for_a_reserved_row_its_pr_does_not_touch(home: Path, package: Path, state: Path, registered: Path, capsys) -> None:
    rows = seeded(package)
    rows["rule/aaaaaaa"] |= {"kind": "reserved", "ops": '["*drain*"]'}
    transcript(home, *card_posted(QUOTING_CARD, action_id="merge:12:cafef00d"))

    untouched = RegisterShell(rows, answers=[OWNER_TAP], diff=DIFF.replace("self.drain();", "self.close();"))
    assert ran(capsys, untouched, "authorize", "--row", "rule/aaaaaaa", "--action", TAP_TS) == (
        1,
        "AUTHORIZE-ROW-UNTOUCHED rule/aaaaaaa: #12 at cafef00d changes nothing rule/aaaaaaa covers, so the tap authorizes nothing\n",
    )
    assert "authorizes:rule/aaaaaaa" not in untouched.answers[0]["tags"]
    assert ran(capsys, RegisterShell(rows, answers=[OWNER_TAP], diff=DIFF), "authorize", "--row", "rule/aaaaaaa", "--action", TAP_TS) == (0, "authorized rule/aaaaaaa by 1111111\n")


BOARD_TAP_AT = "2026-10-27T02:23:40Z"


def pool_row(head: str, words: str) -> dict:
    return {"number": 12, "title": "Drain on close", "head": head, "md": words, "change": "Drains the pool.", "plan": "", "risk": "", "review": "", "blocker": ""}


def pool_block(*rows: dict) -> dict:
    return {"type": "pr-groups", "id": "merge-prs", "groups": [{"id": "pool", "prs": list(rows)}]}


def board_of(words: str, past: list[tuple[str, str]] = (), **tap: str) -> dict:
    verdict = {"verdict": "approved", "head": "cafef00d", "at": BOARD_TAP_AT} | tap
    return {
        "doc": {"blocks": [pool_block(pool_row("cafef00d", words))]},
        "rounds": {"history": [{"number": number, "blocks": [pool_block(pool_row(head, said))]} for number, (head, said) in enumerate(past, 1)]},
        "interactions": {"triage": {"merge-prs": {"12": verdict}}},
    }


@pytest.fixture
def reserved(package: Path) -> dict[str, dict[str, str]]:
    rows = seeded(package)
    rows["rule/aaaaaaa"] |= {"kind": "reserved", "ops": '["*drain*"]'}
    return rows


def test_authorize_records_the_owner_answer_for_an_approved_board_merge_row_quoting_the_row(home: Path, package: Path, state: Path, registered: Path, reserved: dict, capsys) -> None:
    transcript(home, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Board posted."}]}})
    shell = RegisterShell(reserved, diff=DIFF, board=board_of(QUOTING_CARD))

    code, out = ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--action", BOARD_TAP_AT)

    assert (code, out) == (0, "authorized rule/aaaaaaa by 4444444\n")
    assert shell.answers[0]["tags"] == ["from:owner", "program:brook", f"proof:board:merge-prs:12:{BOARD_TAP_AT}", "authorizes:rule/aaaaaaa"]
    assert "Owner approved #12 at cafef00d on board s-root" in shell.answers[0]["body"]


def test_authorize_honors_a_carried_board_tap_and_reuses_the_answer_it_already_recorded(home: Path, package: Path, state: Path, registered: Path, reserved: dict, capsys) -> None:
    transcript(home, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Board posted."}]}})
    recorded = dict(OWNER_TAP, body="Owner approved #12 at cafef00d, carried from 1234567: merge also authorizes rule/aaaaaaa.", tags=["from:owner", "program:brook", f"proof:board:merge-prs:12:{BOARD_TAP_AT}"])
    shell = RegisterShell(reserved, answers=[recorded], diff=DIFF, board=board_of("The pool drains now.", past=[("1234567", QUOTING_CARD)], carriedFrom="1234567", carryNote="patch-identical restack, per cci #1"))

    assert ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--action", BOARD_TAP_AT) == (0, "authorized rule/aaaaaaa by 1111111\n")
    assert len(shell.answers) == 1


@pytest.mark.parametrize(
    ("board", "at", "verdict"),
    [
        (board_of(QUOTING_CARD), "2026-10-27T03:00:00Z", "AUTHORIZE-NO-TAP"),
        (board_of(QUOTING_CARD, verdict="rejected"), BOARD_TAP_AT, "AUTHORIZE-NOT-AFFIRMATIVE"),
        (board_of("The pool drains on close now."), BOARD_TAP_AT, "AUTHORIZE-CARD-NAMES-NO-ROW"),
        (board_of(QUOTING_CARD), BOARD_TAP_AT, "AUTHORIZE-ROW-UNTOUCHED"),
        (board_of(QUOTING_CARD, past=[("1234567", "The pool drains now.")], carriedFrom="1234567"), BOARD_TAP_AT, "AUTHORIZE-CARD-NAMES-NO-ROW"),
    ],
    ids=["no-tap-at-that-time", "rejected-row", "row-quoting-nothing", "head-touching-nothing", "carried-from-a-row-quoting-nothing"],
)
def test_authorize_refuses_a_board_tap_that_is_missing_rejected_unquoting_or_untouching(home: Path, package: Path, state: Path, registered: Path, reserved: dict, capsys, board: dict, at: str, verdict: str) -> None:
    transcript(home, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Board posted."}]}})
    shell = RegisterShell(reserved, diff=DIFF.replace("self.drain();", "self.close();") if verdict == "AUTHORIZE-ROW-UNTOUCHED" else DIFF, board=board)

    code, out = ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--action", at)

    assert (code, out.split(" ", 1)[0]) == (1, verdict)
    assert not shell.answers


def card_shown(summary: str, answer: str) -> str:
    return "\n".join(
        [
            "card-12-relay: Relay: move the relay code. Merge it?",
            f"  {summary}",
            "  No longer on the board; shown as it last was.",
            "",
            "relay-12-choice: choice, pick one",
            "  Should the relay code move?",
            "  - relay-12-approve: Yes, merge it: move the relay code (recommended)",
            "    What it does: the relay code moves.",
            "  - relay-12-hold: No, hold it",
            "    What it does: nothing moves.",
            "",
            "relay-12-approval: approval",
            "  Approve the move?",
            "",
            "Answers, newest first:",
            *([f"  Oct 28 7:00 PM PDT  {answer}"] if answer else []),
            "",
        ]
    )


QUOTING_SUMMARY = "Your words, verbatim: Oct 18 “Relay code lives in relay-journal.” This moves it there."


@pytest.mark.parametrize(
    ("summary", "answer", "flags", "verdict"),
    [
        (QUOTING_SUMMARY, "relay-12-choice  picked relay-12-approve", ["--option", "relay-12-approve"], ""),
        (QUOTING_SUMMARY, "relay-12-approval  approved", [], ""),
        (QUOTING_SUMMARY, "relay-12-choice  picked relay-12-hold", ["--option", "relay-12-hold"], "AUTHORIZE-NOT-AFFIRMATIVE"),
        (QUOTING_SUMMARY, "relay-12-choice  picked relay-12-hold", ["--option", "relay-12-approve"], "AUTHORIZE-NOT-AFFIRMATIVE"),
        ("This moves the relay code.", "relay-12-choice  picked relay-12-approve", ["--option", "relay-12-approve"], "AUTHORIZE-CARD-NAMES-NO-ROW"),
        (QUOTING_SUMMARY, "", ["--option", "relay-12-approve"], "AUTHORIZE-NO-TAP"),
    ],
    ids=["affirmative-pick", "approval", "hold-pick", "pick-changed-to-hold", "card-quoting-nothing", "no-answer"],
)
def test_authorize_takes_the_owners_affirmative_pick_on_a_card_quoting_the_rows_words(home: Path, package: Path, state: Path, registered: Path, capsys, summary: str, answer: str, flags: list[str], verdict: str) -> None:
    transcript(home, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Card posted."}]}})
    block = "relay-12-choice" if flags else "relay-12-approval"
    shell = RegisterShell(seeded(package), blocks={block: card_shown(summary, answer)})

    code, out = ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--block", block, *flags)

    if verdict:
        assert (code, out.split(" ", 1)[0]) == (1, verdict)
        assert not shell.answers
        return
    assert (code, out) == (0, "authorized rule/aaaaaaa by 4444444\n")
    [made] = shell.answers
    assert made["title"] == "Relay: move the relay code. Merge it?"
    assert made["tags"] == ["from:owner", "program:brook", f"proof:card:{block}:{flags[1] if flags else 'approved'}", "authorizes:rule/aaaaaaa"]


@pytest.mark.parametrize("label", ["Hold", "Right", "Some wrong"])
def test_authorize_refuses_a_tap_that_grants_nothing(home: Path, package: Path, state: Path, registered: Path, capsys, label: str) -> None:
    transcript(home, *card_posted(QUOTING_CARD, label=label))
    shell = RegisterShell(seeded(package), answers=[OWNER_TAP])

    assert ran(capsys, shell, "authorize", "--row", "rule/aaaaaaa", "--action", TAP_TS) == (
        1,
        f"AUTHORIZE-NOT-AFFIRMATIVE rule/aaaaaaa: the tap was {label!r}, which grants nothing\n",
    )
    assert "authorizes:rule/aaaaaaa" not in shell.answers[0]["tags"]


def test_pr_block_cites_only_an_authorization_that_names_this_pr(home: Path, package: Path, state: Path, capsys) -> None:
    diff = DIFF.replace("+    self.drain();", "+    let pool = Pool::new();")
    granted = {"id": "2222222" + "0" * 33, "title": "Merge #12?", "body": "Merge also authorizes rule/bbbbbbb.", "tags": ["authorizes:rule/bbbbbbb"]}
    elsewhere = {"id": "3333333" + "0" * 33, "title": "Merge #9?", "body": "Merge also authorizes rule/bbbbbbb.", "tags": ["authorizes:rule/bbbbbbb"]}

    _, cited = ran(capsys, RegisterShell(seeded(package), answers=[elsewhere, granted], diff=diff), "pr-block", "--pr", "12", "--head", "cafef00d", "--base", "main", "--lane", "lane-b")
    _, left = ran(capsys, RegisterShell(seeded(package), answers=[elsewhere], diff=diff), "pr-block", "--pr", "12", "--head", "cafef00d", "--base", "main", "--lane", "lane-b")

    assert "- rule/bbbbbbb: authorized by 2222222" in cited.splitlines()
    assert "- rule/bbbbbbb: leaves out Pool.drain" in left.splitlines()


def test_pr_block_says_what_a_protecting_row_protects_and_how_the_change_serves_it(home: Path, package: Path, state: Path, capsys) -> None:
    """port-memory-6 #231684: pr-block printed 'leaves out <name>' for a row with a protects field, and check-pr refused that same line as RULINGS-PLAIN."""
    diff = DIFF.replace("+    self.drain();", "+    let pool = Pool::new();")
    rows = seeded(package)
    rows["rule/bbbbbbb"] |= {"protects": "runs from holding a closed pool's connections"}

    _, met = ran(capsys, RegisterShell(rows, diff=DIFF), "pr-block", "--pr", "12", "--head", "cafef00d", "--lane", "lane-b")
    _, left = ran(capsys, RegisterShell(rows, diff=diff), "pr-block", "--pr", "12", "--head", "cafef00d", "--base", "main", "--lane", "lane-b")

    assert "- rule/bbbbbbb protects runs from holding a closed pool's connections; this change serves it by meeting it with Pool.drain at src/relay/pool.rs:2" in met.splitlines()
    assert "- rule/bbbbbbb protects runs from holding a closed pool's connections; this change serves it by leaving Pool.drain alone" in left.splitlines()
    assert all(rulings.EXPLAINS.search(line) for line in (met + left).splitlines() if line.startswith("- rule/bbbbbbb"))


def test_pr_block_names_a_deletion_of_a_protected_symbol_and_never_calls_it_left_alone(home: Path, package: Path, state: Path, capsys) -> None:
    """plan-group-b-1 #238453, root #238470: pr-block wrote 'serves it by leaving SessionThreadFollower alone' for a diff that deleted SessionThreadFollower."""
    diff = DIFF.replace("-    retry_forever(relay-server);\n+    self.drain();", "-    self.drain();\n+    retry_forever(relay-server);")
    rows = seeded(package)
    rows["rule/bbbbbbb"] |= {"protects": "runs from holding a closed pool's connections"}

    _, out = ran(capsys, RegisterShell(rows, diff=diff), "pr-block", "--pr", "12", "--head", "cafef00d", "--base", "main", "--lane", "lane-b")

    assert "- rule/bbbbbbb protects runs from holding a closed pool's connections; this change deletes Pool.drain at src/relay/pool.rs:2, and no answer of yours authorizes that" in out.splitlines()
    assert "- On Oct 20 you said “Drain the pool when a run closes.”; this change deletes Pool.drain, and none of your answers authorizes that." in out.splitlines()
    assert not any("alone" in line for line in out.splitlines() if "bbbbbbb" in line or "Drain the pool" in line)


def test_status_anchor_and_supersede_write_their_fields(home: Path, package: Path, state: Path, capsys) -> None:
    shell = RegisterShell(seeded(package))

    assert ran(capsys, shell, "status", "rule/aaaaaaa", "broken", "--evidence", "#110600")[0] == 0
    assert ran(capsys, shell, "anchor", "rule/ccccccc", "--paths", "src/cards.py", "--symbols", "quote_words")[0] == 0
    assert ran(capsys, shell, "supersede", "rule/bbbbbbb", "--by", "rule/ccccccc")[0] == 0

    assert (shell.rows["rule/aaaaaaa"]["status"], shell.rows["rule/aaaaaaa"]["evidence"]) == ("broken", "#110600")
    assert (shell.rows["rule/ccccccc"]["paths"], shell.rows["rule/ccccccc"]["symbols"]) == ('["src/cards.py"]', '["quote_words"]')
    assert ["ccn", "-R", REPO, "answer", "supersede", "bbbbbbb", "--by", "ccccccc"] in shell.calls
    assert (shell.rows["rule/bbbbbbb"]["status"], shell.rows["rule/bbbbbbb"]["superseded_by"]) == ("superseded", '["rule/ccccccc"]')
    assert shell.rows["rule/bbbbbbb"]["evidence"] == "superseded by rule/ccccccc: “Cards quote the words they ask about.”"
    assert shell.rows["rule/ccccccc"]["supersedes"] == '["rule/bbbbbbb"]'


ROUND_TWO = {
    "id": "5eed0c0de1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
    "title": "Round 2: confirms?",
    "body": (
        "Board round 2, 10:00 PM Oct 21: approved r2-pool-confirm (old jobs per 1234567), r2-op9-approval (a run missing its state opens read-only with Retry),"
        " r2-drain-approval, r2-relay-confirm (one reader, two writers), r2-stream-confirm (events stream both ways, nothing polled), r2-cache-confirm."
    ),
    "tags": ["from:owner", "program:brook", "scope:durable"],
    "created_at": "2026-10-22T05:00:10Z",
}
ROUND_TWO_ROW = {
    "kind": "pick",
    "status": "in-force",
    "global": "no",
    "words": "No old job is deleted until a later card. Approve?",
    "answer": '["5eed0c0"]',
    "at": "Oct 21 10:00 PM PT",
    "said": "1",
    "steps": '["1.9"]',
    "lanes": '["all"]',
    "paths": "none",
    "symbols": "none",
    "tests": '["C233"]',
    "evidence": "census",
}
APPROVED = (
    "approved r2-pool-confirm (old jobs per 1234567), r2-op9-approval (a run missing its state opens read-only with Retry), r2-drain-approval,"
    " r2-relay-confirm (one reader, two writers), r2-stream-confirm (events stream both ways, nothing polled), r2-cache-confirm"
)


def answer_sourced(tmp_path: Path, words: str = APPROVED) -> Path:
    path = tmp_path / "round-two.json"
    implication = {"text": "A run missing its state opens read-only, with Retry.", "direction": "permits"}
    path.write_text(json.dumps([{"extends": "rule/5eed0c0", "words": words, "implications": [implication]}]))
    return path


@pytest.mark.parametrize("source", ["from:owner", "slack:owner-words"])
def test_classify_answer_extends_the_row_with_the_owners_six_approvals_and_the_answer_as_proof(home: Path, package: Path, state: Path, tmp_path: Path, capsys, source: str) -> None:
    shell = RegisterShell(seeded(package) | {"rule/5eed0c0": dict(ROUND_TWO_ROW)}, answers=[dict(ROUND_TWO, tags=[source, "program:brook", "scope:durable"])])

    code, _ = ran(capsys, shell, "classify", "--answer", "5eed0c0", str(answer_sourced(tmp_path)))

    row = shell.rows["rule/5eed0c0"]
    [repeat] = json.loads(row["repeats"])
    assert code == 0
    assert (repeat["key"], repeat["answer"], repeat["words"], repeat["proof"]) == ("rule/5eed0c0", [ROUND_TWO["id"]], APPROVED, "proof:answer:5eed0c0de1b2c3d4")
    assert row["said"] == "1"
    assert [item["text"] for item in json.loads(row["implications"])] == ["A run missing its state opens read-only, with Retry."]
    assert ran(capsys, shell, "classify", "--answer", "5eed0c0", str(answer_sourced(tmp_path))) == (
        1,
        "CLASSIFIED 5eed0c0de1b2c3d4: this capture is already in the register or earlier in this batch\n",
    )


def test_classify_answer_takes_only_words_in_an_owner_answer_body(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    rows = seeded(package) | {"rule/5eed0c0": dict(ROUND_TWO_ROW)}
    reading = answer_sourced(tmp_path, "the owner approved opening old runs read-only")

    code, out = ran(capsys, RegisterShell(rows, answers=[ROUND_TWO]), "classify", "--answer", "5eed0c0", str(reading))
    assert code == 1
    assert out.startswith("NOT-VERBATIM 5eed0c0de1b2c3d4: ")

    unlabelled = {key: value for key, value in ROUND_TWO.items() if key != "tags"}
    for written in (dict(ROUND_TWO, tags=["program:brook", "scope:durable"]), unlabelled):
        with pytest.raises(SystemExit, match=r"^NOT-OWNER 5eed0c0: "):
            rulings.main(["classify", "--program", "brook", "--repo", REPO, "--answer", "5eed0c0", str(answer_sourced(tmp_path))], RegisterShell(rows, answers=[written]))


def answer_onto(tmp_path: Path, key: str) -> Path:
    path = tmp_path / "onto.json"
    path.write_text(json.dumps([{"extends": key, "words": APPROVED}]))
    return path


def test_confirm_takes_an_owner_answer_classified_onto_the_row_or_cited_by_its_implications(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    shell = RegisterShell(seeded(package), answers=[ROUND_TWO])
    assert ran(capsys, shell, "classify", "--answer", "5eed0c0", str(answer_onto(tmp_path, "rule/aaaaaaa")))[0] == 0

    code, out = ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", "5eed0c0", "--tick", "3")

    assert code == 0
    assert out.splitlines()[2] == "3. [permits, ticked, binds now] Relay cleanup may live in the desktop app."
    assert json.loads(shell.rows["rule/aaaaaaa"]["implications"])[2]["proof"] == "5eed0c0"

    cited = RegisterShell(seeded(package), answers=[ROUND_TWO])
    implications = json.loads(cited.rows["rule/aaaaaaa"]["implications"])
    cited.rows["rule/aaaaaaa"]["implications"] = json.dumps([item | {"proof": "aaaaaaa 5eed0c0"} for item in implications])
    assert ran(capsys, cited, "confirm", "rule/aaaaaaa", "--capture", "5eed0c0", "--tick", "2")[0] == 0
    assert json.loads(cited.rows["rule/aaaaaaa"]["implications"])[1]["state"] == "ticked"


def test_amend_links_an_answer_and_rewrites_one_implication_and_records_both_on_the_row(home: Path, package: Path, state: Path, capsys, judged: Judge) -> None:
    shell = RegisterShell(seeded(package), answers=[ROUND_TWO])
    was = json.loads(shell.rows["rule/aaaaaaa"]["implications"])[1]["text"]

    code, _ = ran(capsys, shell, "amend", "rule/aaaaaaa", "--answer", "5eed0c0", "--implication", "2", "--text", "Relay cleanup lives in the journal crate.")

    row = shell.rows["rule/aaaaaaa"]
    assert code == 0
    assert ROUND_TWO["id"] in json.loads(row["answer"])
    assert json.loads(row["implications"])[1]["text"] == "Relay cleanup lives in the journal crate."
    assert [(edit.get("answer"), edit.get("implication"), edit.get("was")) for edit in json.loads(row["amends"])] == [([ROUND_TWO["id"]], None, None), (None, 2, was)]
    assert [(kind, artifact) for kind, _, artifact, _ in judged.asked] == [("implication", "Relay cleanup lives in the journal crate. (removes)")]


def test_an_owner_requirement_binds_when_recorded_and_amend_moves_a_permits_line_to_requires(home: Path, package: Path, state: Path, capsys) -> None:
    """perf-prevention-1 (ccn fa32987, root #209337): his speed and one-process requirements sat as permits lines waiting for a tick for a day."""
    rows = seeded(package)
    rows["rule/aaaaaaa"]["implications"] = implied([("The chat list answers within half a second.", "permits"), ("The Mac runs one replication process.", "requires")])
    shell = RegisterShell(rows)

    assert ran(capsys, shell, "implications", "rule/aaaaaaa") == (
        0,
        "1. [permits, proposed, waits for the owner's tick] The chat list answers within half a second.\n2. [requires, proposed, binds now] The Mac runs one replication process.\n",
    )
    code, out = ran(capsys, shell, "amend", "rule/aaaaaaa", "--implication", "1", "--direction", "requires")
    assert code == 0 and out.startswith("1. [requires, proposed, binds now] The chat list answers within half a second.\n")
    assert [(edit["implication"], edit["was_direction"], edit["direction"]) for edit in json.loads(shell.rows["rule/aaaaaaa"]["amends"])] == [(1, "permits", "requires")]
    assert ran(capsys, shell, "amend", "rule/aaaaaaa", "--implication", "1") == (2, "AMEND-USAGE: --implication goes with --text, --direction, or both\n")


def test_confirm_refuses_an_owner_answer_the_row_never_cites_and_the_rows_own_source(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    unrelated = dict(ROUND_TWO, id="0dd0c0de" + "0" * 32)
    shell = RegisterShell(seeded(package), answers=[ROUND_TWO, unrelated])
    assert ran(capsys, shell, "classify", "--answer", "5eed0c0", str(answer_onto(tmp_path, "rule/bbbbbbb")))[0] == 0
    before = shell.rows["rule/aaaaaaa"]["implications"]

    for identity in ("5eed0c0", "0dd0c0d", "aaaaaaa"):
        assert ran(capsys, shell, "confirm", "rule/aaaaaaa", "--capture", identity, "--tick", "3") == (
            1,
            f"CAPTURE-NOT-ON-CARD {identity}: confirm takes only a captured tap or reply on rule/aaaaaaa's card\n",
        )
    assert shell.rows["rule/aaaaaaa"]["implications"] == before


def test_one_answer_extends_several_rows_in_one_batch_and_never_again(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    shell = RegisterShell(seeded(package) | {"rule/5eed0c0": dict(ROUND_TWO_ROW)}, answers=[ROUND_TWO])
    path = tmp_path / "round-two-rows.json"
    path.write_text(
        json.dumps([
            {"extends": "rule/5eed0c0", "words": "r2-op9-approval (a run missing its state opens read-only with Retry)"},
            {"extends": "rule/bbbbbbb", "words": "r2-drain-approval"},
        ])
    )

    code, _ = ran(capsys, shell, "classify", "--answer", "5eed0c0", str(path))

    assert code == 0
    assert [json.loads(shell.rows[key]["repeats"])[0]["proof"] for key in ("rule/5eed0c0", "rule/bbbbbbb")] == ["proof:answer:5eed0c0de1b2c3d4"] * 2
    assert ran(capsys, shell, "classify", "--answer", "5eed0c0", str(path))[0] == 1


MIGRATION_DIFF = DIFF + """diff --git a/db/migrations/0042_parking.sql b/db/migrations/0042_parking.sql
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/db/migrations/0042_parking.sql
@@ -0,0 +1,3 @@
+CREATE TABLE parked (
+  parked_at TEXT NOT NULL
+);
"""
MIGRATION = "db/migrations/0042_parking.sql"
GRADED_OK = "fidelity: ok cafef00 checked exception/E1,rule/aaaaaaa,rule/bbbbbbb names relay-server|parked worker\n"


def graded(capsys: pytest.CaptureFixture[str], shell: RegisterShell, out: Path, *argv: str) -> tuple[int, str, str]:
    code, printed = ran(capsys, shell, "grade", "--pr", "acme/app#12", "--head", "cafef00d", "--base", "main", "--out", str(out), *argv)
    return code, printed, out.read_text()


def test_a_new_migration_file_is_a_mechanism_the_grade_refuses_ok_until_grounded(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    code, printed, grade = graded(capsys, RegisterShell(seeded(package), diff=MIGRATION_DIFF), tmp_path / "head.grade")
    assert code == 1
    assert f"scanned 2 files: src/relay/pool.rs, {MIGRATION}" in printed
    assert f"UNGROUNDED {MIGRATION}:1 [migration, table] CREATE TABLE parked (" in printed
    assert f"UNGROUNDED {MIGRATION}:2 [column] parked_at TEXT NOT NULL" in printed
    assert "fidelity: ok" not in grade
    assert grade.splitlines()[0].startswith(f"fidelity: missing cafef00 plan {MIGRATION}:1 plan names no migration or table")


def test_a_root_seq_or_a_design_row_citing_no_owner_answer_is_rejected_as_grounds(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    rows = seeded(package) | {"design/s1": {"kind": "design", "status": "in-force", "global": "yes", "words": "One database per session.", "answer": "[]", "proof": "design-rev.md"}}
    for ground in ("#126206", "design/s1"):
        code, printed, grade = graded(capsys, RegisterShell(rows, diff=MIGRATION_DIFF), tmp_path / "head.grade", "--ground", f"{MIGRATION}:*={ground}")
        assert code == 1
        assert f"REJECTED {ground}: {rulings.ROOT_GROUNDS}" in printed
        assert f"; {ground}: {rulings.ROOT_GROUNDS}" in grade
        assert "fidelity: ok" not in grade


def test_a_plan_line_grounds_a_mechanism_and_is_printed_beside_it(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    code, printed, grade = graded(capsys, RegisterShell(seeded(package), diff=MIGRATION_DIFF), tmp_path / "head.grade", "--ground", f"{MIGRATION}:*=#126206,plan:12")
    assert code == 0
    assert f"grounded {MIGRATION}:1 [migration, table] CREATE TABLE parked (" in printed
    assert "  accepted plan:12 | 2. Drain on close." in printed
    assert f"  REJECTED #126206: {rulings.ROOT_GROUNDS}" in printed
    assert grade == GRADED_OK


def test_an_owner_rule_row_grounds_a_mechanism_and_a_not_a_mechanism_mark_sets_one_aside(home: Path, package: Path, state: Path, tmp_path: Path, capsys) -> None:
    code, printed, grade = graded(
        capsys,
        RegisterShell(seeded(package), diff=MIGRATION_DIFF),
        tmp_path / "head.grade",
        "--ground",
        f"{MIGRATION}:1=rule/bbbbbbb",
        "--not-a-mechanism",
        f"{MIGRATION}:2=the column the plan's table names",
    )
    assert code == 0
    assert "  accepted rule/bbbbbbb | Drain the pool when a run closes." in printed
    assert f"not-a-mechanism {MIGRATION}:2 [column] parked_at TEXT NOT NULL : the column the plan's table names" in printed
    assert grade == GRADED_OK


@pytest.mark.parametrize(("key", "refused"), [("design/s1", True), ("rule/bbbbbbb", False), ("plan:12", False)])
def test_a_missing_row_must_be_a_plan_line_or_an_owner_row(home: Path, package: Path, state: Path, tmp_path: Path, capsys, key: str, refused: bool) -> None:
    rows = seeded(package) | {"design/s1": {"kind": "design", "status": "in-force", "global": "yes", "words": "One database per session.", "answer": "[]", "proof": "design-rev.md"}}
    argv = ("--ground", f"{MIGRATION}:*=plan:12", "--missing", f"{key}=the head keeps a second database")
    if refused:
        with pytest.raises(SystemExit, match=re.escape(f"--missing takes a plan line plan:<n> or an owner row: {key}: {rulings.ROOT_GROUNDS}")):
            graded(capsys, RegisterShell(rows, diff=MIGRATION_DIFF), tmp_path / "head.grade", *argv)
        return
    code, _, grade = graded(capsys, RegisterShell(rows, diff=MIGRATION_DIFF), tmp_path / "head.grade", *argv)
    assert code == 1
    assert grade == f"fidelity: missing cafef00 {key} the head keeps a second database\n"


SYMBOL_DIFF = """diff --git a/go/release/withhold.go b/go/release/withhold.go
--- a/go/release/withhold.go
+++ b/go/release/withhold.go
@@ -1,1 +1,2 @@
 package release
+func (d *Driver) Retire(retired string) error { return nil }
diff --git a/go/release/withhold_test.go b/go/release/withhold_test.go
--- a/go/release/withhold_test.go
+++ b/go/release/withhold_test.go
@@ -1,1 +1,2 @@
 package release
+var _ = "retire"
diff --git a/docs/lessons.md b/docs/lessons.md
--- a/docs/lessons.md
+++ b/docs/lessons.md
@@ -1,1 +1,2 @@
 # Lessons
+Workers retire after the watch; retire them by hand otherwise.
"""


@pytest.mark.parametrize(("symbol", "hit"), [("retire", False), ("Driver.Retire", True), ("Retire", True), ("Retired", False)])
def test_a_symbol_hits_only_its_exact_identifier_in_code_never_a_doc_or_test_word(symbol: str, hit: bool) -> None:
    assert rulings.symbol_hit(symbol, rulings.parse_diff(SYMBOL_DIFF)) is hit


def test_a_row_whose_symbol_only_a_doc_and_a_test_name_is_untouched() -> None:
    diff = rulings.parse_diff(SYMBOL_DIFF)
    assert not rulings.touched({"symbols": '["retire"]', "paths": '["rust/crates/server/src/admin.rs:3931 @ 19dd3ce34ce"]'}, diff)
    assert rulings.touched({"symbols": '["retire"]'}, rulings.parse_diff(SYMBOL_DIFF.replace("func (d *Driver) Retire(", "func retire(")))
