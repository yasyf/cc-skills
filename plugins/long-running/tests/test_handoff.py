from __future__ import annotations

import json
import os
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import handoff
import ledger
import plans
import progress
import pytest
import standing
from ccn_plans import PLAN_ID, CcnPlans, plan, task

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
REGISTER = "standing-rules:brook"
REGISTER_DOC = "c" * 40
REGISTER_BODY = "# brook register\n\n1. Release as it merges, never on the owner's word (4ffc9a5).\n2. Pulumi state is the only truth (ec2881e).\n"
PRE_COMPACT = """# ssql drive: pre-compact handoff 2026-10-10T2215Z (supersedes a3f05a3)

Read first after the plan. Drive ssql-hacks, root session b577ef71.

## Standing owner rules

- Never cancel a release build (answer 4cd6a3d).
- Dormant reads wake and migrate on every target (answer b70e998).

## A. Every owner instruction, with receipts

### Earlier windows (from 7033e8a, still binding)
1. Overview of ssql migrations vs plan. DONE earlier.
2. "what is a squash". ANSWERED.

### This window (2026-10-10)
1. "are you waiting for anything from me?" ANSWERED: no.

## B. Today's incident: release 5177 SandSQL outage (retro, IN PROGRESS)

### Timeline (Pacific)
- 7:56 AM: release 5117 stuck at approve blocks.

### Impact
8,421 mint 503s.

## C. Other in-flight work

| item | state | owner / next |
|---|---|---|
| Registry stack R2 | green | landing-desk |

## F. Lessons
- Verify an Orca launch actually produced a lane within a minute.

## G. Open owner-facing items
- #23 reply to Andrew: the copy is drafted and unsent."""

def creation_entry(body: str) -> dict:
    return {"kind": "create", "time": "2026-09-01T00:00:00Z", "changes": [{"field": "body", "to": body}]}


class FakeCcn(ledger.Shell):
    def __init__(self, answers: list[dict], docs: dict[str, str], rows: list[dict]) -> None:
        self.answers = answers
        self.docs = dict(docs)
        self.active = list(docs)
        self.rows = rows
        self.calls: list[list[str]] = []
        self.added: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.tags: dict[str, list[str]] = {}
        self.supersedes: dict[str, list[str]] = {}
        self.updated: dict[str, str] = {}
        self.stuck: set[str] = set()
        self.history: dict[str, list[dict]] = {}
        self.registers: list[str] = []
        self.register_label = REGISTER
        self.standing: list[dict] = [{"seq": 2, "kind": "go", "text": "every landed PR is deployed in the same pass it lands", "refs": {}}]
        self.notes = CcnPlans([])
        self.lane_records: list[dict] = []
        self.register_rows: dict = {"ledger": None, "program": "brook", "rows": {}, "classified": []}
        self.rows_error: str | None = None

    def rule(self, kind: str, text: str, **fields) -> None:
        self.standing.append({"seq": len(self.standing) + 2, "kind": kind, "text": text, "refs": {}, **fields})

    def write(self, doc: str, body: str, title: str = "brook: progress", supersedes: tuple[str, ...] = ()) -> None:
        self.docs[doc], self.titles[doc] = body, title
        self.active.append(doc)
        for older in supersedes:
            self.active.remove(older)
            self.supersedes.setdefault(doc, []).append(older)

    def touch(self, doc: str) -> None:
        self.updated[doc] = f"2026-10-02T00:{len(self.updated):02d}:00Z"

    def record(self, doc: str) -> dict:
        created = f"2026-10-01T00:{list(self.docs).index(doc):02d}:00Z"
        newer = sorted(new for new, olds in self.supersedes.items() if doc in olds)
        return {
            "id": doc,
            "title": self.titles.get(doc, "brook: progress"),
            "body": self.docs[doc],
            "tags": self.tags.get(doc, [self.register_label] if doc in self.registers else ["progress:brook"]),
            "created_at": created,
            "updated_at": self.updated.get(doc, created),
        } | ({"superseded_by": newer} if newer else {})

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        self.calls.append(argv)
        if argv[:2] == ["cci", "tail"]:
            assert argv[3] == "brook"
            since = argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1]
            if "--topic=standing" not in argv:
                return "".join(json.dumps({"drive": "brook", **record}) + "\n" for record in self.lane_records if (record["seq"] > int(since) if since.isdigit() else record["at"] >= since))
            return "".join(json.dumps({"drive": "brook", "topic": "standing", **record}) + "\n" for record in self.standing if record["seq"] > int(since))
        if argv[1:3] == ["ledger", "show"]:
            return json.dumps({"id": argv[3], "rows": self.rows})
        if argv[1:3] == [str(standing.RULINGS), "rows"]:
            if self.rows_error:
                raise subprocess.CalledProcessError(2, argv, stderr=self.rows_error)
            return json.dumps(self.register_rows)
        assert argv[1:3] == ["-R", REPO], argv
        verb = argv[3:5]
        labels = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--label"]
        if verb == ["answer", "list"]:
            return json.dumps([a for a in self.answers if all(label in a["tags"] for label in labels)])
        if verb[0] in ("task", "plan"):
            return self.notes(*argv[3:])
        if verb == ["doc", "list"] and "--full" in argv:
            listed = self.registers if self.register_label in labels else self.active
            if "--include-superseded" in argv:
                listed = [*listed, *(doc for doc in self.docs if doc not in listed and self.record(doc).get("superseded_by"))]
            return json.dumps([record for doc in listed if all(label in (record := self.record(doc))["tags"] for label in labels)])
        if verb == ["doc", "list"]:
            return json.dumps(
                [
                    {key: self.record(doc)[key] for key in ("id", "title", "updated_at")}
                    for doc in (self.registers if self.register_label in labels else self.active)
                ]
            )
        if verb == ["doc", "show"]:
            return json.dumps(self.record(argv[5]) | {"supersedes": sorted(self.supersedes.get(argv[5], []))})
        if verb == ["doc", "history"]:
            return json.dumps(self.history.get(argv[5]) or [creation_entry(self.docs[argv[5]])])
        if verb == ["doc", "add"]:
            doc = f"{len(self.docs):x}" * 40
            self.docs[doc] = stdin or ""
            self.history[doc] = [creation_entry(self.docs[doc])]
            (self.registers if self.register_label in labels else self.active).append(doc)
            self.added[doc] = argv[5]
            self.titles[doc] = argv[5]
            self.touch(doc)
            return json.dumps({"id": doc[:40]})
        if verb == ["doc", "edit"]:
            change = {"field": "body", "from": self.docs[argv[5]], "to": stdin or ""}
            self.history.setdefault(argv[5], [creation_entry(self.docs[argv[5]])]).insert(0, {"kind": "edit", "time": "2026-10-01T00:00:00Z", "changes": [change]})
            self.docs[argv[5]] = stdin or ""
            if "--title" in argv:
                self.titles[argv[5]] = argv[argv.index("--title") + 1]
            self.touch(argv[5])
            return ""
        if verb == ["doc", "supersede"]:
            if argv[5] not in self.stuck:
                self.active.remove(argv[5])
            self.supersedes.setdefault(argv[7], []).append(argv[5])
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
        json.dumps({"drive": "d1", "ledger": LEDGER, "repo": "o/r", "checkout": REPO, "sessions": ["s-root"], "orca_run": "run_1", "state_dir": str(tmp_path / "state" / "brook")})
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


def generated_record(narrative: str) -> str:
    return (
        "# brook: progress 2026-10-01T0000Z (generated)\n\n"
        f"{handoff.GENERATED_LINE}2026-10-01T00:00:00Z. Every section above `## Root narrative` is rebuilt at each handoff.\n\n"
        f"## Root narrative\n\n_From doc aaaaaaa._\n\n{narrative}\n"
    )


def shell_with(narrative: str = "", register: bool = True, rows: list[dict] | None = None) -> FakeCcn:
    rows = rows or [ask("000001"), ask("000002", dropped_at="2026-10-01T01:00:00Z"), ask("000003", answered_at="2026-10-01T01:00:00Z")]
    shell = FakeCcn([RULE, OLD_RULE, OTHER], {"a" * 40: generated_record(narrative or "## Root's next actions\n1. watch SoFi")}, rows)
    shell.titles["a" * 40] = "brook: progress 2026-10-01T0000Z (generated)"
    if register:
        shell.docs[REGISTER_DOC] = REGISTER_BODY
        shell.registers.append(REGISTER_DOC)
    return shell


def test_generate_writes_every_source_into_the_active_generated_doc(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    out = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[out["id"]]
    assert out["id"] == "a" * 40
    assert shell.active == [out["id"]] and shell.added == {}
    assert Path(out["file"]).read_text() == body
    assert Path(out["file"]).parent == drive_home / ".claude/plans/brook-progress"
    assert shell.titles[out["id"]].startswith("brook: progress ") and shell.titles[out["id"]].endswith(" (generated)")
    assert (out["read_first"], out["narrative"]) == ([], "aaaaaaa")
    for line in (
        "Register `ccn doc show ccccccc`: 2 owner-approved rules, delivered verbatim after every compaction.\n",
        "- #2 [cci #2]",
        "- ask/000001 lane-a: ask 000001",
        "## Open tasks\n- none in progress\n- 1 pending in `TaskList`\n",
        "- teammate: orca-desk-6",
        "- monitor: batches.jsonl grep",
        "- orca-desk.md: head R3, cursor R2: - R3 (root) → desk: launch l02",
        "- landing-desk.md: head L1, cursor -: - L1 (root) → desk: hold #1",
        "- plan owner-gate line cites no live answer: brook.md:3: - SoFi release on the owner's word",
        "- Drive `d1`: ledger `1a2b3c4`, Orca run `run_1`, checkout `/repo`, root sessions s-root\n\n## Open owner asks\n",
    ):
        assert line in body
    assert handoff.CATCH_UP.format(drive="brook") in body
    assert "Unrelated" not in body
    assert "000002" not in body and "000003" not in body
    assert "never on the owner's word" not in body.split("## Lint findings")[1]
    assert body.split("## Root narrative\n")[1].strip() == "_From doc aaaaaaa, carried forward._\n\n## Root's next actions\n1. watch SoFi"


def test_generate_reads_the_register_of_the_drives_program_not_the_plan_stem(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = drive_home / ".claude/long-running/drives/d1.json"
    registry.write_text(json.dumps(json.loads(registry.read_text()) | {"state_dir": str(drive_home / "state" / "durable-program")}))
    shell = shell_with()
    shell.register_label = "standing-rules:durable-program"

    out = generate(drive_home, shell, capsys=capsys)

    assert "Register `ccn doc show ccccccc`: 2 owner-approved rules" in shell.docs[out["id"]]
    labels = [call[6] for call in shell.calls if call[3:6] == ["doc", "list", "--label"] and call[6].startswith("standing-rules:")]
    assert labels and set(labels) == {"standing-rules:durable-program"}


def test_the_handoff_names_no_dashboard_even_when_one_is_serving(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = drive_home / "state" / "brook" / "dashboard" / "server.json"
    server.parent.mkdir(parents=True)
    server.write_text(json.dumps({"url": "http://127.0.0.1:8123/", "tailnet_url": "http://mac.tail1.ts.net:8123/"}))
    shell = shell_with()

    out = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[out["id"]]
    assert "Dashboard" not in body and "live-dashboard" not in body and "8123" not in body


def test_the_roots_doc_gains_the_generated_sections_in_place_and_supersedes_the_rest(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.write("b" * 40, "## Root's next actions\n1. land l11")

    out = generate(drive_home, shell, capsys=capsys)

    assert out["id"] == "b" * 40
    assert shell.active == ["b" * 40]
    assert not any(call[3:5] == ["doc", "add"] for call in shell.calls)
    assert ["ccn", "-R", REPO, "doc", "supersede", "a" * 40, "--by", "b" * 40] in shell.calls
    body = shell.docs["b" * 40]
    assert body.count("## Standing owner rules") == 1
    assert body.endswith("_From doc bbbbbbb._\n\n## Root's next actions\n1. land l11\n")
    assert "Then read the progress doc `ccn doc show bbbbbbb`" in out["digest"]


def test_a_hand_written_doc_is_augmented_whichever_session_wrote_it_and_carried_in_place_after(
    drive_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = shell_with()
    shell.write("b" * 40, "## Root's next actions\n1. land l11")

    first = generate(drive_home, shell, capsys=capsys)
    second = generate(drive_home, shell, capsys=capsys)

    assert first["id"] == second["id"] == "b" * 40
    assert shell.active == ["b" * 40] and shell.added == {}
    assert ["ccn", "-R", REPO, "doc", "supersede", "a" * 40, "--by", "b" * 40] in shell.calls
    assert shell.titles["b" * 40] == "brook: progress"
    body = shell.docs["b" * 40]
    assert body.count("## Standing owner rules") == 1 and body.count("_From ") == 1
    assert body.endswith("_From doc bbbbbbb, carried forward._\n\n## Root's next actions\n1. land l11\n")
    assert first["narrative"] == second["narrative"] == "bbbbbbb"


def test_a_hand_written_pre_compact_handoff_stays_verbatim_under_the_generated_sections_at_every_compaction(
    drive_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = shell_with(register=False)
    shell.write("b" * 40, PRE_COMPACT)

    first = generate(drive_home, shell, capsys=capsys)
    fresh = shell.docs["b" * 40]
    second = generate(drive_home, shell, capsys=capsys)
    third = generate(drive_home, shell, capsys=capsys)

    assert first["id"] == second["id"] == third["id"] == "b" * 40
    assert fresh.split(f"{handoff.NARRATIVE}\n")[1] == f"\n_From doc bbbbbbb._\n\n{PRE_COMPACT}\n"
    carried = shell.docs["b" * 40]
    assert carried.split(f"{handoff.NARRATIVE}\n")[1] == f"\n_From doc bbbbbbb, carried forward._\n\n{PRE_COMPACT}\n"
    assert "## Folded narrative" not in carried and "## Carried binding sections" not in carried
    assert standing.section(carried)[:1] == [
        "- no `standing-rules` register doc; the root's own `## Standing owner rules` follows verbatim under `## Root narrative`"
    ]


def test_generate_fails_loudly_when_another_progress_doc_stays_active(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.write("b" * 40, "## Root's next actions\n1. land l11")
    shell.stuck.add("a" * 40)
    argv = ["generate", "--program", "brook", "--plan", str(drive_home / ".claude/plans/brook.md"), "--repo", REPO]

    assert handoff.main(argv, shell) == handoff.SEVERAL_ACTIVE

    assert capsys.readouterr().out.startswith("2 active progress:brook docs after generation, expected only bbbbbbb: aaaaaaa, bbbbbbb; ")


def test_the_active_generated_doc_is_edited_in_place_not_chained(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    first = generate(drive_home, shell, capsys=capsys)

    second = generate(drive_home, shell, capsys=capsys)
    third = generate(drive_home, shell, capsys=capsys)

    assert first["id"] == second["id"] == third["id"]
    assert shell.active == [first["id"]]
    assert not any(call[3:5] == ["doc", "add"] for call in shell.calls)
    assert sum(call[3:5] == ["doc", "edit"] and call[5] == first["id"] for call in shell.calls) == 3
    assert first["register"] == REGISTER_DOC and shell.registers == [REGISTER_DOC]
    assert shell.titles[first["id"]].endswith(" (generated)")
    body = shell.docs[first["id"]]
    assert body.count("## Standing owner rules") == 1
    assert "_From doc aaaaaaa, carried forward._" in body


def test_with_no_active_progress_doc_generation_adds_one(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.active.clear()

    out = generate(drive_home, shell, capsys=capsys)

    assert shell.active == [out["id"]] and shell.added[out["id"]].endswith(" (generated)")
    assert not any(call[3:5] == ["doc", "edit"] for call in shell.calls)
    assert shell.docs[out["id"]].endswith("## Root narrative\n\n_The root has written no narrative yet._\n")


def test_a_carried_narrative_is_only_the_previous_narrative_section(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    first = generate(drive_home, shell, capsys=capsys)

    second = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[second["id"]]
    assert body.count("## Standing owner rules") == 1
    assert body.count("1. watch SoFi") == 1
    assert body.count("_From ") == 1
    assert "_From doc aaaaaaa, carried forward._" in body
    assert first["id"] == second["id"]


def test_folder_mode_calls_no_ccn(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    out = generate(drive_home, shell, "--folder", capsys=capsys)

    assert out["id"] is None
    assert shell.calls == []
    assert out["register"] is None
    assert out["digest"].startswith(f"Compacted long-running drive `brook`. Before acting, read the generated handoff `{out['file']}`, then ")
    assert "- no `standing-rules` register doc" in Path(out["file"]).read_text()


def test_digest_names_the_register_first(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = generate(drive_home, shell_with(), capsys=capsys)

    assert out["digest"].splitlines() == [
        f"Compacted long-running drive `brook`. Before acting, read the standing rules register `ccn doc show {out['register'][:7]}`: "
        "it arrives verbatim with your next tool result, binds every lane brief, and outranks the summary. "
        f"Then read the progress doc `ccn doc show {out['id'][:7]}`, then `{drive_home}/.claude/plans/brook.md`. "
        "Reload Skill `long-running` if its rules are gone.",
        "Register: 2 owner-approved rules, 1 live standing rules.",
        "Open: 1 owner asks, 1 tasks, 1 lanes, 1 monitors, 1 lint findings.",
    ]


def test_digest_stays_inside_the_injected_context_budget() -> None:
    big = handoff.Handoff(
        program="release-v3",
        plan="/Users/someone/.claude/plans/" + "x" * 120 + ".md",
        at=handoff.datetime.now(handoff.timezone.utc),
        register={"id": "7654321" + "0" * 33, "body": "".join(f"{n}. rule\n" for n in range(1, 31))},
        standing={f"#{n}": f"#{n} " + "rule text " * 60 for n in range(80)},
        asks=["ask"] * 40,
        tasks=[{}] * 200,
    )

    text = handoff.digest(big, "the progress doc `ccn doc show 1234567`")

    assert len(text.encode()) <= handoff.DIGEST_BUDGET
    assert text.startswith("Compacted long-running drive `release-v3`. Before acting, read the standing rules register `ccn doc show 7654321`")
    assert text.splitlines()[-2] == "Register: 30 owner-approved rules, 80 live standing rules."
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
    shell.write("b" * 40, "## Root's next actions\n1. ship sanddb on SoFi on the owner's word")
    argv = ["generate", "--program", "brook", "--plan", str(drive_home / ".claude/plans/brook.md"), "--repo", REPO, "--strict"]

    assert handoff.main(argv, shell) == 3

    assert "owner-gate line cites no live answer id: 1. ship sanddb on SoFi on the owner's word" in capsys.readouterr().out
    assert not any(call[3:5] == ["doc", "add"] for call in shell.calls)
    assert not (drive_home / ".claude/plans/brook-progress").exists()


def test_a_rule_the_sources_dropped_is_carried_once_as_superseded(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    first = generate(drive_home, shell, capsys=capsys)
    shell.rule("correction", "deploy every landing within five minutes", re=2, refs={"ccn": "4ffc9a5"})

    second = generate(drive_home, shell, capsys=capsys)
    body = shell.docs[second["id"]]
    third = generate(drive_home, shell, capsys=capsys)

    assert "- #2 superseded by #3" in body
    assert "- #3 [ccn 4ffc9a5]" in body
    assert "## Lint findings\n- plan owner-gate line" in body
    assert "superseded by" not in "\n".join(handoff.standing.section(shell.docs[third["id"]]))
    assert first["id"] == second["id"] == third["id"]


def strict(home: Path, shell: FakeCcn) -> int:
    argv = ["generate", "--program", "brook", "--plan", str(home / ".claude/plans/brook.md"), "--repo", REPO, "--strict"]
    return handoff.main(argv, shell)


@pytest.mark.parametrize(
    "fix",
    [
        ("correction", "no release waits on the owner's word (answer 4ffc9a5)", {"re": 3}),
        ("go", "every release ships on the owner's word only once (answer 4ffc9a5)", {}),
    ],
)
def test_strict_names_an_uncited_standing_rule_until_a_cited_record_replaces_it(
    drive_home: Path, capsys: pytest.CaptureFixture[str], fix: tuple[str, str, dict]
) -> None:
    shell = shell_with()
    shell.write("b" * 40, "## Root's next actions\n1. land l11")
    shell.rule("go", "every release ships on the owner's word only once")

    assert strict(drive_home, shell) == 3

    [finding] = capsys.readouterr().out.splitlines()
    assert finding.startswith("cci #3: standing rule #3 requires owner approval but cites no live answer id: ")
    assert "--kind correction --re-seq 3 --topic standing" in finding and "ccn doc edit" not in finding

    kind, text, fields = fix
    if kind == "go":
        shell.standing.pop()
    shell.rule(kind, text, **fields)

    assert strict(drive_home, shell) == 0


def test_strict_names_the_narrative_line_and_its_edit(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.write("b" * 40, "## Root's next actions\n1. land l11\n2. ship sanddb on SoFi on the owner's word")

    assert strict(drive_home, shell) == 3

    assert capsys.readouterr().out.strip() == (
        "narrative (doc bbbbbbb) line 3: owner-gate line cites no live answer id: 2. ship sanddb on SoFi on the owner's "
        "word; end that line with `(answer <id>)` via `ccn doc edit bbbbbbbb --body -`"
    )


def test_the_handoff_names_the_register_doc_and_never_writes_it(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.registers.insert(0, "e" * 40)
    shell.docs["e" * 40] = "# an older draft\n"
    shell.updated["e" * 40] = "2026-09-30T00:00:00Z"

    before = generate(drive_home, shell, capsys=capsys)
    before_body = shell.docs[before["id"]]
    after = generate(drive_home, shell, capsys=capsys)

    assert before["register"] == after["register"] == REGISTER_DOC
    assert not any(call[3:5] in (["doc", "add"], ["doc", "edit"], ["doc", "supersede"]) and REGISTER_DOC in call for call in shell.calls)
    assert shell.docs[REGISTER_DOC] == REGISTER_BODY
    section = "\n".join(standing.section(shell.docs[after["id"]]))
    assert standing.pointer({"id": REGISTER_DOC, "body": REGISTER_BODY}) in section
    assert REGISTER_BODY.splitlines()[2] not in shell.docs[after["id"]]
    assert "an older draft" not in section
    body = handoff.lint_view(shell.docs[after["id"]])
    assert standing.lint(body, before_body, {"id": REGISTER_DOC, "body": REGISTER_BODY}, {RULE["id"]}) == []


def test_a_hand_written_narrative_is_carried_verbatim_never_folded(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    narrative = (
        "## 10:17 PM dump 2\n\n### Owner rulings since 9 PM (verbatim, binding)\n- \"ship it\"\n\n### Program state\nCensus 231/293. Lanes a, b.\n\n"
        "## 11:57 PM dump 3\n\n### Program state\nCensus 240/293.\n" + "".join(f"- lane {n}: still running\n" for n in range(2000))
    )
    shell = shell_with()
    shell.write("b" * 40, narrative)

    first = generate(drive_home, shell, capsys=capsys)
    second = generate(drive_home, shell, capsys=capsys)

    assert first["id"] == second["id"] == "b" * 40
    assert len(narrative.encode()) > progress.CAP
    assert shell.docs["b" * 40].split("\n## Root narrative\n")[1] == f"\n_From doc bbbbbbb, carried forward._\n\n{narrative.strip()}\n"


def test_generate_refuses_generated_sections_over_the_cap_and_writes_nothing(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with(rows=[ask("000001", text="x" * progress.CAP)])
    argv = ["generate", "--program", "brook", "--plan", str(drive_home / ".claude/plans/brook.md"), "--session", str(drive_home / "session.json"), "--repo", REPO]

    assert handoff.main(argv, shell) == handoff.OVERSIZED

    assert "its largest section is `Open owner asks` at " in capsys.readouterr().out
    assert not any(call[3:5] in (["doc", "add"], ["doc", "edit"]) for call in shell.calls)
    assert not (drive_home / ".claude/plans/brook-progress").exists()


FULL_HANDOFF = (
    "# Drive brook: pre-compact handoff, 2026-10-10 ~22:10Z (3:10pm PT)\n\n"
    "This supersedes progress doc aaaaaaa. Read aaaaaaa for everything before ledger ask 83.\n\n"
    "## 0. Binding rules added this segment (verbatim)\n\n- **363cc69**: the version-qualified PC replace is routine.\n\n"
    "## 1. Owner asks 83 through 103\n\n" + "".join(f"- ask/0000{n}: in flight on lane-{n}, PR #34{n}\n" for n in range(83, 104)) + "\n"
    "## 2. Live lanes\n\n- oncall-ic-dual-3 (124)\n- oncall-credential-durability-2 (122, 123)\n"
)
ADDENDUM = (
    "# Addendum to progress doc bbbbbbb (everything after 22:10Z; updated ~22:20Z)\n\n"
    "## Owner rulings since bbbbbbb\n\n- **824f65f**: the version-qualified PC replace is routine (consistent with 363cc69).\n\n"
    "## Results since bbbbbbb\n\n- **Retro DONE**: ccn doc **8bcade3** supersedes 21961bb, with the catalog in the appendix.\n\n"
    "## Live lanes\n\n- oncall-ic-dual-3 (124), oncall-credential-durability-2 (122, 123), oncall-card-domain (129, 115, 128)\n\n"
    "## Next actions for the root\n\n1. Keep the freeze until the release-v3 drive or the owner lifts it.\n"
)


@pytest.mark.parametrize("addendum_supersedes", [False, True])
def test_the_hand_written_handoff_and_its_addendum_survive_a_regenerating_compaction(
    drive_home: Path, capsys: pytest.CaptureFixture[str], addendum_supersedes: bool
) -> None:
    shell = shell_with()
    shell.write("b" * 40, FULL_HANDOFF, "brook: progress 2026-10-10T2210Z (pre-compact handoff, full)", supersedes=("a" * 40,))
    shell.write("9" * 40, ADDENDUM, "brook: progress 2026-10-10T2225Z addendum (read with bbbbbbb)", ("b" * 40,) if addendum_supersedes else ())

    stop = generate(drive_home, shell, capsys=capsys)
    compaction = generate(drive_home, shell, capsys=capsys)

    assert stop["id"] == compaction["id"] == "9" * 40
    assert shell.active == ["9" * 40] and shell.added == {}
    assert shell.docs["b" * 40] == FULL_HANDOFF
    assert shell.titles["9" * 40] == "brook: progress 2026-10-10T2225Z addendum (read with bbbbbbb)"
    body = shell.docs["9" * 40]
    assert f"{handoff.READ_FIRST}`ccn doc show bbbbbbb`: brook: progress 2026-10-10T2210Z (pre-compact handoff, full)\n" in body
    assert body.split("\n## Root narrative\n")[1] == f"\n_From doc 9999999, carried forward._\n\n{ADDENDUM.strip()}\n"
    for out in (stop, compaction):
        assert (out["read_first"], out["narrative"]) == (["ccn doc show bbbbbbb"], "9999999")
        assert "Then read the hand-written handoff `ccn doc show bbbbbbb`, then the progress doc `ccn doc show 9999999`, then " in out["digest"]


def test_generate_reads_the_whole_progress_lineage_in_one_listing_whatever_its_length(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc_calls = {}
    for length in (1, 6):
        shell = shell_with()
        chain = [str(n) * 40 for n in range(1, length + 1)]
        for older, doc in zip(["a" * 40, *chain], chain):
            shell.write(doc, f"## Root's next actions\n1. land {doc[:7]}", supersedes=(older,))

        out = generate(drive_home, shell, capsys=capsys)

        assert (out["id"], out["read_first"]) == (chain[-1], [f"ccn doc show {doc[:7]}" for doc in chain[:-1]])
        assert [call for call in shell.calls if call[3:5] == ["doc", "list"] and "--full" in call] == [
            ["ccn", "-R", REPO, "doc", "list", "--label", REGISTER, "--full", "--json"],
            ["ccn", "-R", REPO, "doc", "list", "--label", "progress:brook", "--include-superseded", "--full", "--json"],
        ]
        assert not any(call[3:5] == ["doc", "show"] for call in shell.calls)
        assert out["register"] == REGISTER_DOC
        doc_calls[length] = sum(call[3:4] == ["doc"] for call in shell.calls)
    assert doc_calls[1] == doc_calls[6]


PROGRESS = "progress:brook"


def stored(key: str, *, by: str = "", label: str = PROGRESS, deleted: bool = False, made: bool = False, updated: str = "2026-10-01T00:00:00Z") -> dict:
    return {
        "id": key * 40,
        "tags": [label],
        "body": generated_record(f"## Root's next actions\n1. {key}") if made else f"## Root's next actions\n1. {key}",
        "superseded_by": [newer * 40 for newer in by],
        "deleted": deleted,
        "created_at": updated,
        "updated_at": updated,
    }


def default_order(docs: list[dict]) -> list[dict]:
    return sorted(sorted(docs, key=lambda doc: doc["id"]), key=lambda doc: doc["updated_at"], reverse=True)


def show_walk(store: list[dict]) -> list[str]:
    live = {doc["id"]: doc for doc in store if not doc["deleted"]}
    seen, shown, queue = set(), [], [doc["id"] for doc in default_order(list(live.values())) if PROGRESS in doc["tags"] and not doc["superseded_by"]]
    while queue:
        if (doc_id := queue.pop()) in seen:
            continue
        seen.add(doc_id)
        if PROGRESS not in (doc := live[doc_id])["tags"]:
            continue
        shown.append(doc_id)
        if not handoff.generated(doc["body"]):
            queue += sorted(other for other, newer in live.items() if doc_id in newer["superseded_by"])
    return shown


def full_listing(store: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in doc.items() if key not in ("deleted", "superseded_by")} | ({"superseded_by": doc["superseded_by"]} if doc["superseded_by"] else {})
        for doc in default_order([doc for doc in store if not doc["deleted"] and PROGRESS in doc["tags"]])
    ]


def random_store(seed: int) -> list[dict]:
    rng = random.Random(seed)
    keys = [f"{n:x}" for n in range(rng.randint(1, 9))]
    stamps = [f"2026-10-01T0{n}:00:00Z" for n in range(3)]
    return [
        stored(
            key,
            by="".join(rng.sample(pool := [other for other in [*keys, "f"] if other != key], rng.randint(0, min(2, len(pool))))),
            label=rng.choice([PROGRESS, PROGRESS, "progress:other"]),
            deleted=rng.random() < 0.15,
            made=rng.random() < 0.3,
            updated=rng.choice(stamps),
        )
        for key in keys
    ]


LINEAGE_CASES = {
    "predecessor": [stored("a", by="b"), stored("b")],
    "cross-label-stop": [stored("a", by="b"), stored("b", by="c", label="progress:other"), stored("c")],
    "generated-stop": [stored("a", by="b"), stored("b", by="c", made=True), stored("c")],
    "inactive-through-missing-or-other-label": [stored("a", by="b"), stored("b", label="progress:other"), stored("d", by="f"), stored("e")],
    "tombstoned-predecessor": [stored("a", by="b", deleted=True), stored("b")],
    "equal-timestamps": [stored("a"), stored("b")],
    "reconverging": [stored("a", by="bc"), stored("b", by="c"), stored("c")],
    "cycle": [stored("a", by="b"), stored("b", by="a"), stored("c")],
}


@pytest.mark.parametrize("store", [*LINEAGE_CASES.values(), *(random_store(seed) for seed in range(300))], ids=[*LINEAGE_CASES, *(f"random-{seed}" for seed in range(300))])
def test_the_batch_lineage_walks_exactly_what_a_doc_show_per_record_walked(store: list[dict]) -> None:
    records = full_listing(store)
    active = [doc for doc in records if not doc.get("superseded_by")]

    assert [doc["id"] for doc in active] == [
        doc["id"] for doc in default_order(store) if not doc["deleted"] and PROGRESS in doc["tags"] and not doc["superseded_by"]
    ]
    assert [doc["id"] for doc in handoff.lineage(records, active)] == show_walk(store)


def test_the_batch_lineage_keeps_the_cases_the_contract_names() -> None:
    def walk(case: str) -> list[str]:
        records = full_listing(LINEAGE_CASES[case])
        return [doc["id"][0] for doc in handoff.lineage(records, [doc for doc in records if not doc.get("superseded_by")])]

    assert walk("predecessor") == ["b", "a"]
    assert walk("cross-label-stop") == ["c"]
    assert walk("generated-stop") == ["c", "b"]
    assert walk("inactive-through-missing-or-other-label") == ["e"]
    assert walk("tombstoned-predecessor") == ["b"]
    assert walk("equal-timestamps") == ["b", "a"]
    assert walk("reconverging") == ["c", "b", "a"]
    assert walk("cycle") == ["c"]


def test_open_tasks_list_only_in_progress_and_lanes_only_running_ten(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session = json.loads((drive_home / "session.json").read_text())
    session["tasks"] = [{"id": str(n), "status": "pending" if n < 12 else "in_progress", "subject": f"task {n}"} for n in range(13)]
    session["background"] = [{"type": "teammate", "status": "running", "description": f"lane-{n}"} for n in range(14)]
    session["background"].append({"type": "teammate", "status": "completed", "description": "lane-done"})
    (drive_home / "session.json").write_text(json.dumps(session))

    shell = shell_with()
    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    tasks = body.split("## Open tasks\n")[1].split("\n\n")[0].splitlines()
    assert tasks == ["- #12 task 12", "- 12 pending in `TaskList`"]
    lanes = body.split("## Lanes and monitors\n")[1].split("\n\n")[0].splitlines()
    assert lanes[0] == "- teammate: lane-13"
    assert lanes[-1] == "- 4 more running lanes" and len(lanes) == 11
    assert "lane-done" not in body


def test_a_standing_rule_is_named_by_id_and_its_text_stays_in_cci(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    assert "\n".join(standing.section(body)).endswith("- #2 [cci #2]")
    assert "every landed PR is deployed" not in body


class MinuteClock(datetime):
    ticks = 0

    @classmethod
    def now(cls, tz: timezone | None = None) -> datetime:
        cls.ticks += 1
        return datetime(2026, 10, 10, 22, cls.ticks, tzinfo=tz)


@pytest.mark.parametrize("above", [True, False])
def test_a_narrative_the_root_edits_into_the_generated_doc_is_fresh_once_and_stays_verbatim(
    drive_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, above: bool
) -> None:
    monkeypatch.setattr(handoff, "datetime", MinuteClock)
    shell = shell_with("## 10:00 PM dump 1\n\n### Program state\nCensus 200/293. Lanes a, b.\n")
    first = generate(drive_home, shell, capsys=capsys)
    doc = first["id"]
    head, carried = shell.docs[doc].split(f"{handoff.NARRATIVE}\n\n")
    carried = handoff.PROVENANCE.sub("", carried)
    if above:
        dump = "Pre-compact handoff written by main at 3:10 PM. Read all of it.\n\n### A. Current state\nReleases are paused.\n"
        edited = f"{head}{handoff.NARRATIVE}\n\n{dump}\n{carried}"
    else:
        dump = "## 3:10 PM dump 2\n\n### Current state\nReleases are paused.\n"
        edited = f"{head}{handoff.NARRATIVE}\n\n{carried}\n{dump}"
    shell.run(["ccn", "-R", REPO, "doc", "edit", doc, "--body", "-"], stdin=edited)

    second = generate(drive_home, shell, capsys=capsys)
    edited_narrative = handoff.narrative_of(shell.docs[doc])
    third = generate(drive_home, shell, capsys=capsys)

    assert (first["fresh"], second["fresh"], third["fresh"]) == (False, True, False)
    assert first["id"] == second["id"] == third["id"]
    narrative = shell.docs[doc].split(f"{handoff.NARRATIVE}\n")[1]
    assert narrative.startswith(f"\n_From doc {doc[:7]}, carried forward._\n\n")
    assert narrative == f"\n_From doc {doc[:7]}, carried forward._\n\n{edited_narrative}\n"
    assert narrative.count(dump.strip("\n")) == 1 and "\n## 10:00 PM dump 1" in narrative
    assert "## Folded narrative" not in narrative


def test_open_tasks_end_with_the_drive_plans_done_and_open_steps(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plans.SHOWN.clear()
    shell = shell_with()
    shell.notes = CcnPlans([task("a", "Ship the reader", plan=PLAN_ID, labels=("step:1",), status="done"), task("b", "Drop the column", plan=PLAN_ID, labels=("step:2",))], [plan()])

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    tasks = body.split("## Open tasks\n")[1].split("\n\n")[0].splitlines()
    assert tasks == ["- none in progress", "- 1 pending in `TaskList`", "- Plan `fc8714e` Migration: 1 of 2 tasks done, 0 blocked; steps open: 2 0/1"]


def lane_record(seq: int, lane: str, kind: str, text: str, minutes_ago: int) -> dict:
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"seq": seq, "lane": lane, "kind": kind, "text": text, "at": at}


def test_the_handoff_lists_each_incident_lane_with_its_last_record_and_age(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.lane_records = [
        lane_record(9, "inc-old-fix", "mechanism", "older than the window", 60 * 7),
        lane_record(10, "inc-liam-load-fix", "mechanism", "the fork reads the parent", 30),
        lane_record(11, "inc-liam-load-fix", "opened", "opened #41461", 20),
        lane_record(12, "inc-liam-load-opus", "mechanism", "SAND-59 is a fork", 14),
        lane_record(13, "enum-local-client", "mechanism", "the set is proven", 12),
        lane_record(14, "inc-chats-load-fix", "report", "CANDIDATE the sidecar drops the lease", 9),
        lane_record(15, "inc-chats-load-fix", "state", "still reading the sidecar log", 3),
    ]

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    assert (
        "## Incident lanes\n"
        "- `inc-chats-load-fix`: last state 3 min ago: still reading the sidecar log | CANDIDATE 9 min ago, no OPENED or BLOCKER since\n"
        "- `inc-liam-load-opus`: last mechanism 14 min ago: SAND-59 is a fork | MECHANISM 14 min ago, no OPENED or BLOCKER since\n"
        "- `inc-liam-load-fix`: last opened 20 min ago: opened #41461\n"
    ) in body
    assert "enum-local-client" not in body and "inc-old-fix" not in body


def test_a_drive_with_no_incident_lane_says_so(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    assert "## Incident lanes\n- none posted in the last 6 h\n" in body


def test_generate_clears_a_cursor_from_before_the_drive_that_no_line_sits_past_and_keeps_one_with_a_line_past_it(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Papercut 3dfdc2f, Oct 20: the orca desk restarted after an inbox rotation onto a `.cursor` file written Oct 1."""
    registry = drive_home / ".claude/long-running/drives/d1.json"
    registry.write_text(json.dumps(json.loads(registry.read_text()) | {"started_at": "2026-10-20T00:00:00Z"}))
    inbox = drive_home / ".claude/scratch/brook/inbox"
    stale, behind = inbox / "landing-desk.md.cursor", inbox / "orca-desk.md.cursor"
    stale.write_text("L1\n")
    written = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
    for cursor in (stale, behind):
        os.utime(cursor, (written, written))
    shell = shell_with()

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    assert not stale.exists() and behind.read_text() == "R2\n"
    for line in (
        "- orca-desk.md: head R3, cursor R2: - R3 (root) → desk: launch l02",
        "- landing-desk.md: head L1, cursor -: - L1 (root) → desk: hold #1",
        "- cleared stale cursor landing-desk.md.cursor at L1, written before the drive started at 2026-10-20T00:00:00Z",
    ):
        assert line in body


def test_generate_keeps_an_old_cursor_at_the_newest_ruling_when_a_line_follows_it(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = drive_home / ".claude/long-running/drives/d1.json"
    registry.write_text(json.dumps(json.loads(registry.read_text()) | {"started_at": "2026-10-20T00:00:00Z"}))
    inbox = drive_home / ".claude/scratch/brook/inbox"
    (inbox / "landing-desk.md").write_text("- L1 (root) → desk: hold #1\nMONITOR alert fired: queue stalled\n")
    cursor = inbox / "landing-desk.md.cursor"
    cursor.write_text("L1\n")
    written = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
    os.utime(cursor, (written, written))
    shell = shell_with()

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    assert cursor.read_text() == "L1\n" and "cleared stale cursor" not in body


def test_generate_keeps_a_read_through_cursor_written_after_the_drive_started(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = drive_home / ".claude/long-running/drives/d1.json"
    registry.write_text(json.dumps(json.loads(registry.read_text()) | {"started_at": "2026-10-20T00:00:00Z"}))
    cursor = drive_home / ".claude/scratch/brook/inbox/landing-desk.md.cursor"
    cursor.write_text("L1\n")
    shell = shell_with()

    body = shell.docs[generate(drive_home, shell, capsys=capsys)["id"]]

    assert cursor.read_text() == "L1\n" and "cleared stale cursor" not in body


AFF1D7D4 = Path(__file__).parent / "fixtures" / "handoff-aff1d7d4-shape.md"
ROW_WORDS = "the app shows no syncing state, and the downloading bar is the one transfer state"


def scope_rows(count: int) -> dict:
    rows = {}
    for n in range(count):
        implications = [{"n": i, "text": f"surface {n}.{i} shows no syncing state " + "x" * 80, "direction": "removes", "state": "proposed", "proof": "seed"} for i in range(1, 6)]
        rows[f"rule/{n:07x}"] = {
            "kind": "scope",
            "status": "in-force",
            "words": f"{ROW_WORDS}, row {n}",
            "answer": json.dumps([f"{n:07x}"]),
            "said": "1",
            "implications": json.dumps(implications),
            "names": json.dumps([f"syncing state {n}", "Syncing"]),
            "updated_at": "2026-10-01T00:00:00Z",
            "evidence": "seed",
        }
    return rows


def test_generate_prints_every_scope_row_in_the_owners_words_past_the_size_cap(
    drive_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(standing, "post_blocker", lambda program, text: posted.append((program, text)))
    shell = shell_with(register=False)
    shell.register_rows = {"ledger": "0d4cfee", "program": "brook", "rows": scope_rows(60), "classified": []}

    out = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[out["id"]]
    assert len(body.partition(f"\n{handoff.NARRATIVE}\n")[0].encode()) > progress.CAP
    assert "no `standing-rules` register doc" not in body
    for key, fields in shell.register_rows["rows"].items():
        assert f"- `{key}` (in-force, said 1): “{fields['words']}”\n" in body
        assert all(f"  - implication {i['n']}, removes, proposed: {i['text']}\n" in body for i in json.loads(fields["implications"]))
    assert standing.lint(handoff.lint_view(body), None, None, set(), standing.Rows(rows=shell.register_rows["rows"])) == []
    assert posted == []


def test_a_failed_register_read_still_generates_and_posts_one_blocker(
    drive_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(standing, "post_blocker", lambda program, text: posted.append((program, text)))
    shell = shell_with(register=False)
    shell.rows_error = "rulings.py: error: no register ledger for brook\n"

    out = generate(drive_home, shell, capsys=capsys)

    body = shell.docs[out["id"]]
    assert "## Standing owner rules\n\n- no `standing-rules` register doc\n\n- #2 [cci #2]\n\n- the register rows did not load (rulings.py: error: no register ledger for brook)" in body
    [(cci_drive, text)] = posted
    assert cci_drive == "brook" and text.startswith("The register rows for brook did not load for the handoff: rulings.py: error: no register ledger for brook.")
    assert [handoff.sys.executable, str(standing.RULINGS), "rows", "--drive", "d1", "--repo", REPO, "--json"] in shell.calls


def test_lint_fails_the_aff1d7d4_handoff_against_the_register(drive_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = shell_with()
    shell.register_rows = {
        "ledger": "0d4cfee",
        "program": "brook",
        "rows": {"rule/f958522": {"kind": "scope", "status": "in-force", "words": ROW_WORDS, "answer": '["f958522"]', "updated_at": "2026-10-01T00:00:00Z"}},
        "classified": [],
    }

    code = handoff.main(["lint", "--file", str(AFF1D7D4), "--program", "brook", "--repo", REPO], shell)

    printed = capsys.readouterr().out.splitlines()
    assert code == standing.VIOLATIONS
    assert 'the handoff says "no `standing-rules` register doc"; the register ledger holds the owner\'s rows, so regenerate the section with `handoff.py generate`' in printed
    assert (
        f"`f958522` cites without its words register row `rule/f958522`: - f958522: the app shows no syncing state.; quote the row's words verbatim: “{ROW_WORDS}”"
    ) in printed
