from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import handoff
import ledger
import pytest
import standing

REPO = "/repo"
LEDGER = "1a2b3c4d" * 5
RULE = {
    "id": "4ffc9a5" + "0" * 33,
    "title": "When does a merged change get released?",
    "body": "As it merges, every PR, never on the owner's word.",
    "tags": ["scope:durable", "brook"],
    "updated_at": "2026-10-01T14:07:41Z",
}
OLD_RULE = {
    "id": "ec2881e" + "0" * 33,
    "title": "Is any ledger a source of truth?",
    "body": "No.\n\nRule: Pulumi state is the only truth; the ledger only records and renders.",
    "tags": ["scope:durable", "progress:brook"],
    "updated_at": "2026-09-30T01:00:00Z",
}
OTHER = {"id": "abcdef1" + "0" * 33, "title": "Unrelated", "body": "elsewhere", "tags": ["scope:durable"], "updated_at": "2026-10-01T00:00:00Z"}
PICK = {
    "id": "e0e57c8" + "0" * 33,
    "title": "Card 6, selection: confirm one resolver?",
    "body": "One set; closure exclusions allowed (Recommended)\nOptions: One set; closure exclusions allowed (Recommended) | One set; exclude only direct picks",
    "tags": ["from:owner", "source:askuserquestion", "scope:durable"],
    "updated_at": "2026-10-05T04:12:39Z",
}
FOREIGN = {
    "id": "f0f0f0f" + "0" * 33,
    "title": "Another drive's pick?",
    "body": "Theirs",
    "tags": ["from:owner", "source:askuserquestion", "scope:durable"],
    "updated_at": "2026-10-05T04:00:00Z",
}
CREATED_IN = {PICK["id"]: "s-root", FOREIGN["id"]: "s-other", OTHER["id"]: "s-root"}
REGISTER = "standing-rules:brook"


class FakeCcn(ledger.Shell):
    def __init__(self, answers: list[dict], docs: dict[str, str], rows: list[dict]) -> None:
        self.answers = answers
        self.docs = dict(docs)
        self.active = list(docs)
        self.rows = rows
        self.calls: list[list[str]] = []
        self.added: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.stuck: set[str] = set()
        self.created: dict[str, dict] = {}
        self.registers: list[str] = []

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        self.calls.append(argv)
        if argv[1:3] == ["ledger", "show"]:
            return json.dumps({"id": argv[3], "rows": self.rows})
        assert argv[1:3] == ["-R", REPO], argv
        verb = argv[3:5]
        labels = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--label"]
        if verb == ["answer", "list"]:
            return json.dumps([a for a in self.answers if all(label in a["tags"] for label in labels)])
        if verb == ["answer", "history"]:
            return json.dumps([{"kind": "edit"}, {"kind": "create", "session": CREATED_IN.get(argv[5])}])
        if verb == ["doc", "list"]:
            return json.dumps(
                [
                    {"id": doc, "title": self.titles.get(doc, "brook: progress"), "updated_at": f"2026-10-01T0{i}:00:00Z"}
                    for i, doc in enumerate(self.registers if REGISTER in labels else self.active)
                ]
            )
        if verb == ["doc", "show"]:
            return json.dumps({"id": argv[5], "body": self.docs[argv[5]]})
        if verb == ["doc", "history"]:
            return json.dumps([{"kind": "edit", "time": "2026-10-01T00:00:00Z"}, {"kind": "create", "time": "2026-09-01T00:00:00Z"} | self.created.get(argv[5], {})])
        if verb == ["doc", "add"]:
            doc = f"{len(self.docs):x}" * 40
            self.docs[doc] = stdin or ""
            (self.registers if REGISTER in labels else self.active).append(doc)
            self.added[doc] = argv[5]
            self.titles[doc] = argv[5]
            return json.dumps({"id": doc[:40]})
        if verb == ["doc", "edit"]:
            self.docs[argv[5]] = stdin or ""
            if "--title" in argv:
                self.titles[argv[5]] = argv[argv.index("--title") + 1]
            return ""
        if verb == ["doc", "supersede"]:
            if argv[5] not in self.stuck:
                self.active.remove(argv[5])
            return ""
        raise AssertionError(f"unexpected ccn call: {argv}")


def ask(key: str, **fields: str) -> dict:
    return {"key": f"ask/{key}", "fields": {"text": f"ask {key}", "lane": "lane-a", "accept": "check", "asked_at": "2026-10-01T00:00:00Z"} | fields}


@pytest.fixture
def drive_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / ".claude" / "scratch" / "brook" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "orca-desk.md").write_text(
        "- R1 (root) → desk: launch l01\n"
        "- R2 (standing) every landed PR is deployed in the same pass it lands\n"
        "- R3 (root) → desk: launch l02\n"
    )
    (inbox / "orca-desk.md.cursor").write_text("R2\n")
    (inbox / "landing-desk.md").write_text("- L1 (root) → desk: hold #1\n")
    plan = tmp_path / ".claude" / "plans" / "brook.md"
    plan.parent.mkdir()
    plan.write_text(
        "# brook\n\n- SoFi release on the owner's word\n- SoFi releases as it merges (4ffc9a5), never on the owner's word\n"
    )
    drives = tmp_path / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "d1.json").write_text(
        json.dumps({"drive": "d1", "ledger": LEDGER, "repo": "o/r", "checkout": REPO, "sessions": ["s-root"], "orca_run": "run_1"})
    )
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps(
            {
                "session_id": "s-root",
                "tasks": [{"id": "7", "status": "pending", "subject": "OWNER: release as merged"}],
                "background": [
                    {"type": "teammate", "status": "running", "description": "orca-desk-6"},
                    {"type": "monitor", "status": "running", "description": "batches.jsonl grep"},
                ],
            }
        )
    )
    return tmp_path


def generate(home: Path, shell: FakeCcn, *extra: str, capsys: pytest.CaptureFixture[str]) -> dict:
    argv = ["generate", "--program", "brook", "--plan", str(home / ".claude/plans/brook.md"), "--session", str(home / "session.json"), "--repo", REPO, *extra]
    assert handoff.main(argv, shell) == 0
    return json.loads(capsys.readouterr().out)


def shell_with(narrative: str = "") -> FakeCcn:
    rows = [ask("000001"), ask("000002", dropped_at="2026-10-01T01:00:00Z"), ask("000003", answered_at="2026-10-01T01:00:00Z")]
    return FakeCcn([RULE, OLD_RULE, OTHER], {"a" * 40: narrative or "# brook: progress\n\n## Root's next actions\n1. watch SoFi"}, rows)


def test_generate_writes_every_source_and_supersedes_the_previous_doc(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    out = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[out["id"]]
    assert shell.active == [out["id"]]
    assert Path(out["file"]).read_text() == body
    assert Path(out["file"]).parent == drive_home / ".claude/plans/brook-progress"
    assert shell.added[out["id"]].startswith("brook: progress ") and shell.added[out["id"]].endswith(" (generated)")
    for line in (
        "- 4ffc9a5 When does a merged change get released?",
        "- ec2881e Is any ledger a source of truth?",
        "- R2 (standing) every landed PR is deployed in the same pass it lands [orca-desk.md]",
        "- ask/000001 lane-a: ask 000001",
        "- #7 [pending] OWNER: release as merged",
        "- teammate: orca-desk-6 (running)",
        "- monitor: batches.jsonl grep (running)",
        "### orca-desk.md: head R3, cursor R2",
        "### landing-desk.md: head L1, cursor -",
        "- plan owner-gate line cites no live answer: brook.md:3: - SoFi release on the owner's word",
        "- Drive `d1`: ledger `1a2b3c4`, Orca run `run_1`, checkout `/repo`, root sessions s-root",
    ):
        assert line in body
    assert handoff.CATCH_UP.format(directory=drive_home / ".claude/scratch/brook/inbox") in body
    assert "Unrelated" not in body
    assert "000002" not in body and "000003" not in body
    assert "never on the owner's word" not in body.split("## Lint findings")[1]
    assert body.split("## Root narrative\n")[1].strip() == f"_From doc aaaaaaa, carried forward._\n\n{shell.docs['a' * 40]}"


def test_the_roots_doc_gains_the_generated_sections_in_place_and_supersedes_the_rest(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.docs["b" * 40] = "## Root's next actions\n1. land l11"
    shell.active.append("b" * 40)

    out = generate(drive_home, shell, "--narrative-doc", "b" * 40, capsys=capsys)

    assert out["id"] == "b" * 40
    assert shell.active == ["b" * 40]
    assert not any(call[3:5] == ["doc", "add"] and REGISTER not in call for call in shell.calls)
    assert ["ccn", "-R", REPO, "doc", "supersede", "a" * 40, "--by", "b" * 40] in shell.calls
    body = shell.docs["b" * 40]
    assert body.count("## Standing owner rules") == 1
    assert body.endswith("_From doc bbbbbbb._\n\n## Root's next actions\n1. land l11\n")
    assert "Then read the progress doc `ccn doc show bbbbbbb`" in out["digest"]


def handwritten(shell: FakeCcn, session: str | None, age: timedelta) -> str:
    doc = "b" * 40
    shell.docs[doc] = "## Root's next actions\n1. land l11"
    shell.active.append(doc)
    shell.created[doc] = {"session": session, "time": (datetime.now(timezone.utc) - age).isoformat()}
    return doc


def test_a_hand_written_doc_this_session_wrote_minutes_ago_is_augmented_and_the_generated_doc_superseded(
    drive_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = shell_with()
    shell.titles["a" * 40] = "brook: progress 2026-10-01T0000Z (generated)"
    record = handwritten(shell, "s-root", timedelta(minutes=8))

    first = generate(drive_home, shell, "--generated-doc", "a" * 40, capsys=capsys)
    second = generate(drive_home, shell, "--generated-doc", first["id"], capsys=capsys)

    assert first["id"] == second["id"] == record
    assert shell.active == [record]
    assert ["ccn", "-R", REPO, "doc", "supersede", "a" * 40, "--by", record] in shell.calls
    assert shell.titles.get(record, "brook: progress") == "brook: progress"
    body = shell.docs[record]
    assert body.count("## Standing owner rules") == 1 and body.count("_From ") == 1
    assert body.endswith("_From doc bbbbbbb._\n\n## Root's next actions\n1. land l11\n")


@pytest.mark.parametrize(
    ("session", "age"), [("s-other", timedelta(minutes=8)), ("s-root", timedelta(minutes=45)), (None, timedelta(minutes=8))]
)
def test_a_hand_written_doc_from_another_session_or_past_the_window_is_superseded(
    drive_home: Path, capsys: pytest.CaptureFixture[str], session: str | None, age: timedelta
) -> None:
    shell = shell_with()
    record = handwritten(shell, session, age)

    out = generate(drive_home, shell, capsys=capsys)

    assert out["id"] != record
    assert shell.active == [out["id"]]
    assert shell.titles[out["id"]].endswith(" (generated)")
    assert ["ccn", "-R", REPO, "doc", "supersede", record, "--by", out["id"]] in shell.calls


def test_a_hand_written_doc_written_since_the_previous_compaction_is_augmented_past_the_window(
    drive_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = shell_with()
    record = handwritten(shell, "s-root", timedelta(minutes=45))
    since = (datetime.now(timezone.utc) - timedelta(minutes=50)).isoformat()

    out = generate(drive_home, shell, "--fresh-since", since, capsys=capsys)

    assert out["id"] == record
    assert shell.active == [record]


def test_generate_fails_loudly_when_another_progress_doc_stays_active(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.stuck.add("a" * 40)
    argv = ["generate", "--program", "brook", "--plan", str(drive_home / ".claude/plans/brook.md"), "--repo", REPO]

    assert handoff.main(argv, shell) == handoff.SEVERAL_ACTIVE

    generated = shell.active[-1]
    assert capsys.readouterr().out.startswith(
        f"2 active progress:brook docs after generation, expected only {generated[:7]}: aaaaaaa, {generated[:7]}; "
    )


def test_the_sessions_generated_doc_is_edited_in_place_not_chained(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    first = generate(drive_home, shell, capsys=capsys)

    second = generate(drive_home, shell, "--generated-doc", first["id"], capsys=capsys)
    third = generate(drive_home, shell, "--generated-doc", first["id"], capsys=capsys)

    assert first["id"] == second["id"] == third["id"]
    assert shell.active == [first["id"]]
    assert sum(call[3:5] == ["doc", "add"] for call in shell.calls) == 2
    assert sum(call[3:5] == ["doc", "edit"] and call[5] == first["id"] for call in shell.calls) == 2
    assert shell.registers == [first["register"]]
    assert shell.titles[first["id"]].endswith(" (generated)")
    body = shell.docs[first["id"]]
    assert body.count("## Standing owner rules") == 1
    assert "_From doc aaaaaaa, carried forward._" in body


def test_a_generated_doc_that_is_no_longer_active_is_replaced(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    out = generate(drive_home, shell, "--generated-doc", "9" * 40, capsys=capsys)

    assert out["id"] != "9" * 40
    assert not any(call[3:5] == ["doc", "edit"] for call in shell.calls)


def test_a_carried_narrative_is_only_the_previous_narrative_section(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    first = generate(drive_home, shell, capsys=capsys)

    second = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[second["id"]]
    assert body.count("## Standing owner rules") == 1
    assert body.count("1. watch SoFi") == 1
    assert body.count("_From ") == 1
    assert "_From doc aaaaaaa, carried forward._" in body
    assert first["id"] != second["id"]


def test_folder_mode_calls_no_ccn(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    out = generate(drive_home, shell, "--folder", capsys=capsys)

    assert out["id"] is None
    assert shell.calls == []
    assert "- R2 (standing)" in Path(out["file"]).read_text()
    assert out["register"] is None
    assert out["digest"].startswith(
        f"Compacted long-running drive `brook`. Before acting, read the standing rules register `{out['register_file']}`: "
    )
    assert f"Then read the generated handoff `{out['file']}`" in out["digest"]


def test_digest_names_the_register_first(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = generate(drive_home, shell_with(), capsys=capsys)

    assert out["digest"].splitlines() == [
        f"Compacted long-running drive `brook`. Before acting, read the standing rules register `ccn doc show {out['register'][:7]}`: "
        "it arrives verbatim with your next tool results, binds every lane brief, and outranks the summary. "
        f"Then read the progress doc `ccn doc show {out['id'][:7]}`, then `{drive_home}/.claude/plans/brook.md`. "
        "Reload Skill `long-running` if its rules are gone.",
        "Register: 2 owner answers, 1 live standing inbox rules.",
        "Open: 1 owner asks, 1 tasks, 1 lanes, 1 monitors, 1 lint findings.",
    ]


def test_digest_stays_inside_the_injected_context_budget() -> None:
    big = handoff.Handoff(
        program="release-v3",
        plan="/Users/someone/.claude/plans/" + "x" * 120 + ".md",
        at=handoff.datetime.now(handoff.timezone.utc),
        durable=[{"id": f"{n:07x}" + "0" * 33, "title": "Q" * 250} for n in range(120)],
        standing={f"R{n}": f"R{n} (standing) " + "rule text " * 60 for n in range(80)},
        asks=["ask"] * 40,
        tasks=[{}] * 200,
    )

    text = handoff.digest(big, "the progress doc `ccn doc show 1234567`", "`ccn doc show 7654321`")

    assert len(text.encode()) <= handoff.DIGEST_BUDGET
    assert text.startswith("Compacted long-running drive `release-v3`. Before acting, read the standing rules register `ccn doc show 7654321`")
    assert text.splitlines()[-2] == "Register: 120 owner answers, 80 live standing inbox rules."
    assert text.splitlines()[-1].startswith("Open: 40 owner asks, 200 tasks")
    assert handoff.DIGEST_BUDGET + 4500 + 3000 + 2 * len("\n\n") < 10_000


def test_lint_flags_the_plans_uncited_owner_gate_lines(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    out = generate(drive_home, shell, capsys=capsys)

    code = handoff.main(["lint", "--doc", out["id"], "--program", "brook", "--plan", str(drive_home / ".claude/plans/brook.md"), "--repo", REPO], shell)

    printed = capsys.readouterr().out
    assert code == 3
    assert "plan owner-gate line cites no live answer: brook.md:3: - SoFi release on the owner's word" in printed
    assert "missing durable rule" not in printed


def test_strict_refuses_a_narrative_with_an_uncited_owner_gate(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.docs["b" * 40] = "## Root's next actions\n1. ship sanddb on SoFi on the owner's word"
    shell.active.append("b" * 40)
    argv = ["generate", "--program", "brook", "--plan", str(drive_home / ".claude/plans/brook.md"), "--repo", REPO, "--narrative-doc", "b" * 40, "--strict"]

    assert handoff.main(argv, shell) == 3

    assert "owner-gate line cites no live answer id: 1. ship sanddb on SoFi on the owner's word" in capsys.readouterr().out
    assert not any(call[3:5] == ["doc", "add"] for call in shell.calls)
    assert not (drive_home / ".claude/plans/brook-progress").exists()


def test_a_rule_the_sources_dropped_is_carried_once_as_superseded(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    first = generate(drive_home, shell, capsys=capsys)
    inbox = drive_home / ".claude/scratch/brook/inbox/orca-desk.md"
    inbox.write_text(inbox.read_text() + "- R4 R2 superseded by R5\n- R5 (standing) deploy every landing within five minutes\n")
    shell.answers.remove(OLD_RULE)

    second = generate(drive_home, shell, capsys=capsys)
    third = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[second["id"]]
    assert "- R2 superseded by R5" in body
    assert "- R5 (standing) deploy every landing within five minutes [orca-desk.md]" in body
    assert "- ec2881e superseded by nothing: the sources dropped it after the previous handoff (was: ec2881e Is any ledger a source of truth?)" in body
    assert "## Lint findings\n- plan owner-gate line" in body
    assert "superseded by" not in "\n".join(handoff.standing.section(shell.docs[third["id"]]))
    assert first["id"] != second["id"] != third["id"]


def strict(home: Path, shell: FakeCcn) -> int:
    argv = ["generate", "--program", "brook", "--plan", str(home / ".claude/plans/brook.md"), "--repo", REPO, "--narrative-doc", "b" * 40, "--strict"]
    return handoff.main(argv, shell)


@pytest.mark.parametrize(
    "fix",
    [
        "- R9 (standing) supersedes R8 as the cited form: no release waits on the owner's word (answer 4ffc9a5)\n",
        "- R8 (standing) every release ships on the owner's word only once (answer 4ffc9a5)\n",
    ],
)
def test_strict_names_an_uncited_inbox_rule_by_file_and_line_until_a_cited_line_supersedes_it(
    drive_home: Path, capsys: pytest.CaptureFixture[str], fix: str
) -> None:
    shell = shell_with()
    shell.docs["b" * 40] = "## Root's next actions\n1. land l11"
    shell.active.append("b" * 40)
    inbox = drive_home / ".claude/scratch/brook/inbox/orca-desk.md"
    inbox.write_text(inbox.read_text() + "- R8 (standing) every release ships on the owner's word only once\n")

    assert strict(drive_home, shell) == 3

    [finding] = capsys.readouterr().out.splitlines()
    assert finding.startswith(f"{inbox}:4: standing rule R8 is an owner-gate line that cites no live answer id: ")
    assert "supersedes R8" in finding and "ccn doc edit" not in finding

    inbox.write_text(inbox.read_text() + fix)

    assert strict(drive_home, shell) == 0


def test_strict_names_the_narrative_line_and_its_edit(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.docs["b" * 40] = "## Root's next actions\n1. land l11\n2. ship sanddb on SoFi on the owner's word"
    shell.active.append("b" * 40)

    assert strict(drive_home, shell) == 3

    assert capsys.readouterr().out.strip() == (
        "narrative (doc bbbbbbb) line 3: owner-gate line cites no live answer id: 2. ship sanddb on SoFi on the owner's "
        "word; end that line with `(answer <id>)` via `ccn doc edit bbbbbbbb --body -`"
    )


def test_the_register_quotes_every_ruling_in_full_including_this_drives_unlabelled_picks(
    drive_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = FakeCcn([RULE, OLD_RULE, OTHER, PICK, FOREIGN], {"a" * 40: "## Root's next actions\n1. watch SoFi"}, [])

    out = generate(drive_home, shell, capsys=capsys)

    register = shell.docs[out["register"]]
    assert shell.registers == [out["register"]]
    assert Path(out["register_file"]).read_text() == register
    assert register.startswith("## Standing owner rules\n")
    for line in (
        "- ec2881e Is any ledger a source of truth?\n  > No.\n  >\n  > Rule: Pulumi state is the only truth; the ledger only records and renders.\n",
        "- e0e57c8 Card 6, selection: confirm one resolver?\n  > One set; closure exclusions allowed (Recommended)\n",
        "- R2 (standing) every landed PR is deployed in the same pass it lands [orca-desk.md]\n",
    ):
        assert line in register
    assert "Another drive's pick?" not in register and "Unrelated" not in register
    assert register in shell.docs[out["id"]]


def test_a_compaction_round_trip_keeps_the_register_byte_for_byte(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = FakeCcn([RULE, OLD_RULE, PICK], {"a" * 40: "## Root's next actions\n1. watch SoFi"}, [])
    before = generate(drive_home, shell, capsys=capsys)
    register = shell.docs[before["register"]]

    after = generate(drive_home, shell, "--generated-doc", before["id"], capsys=capsys)

    assert after["register"] == before["register"]
    assert shell.docs[after["register"]] == register
    assert not any(call[3:6] == ["doc", "edit", before["register"]] for call in shell.calls)
    assert register in shell.docs[after["id"]]
    assert standing.section(shell.docs[after["id"]]) == standing.section(register)
