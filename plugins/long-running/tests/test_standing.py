from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import standing

RELEASE = "4ffc9a5" + "0" * 33
TICKETS = "104926a" + "0" * 33
LIVE = {RELEASE, TICKETS}
REGISTER = {"id": "0cf17c9" + "0" * 33, "body": "# Register\n\n1. Release as it merges, never on the owner's word (4ffc9a5).\n"}
CARRIED = standing.section_of(REGISTER, ["- R312 deploy every landing"])
UNQUOTED = "the standing rules section does not name register `0cf17c9`; regenerate the handoff with `handoff.py generate`"


def test_a_handoff_quoting_the_register_passes() -> None:
    body = CARRIED + "\n## Owner asks\nSoFi releases as it merges (4ffc9a5), never on the owner's word\n"

    assert standing.lint(body, CARRIED, REGISTER, LIVE) == []


def test_a_handoff_without_the_section_fails() -> None:
    [missing, unquoted] = standing.lint("## Owner asks\nnone\n", None, REGISTER, LIVE)

    assert missing.startswith("no `## Standing owner rules` section")
    assert unquoted == UNQUOTED


def test_a_section_without_the_register_fails() -> None:
    assert standing.lint("## Standing owner rules\n- R312 deploy every landing\n", None, REGISTER, LIVE) == [UNQUOTED]
    assert standing.lint("## Standing owner rules\n- R312 deploy every landing\n", None, None, LIVE) == []
    assert standing.lint("## Standing owner rules\n\n  > # Register\n", None, REGISTER, LIVE) == [UNQUOTED]


def test_dropping_a_carried_rule_needs_a_superseded_by_line() -> None:
    dropped = standing.section_of(REGISTER, [])
    superseded = standing.section_of(REGISTER, ["- R312 superseded by R575"])

    assert standing.lint(dropped, CARRIED, REGISTER, LIVE) == [
        "dropped `R312` since the previous handoff (- R312 deploy every landing); carry it, or write "
        "`- R312 superseded by <id>`"
    ]
    assert standing.lint(superseded, CARRIED, REGISTER, LIVE) == []
    assert standing.lint(dropped, superseded, REGISTER, LIVE) == []


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("real sanddb on SoFi (owner's word)", True),
        ("needs owner approval before enqueue", True),
        ("the SoFi release still requires owner sign-off", True),
        ("Card 1: reserved for the owner", True),
        ("released as it merges, no owner's word gate (4ffc9a5)", False),
        ("CODEOWNER approvals on #28756", False),
        ("owner approval missing (deadbee)", True),
        ("Where an audit row and an owner answer differ, the owner's words win.", False),
        ("When the owner's words list categories, the row names the surface", False),
        ("reserved for the owners of each lane", False),
        ("the owner's go-ahead on SoFi", True),
    ],
)
def test_an_owner_gate_line_must_cite_a_live_answer(line: str, flagged: bool) -> None:
    problems = standing.lint(CARRIED + "\n## Asks\n" + line + "\n", None, REGISTER, LIVE)

    assert problems == ([f"owner-gate line cites no live answer id: {line}"] if flagged else [])


def test_cli_lint_reads_the_register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def ccn(repo: str, *args: str) -> str:
        if args[:2] == ("doc", "show"):
            return json.dumps({"id": args[2], "body": "## Standing owner rules\n- none\n"})
        return json.dumps([{"id": RELEASE}, {"id": TICKETS}])

    monkeypatch.setattr(standing, "ccn", ccn)
    monkeypatch.setattr(standing.rulings, "register", lambda shell, repo, program: REGISTER)
    monkeypatch.setattr(standing, "register_rows", lambda run, repo, which, drive: None)

    assert standing.main(["lint", "--doc", "abc", "--program", "v3"]) == standing.VIOLATIONS
    assert capsys.readouterr().out == UNQUOTED + "\n"

    (tmp_path / "handoff.md").write_text(CARRIED)
    assert standing.main(["lint", "--file", str(tmp_path / "handoff.md"), "--program", "v3"]) == 0


def standing_records(*records: dict):
    def run(argv: list[str]) -> str:
        assert argv[:3] == ["cci", "tail", "--drive"] and argv[3] == "brook"
        assert argv[-3:] == ["--topic=standing", "--kind=go", "--kind=correction"]
        since = int(argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1])
        return "".join(json.dumps({"drive": "brook", "topic": "standing", **record}) + "\n" for record in records if record["seq"] > since)

    return run


def test_a_standing_rule_stays_live_until_a_correction_replaces_it() -> None:
    found = standing.read(
        standing_records(
            {"seq": 4, "kind": "go", "text": "deploy every landing", "refs": {"ccn": "4ffc9a5"}},
            {"seq": 7, "kind": "go", "text": "dev is always releasable", "refs": {}},
            {"seq": 9, "kind": "correction", "re": 4, "text": "deploy every landing within five minutes", "refs": {"ccn": "87833ea"}},
        ),
        "brook",
    )
    assert found.rules == {"#7": "#7 dev is always releasable", "#9": "#9 deploy every landing within five minutes"}
    assert found.sources == {"#7": "cci #7", "#9": "ccn 87833ea"}
    assert found.superseded == {"#4": "#9"}


def test_peer_mail_cci_tail_carries_is_not_a_standing_rule() -> None:
    peer = {"lane": "inc-5177-edge-fix", "text": "verification only", "refs": {}, "to": ["peer:b577ef71"]}
    found = standing.read(
        standing_records(
            {"seq": 4, "kind": "go", "text": "deploy every landing", "refs": {}},
            {**peer, "seq": 5, "drive": "release-v3", "topic": "", "kind": "state"},
            {**peer, "seq": 6, "drive": "release-v3", "kind": "go"},
            {**peer, "seq": 7, "topic": "", "kind": "go"},
            {**peer, "seq": 8, "kind": "decide"},
            {**peer, "seq": 9, "drive": "release-v3", "kind": "correction", "re": 4},
        ),
        "brook",
    )
    assert found.rules == {"#4": "#4 deploy every landing"}
    assert found.superseded == {}


def test_a_carried_cci_rule_id_is_a_bullet_id() -> None:
    assert standing.carried(["- #9 deploy every landing within five minutes [ccn 87833ea]"]) == {"#9": "- #9 deploy every landing within five minutes [ccn 87833ea]"}


FIXTURES = Path(__file__).parent / "fixtures"
AFF1D7D4 = FIXTURES / "handoff-aff1d7d4-shape.md"
SYNCING = "the app shows no syncing state, and the downloading bar is the one transfer state,\non a share or a chat older than 30 days"
CONTINUED = "no cloud worker continues a chat that started on a Mac, and no card or plan line mentions one"
RELEASES = "no release goes out until every scenario passes on every platform"
CARDS = "> a lane waits on an open card: \n\nnothing starts until every card is answered"


def implications(*lines: tuple[str, str]) -> str:
    return json.dumps([{"n": n, "text": text, "direction": direction, "state": "proposed" if n == 1 else "ticked", "proof": "seed"} for n, (direction, text) in enumerate(lines, 1)])


ROWS = standing.Rows(
    rows={
        "rule/f958522": {
            "kind": "scope",
            "status": "in-force",
            "words": SYNCING,
            "answer": '["f958522"]',
            "said": "2",
            "implications": implications(("removes", "No Syncing state appears anywhere in the UI."), ("permits", "The downloading bar shows on a share without the owner's word.")),
            "names": '["syncing state", "Syncing"]',
            "updated_at": "2026-10-23T11:58:25Z",
            "evidence": "seed",
        },
        "rule/4830123": {
            "kind": "scope",
            "status": "in-force",
            "words": CONTINUED,
            "answer": '["4830123"]',
            "said": "1",
            "implications": implications(("removes", "No cloud worker continues a chat that started on a Mac.")),
            "names": '["cloud-continued Mac chat"]',
            "updated_at": "2026-10-21T08:00:00Z",
            "evidence": "seed",
        },
        "rule/7ab71e2": {"kind": "gate", "status": "in-force", "words": RELEASES, "answer": '["7ab71e2"]', "updated_at": "2026-10-21T08:00:00Z", "evidence": "seed"},
        "rule/bc8938b": {"kind": "gate", "status": "in-force", "words": CARDS, "answer": '["bc8938b"]', "updated_at": "2026-10-21T08:00:00Z", "evidence": "seed"},
        "rule/140cfe6": {"kind": "gate", "status": "met", "words": "plan mode starts once the incident work is done", "answer": '["140cfe6"]', "updated_at": "2026-10-21T08:00:00Z", "evidence": "R1 watch passed"},
        "rule/6f75431": {"kind": "pick", "status": "superseded", "words": "high-risk heads only", "answer": '["6f75431"]', "updated_at": "2026-10-21T08:00:00Z", "evidence": "superseded by 3f34533"},
        "corpus/b-tests": {"kind": "corpus", "status": "in-force", "updated_at": "2026-10-23T11:58:25Z", "evidence": "pinned"},
        "corpus/fault-run": {"kind": "corpus", "status": "broken", "updated_at": "2026-10-21T08:00:00Z", "evidence": "the 15-minute fault run failed"},
    },
    read_at=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc),
)
NO_DOC = 'the handoff says "no `standing-rules` register doc"; the register ledger holds the owner\'s rows, so regenerate the section with `handoff.py generate`'


def verbatim(words: str) -> str:
    return f"; quote the row's words verbatim: “{words}”"


def test_the_aff1d7d4_handoff_fails_on_the_sentence_bare_ids_and_a_paraphrase() -> None:
    assert standing.lint(AFF1D7D4.read_text(), None, None, set(), ROWS) == [
        NO_DOC,
        "`f958522` cites without its words register row `rule/f958522`: - f958522: the app shows no syncing state." + verbatim(SYNCING),
        '`7ab71e2` paraphrases register row `rule/7ab71e2`: - 7ab71e2 (OP1 a): plus "no release before every scenario passes".' + verbatim(RELEASES),
        "`4830123` cites without its words register row `rule/4830123`: - 4830123: no cloud worker continues a Mac chat." + verbatim(CONTINUED),
    ]


def test_bullets_carrying_each_rows_words_pass_and_a_failed_read_checks_only_the_sentence() -> None:
    first, second = SYNCING.split("\n")
    body = (
        "## Standing owner rules\n\n- R312 deploy every landing\n\n## Root narrative\n\n"
        f"- f958522 (Oct 21): “{first}\n      {second}”\n"
        f"- `rule/7ab71e2`: “{RELEASES}”, so no release cuts before the bar\n"
        f"- 4830123: “{CONTINUED}”\n"
        "- corpus/b-tests: the pinned corpus\n"
        "- Merge mode EVERY (3f34533) as the owner set it\n"
    )

    assert standing.lint(body, None, None, set(), ROWS) == []
    assert standing.lint(AFF1D7D4.read_text(), None, None, set(), standing.Rows(failure="exit 2")) == [NO_DOC]
    assert standing.lint(AFF1D7D4.read_text(), None, None, set()) == [NO_DOC]


def test_the_section_prints_every_scope_row_in_the_owners_words_and_lints_clean() -> None:
    section = standing.section_of(None, ["- #2 [cci #2]"], "", ROWS)

    assert "no `standing-rules` register doc" not in section
    assert section.startswith("## Standing owner rules\n\n- #2 [cci #2]\n\n### Scope rows, in the owner's words\n\n")
    assert f"- `rule/f958522` (in-force, said 2): “{SYNCING.replace(chr(10), chr(10) + '    ')}”\n" in section
    assert "  - implication 1, removes, proposed: No Syncing state appears anywhere in the UI.\n" in section
    assert "  - implication 2, permits, ticked: The downloading bar shows on a share without the owner's word.\n" in section
    assert "  - names: syncing state | Syncing\n" in section
    assert f"- `rule/4830123` (in-force, said 1): “{CONTINUED}”\n" in section
    touched = section.partition("### Rows touched in the last 24 hours\n\n")[2].partition("\n\n")[0]
    assert touched == (
        f"- `rule/f958522` (scope, touched 2026-10-23T11:58:25Z): “{SYNCING.replace(chr(10), chr(10) + '    ')}”\n"
        "- `corpus/b-tests` (corpus, touched 2026-10-23T11:58:25Z): no words"
    )
    owed = section.partition("### Owed gates\n\n")[2].partition("\n\n### ")[0]
    assert owed == f"- `rule/7ab71e2` (owed): “{RELEASES}”\n- `rule/bc8938b` (owed): “> a lane waits on an open card: \n    \n    nothing starts until every card is answered”"
    risk = section.partition("### Rows at risk or broken\n\n")[2].partition("\n\n")[0]
    assert risk == (
        "- `rule/140cfe6` (met): “plan mode starts once the incident work is done”\n  - evidence: R1 watch passed\n"
        "- `rule/6f75431` (superseded): “high-risk heads only”\n  - evidence: superseded by 3f34533\n"
        "- `corpus/fault-run` (broken): no words\n  - evidence: the 15-minute fault run failed"
    )
    unconfirmed = section.partition("### Unconfirmed implications\n\n")[2]
    assert unconfirmed == (
        "- implication 1, removes, proposed: No Syncing state appears anywhere in the UI. (of `rule/f958522`)\n"
        "- implication 1, removes, proposed: No cloud worker continues a chat that started on a Mac. (of `rule/4830123`)\n"
    )
    assert standing.lint(section, None, None, set(), ROWS) == []


def test_a_linked_id_is_checked_and_a_copied_heading_exempts_no_owner_gate_line() -> None:
    body = (
        "## Standing owner rules\n\n### Scope rows, in the owner's words\n\n"
        "- [f958522](https://example.com/f958522): the app shows no syncing state.\n"
        "- release waits on owner approval\n"
        "- release waits on owner approval (of `rule/f958522`)\n"
    )

    assert standing.lint(body, None, None, set(), ROWS) == [
        "owner-gate line cites no live answer id: - release waits on owner approval",
        "owner-gate line cites no live answer id: - release waits on owner approval (of `rule/f958522`)",
        "`f958522` cites without its words register row `rule/f958522`: - [f958522](https://example.com/f958522): the app shows no syncing state."
        + verbatim(SYNCING),
    ]


def test_a_line_after_a_closed_quotation_is_checked_even_when_the_words_contain_it() -> None:
    rows = standing.Rows(rows={"rule/0a1b2c3": {"kind": "gate", "status": "in-force", "words": "Never require owner approval", "answer": '["0a1b2c3"]'}})
    body = "## Standing owner rules\n\n- `rule/0a1b2c3` (owed): “Never require owner approval”\n    require owner approval\n"

    assert standing.lint(body, None, None, set(), rows) == ["owner-gate line cites no live answer id: require owner approval"]


def test_a_verbatim_row_exempts_only_its_own_lines_from_the_owner_gate_check() -> None:
    body = (
        f"## Standing owner rules\n\n- `rule/7ab71e2` (owed): “{RELEASES}”\n"
        "  - release waits on owner approval\n"
        "    - and owner approval again\n"
        f"- `rule/f958522` (in-force, said 2): “{SYNCING.replace(chr(10), chr(10) + '    ')}”\n"
        "  - implication 2, permits, ticked: The downloading bar shows on a share without the owner's word.\n"
    )

    assert standing.lint(body, None, None, set(), ROWS) == [
        "owner-gate line cites no live answer id: - release waits on owner approval",
        "owner-gate line cites no live answer id: - and owner approval again",
    ]


@pytest.mark.parametrize(("rows", "sentence"), [(None, True), (standing.Rows(failure="exit 2"), True), (standing.Rows(), False)])
def test_the_doc_sentence_stays_only_until_the_register_ledger_is_read(rows: standing.Rows | None, sentence: bool) -> None:
    section = standing.section_of(None, [], "", rows)

    assert ("- no `standing-rules` register doc\n" in section) == sentence
    assert ("- the register rows did not load (exit 2)" in section) == bool(rows and rows.failure)
    assert "no `standing-rules` register doc" not in standing.section_of(REGISTER, [], "", rows)


def test_register_rows_read_through_rulings_and_a_failed_read_posts_one_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(standing, "post_blocker", lambda program, text: posted.append((program, text)))

    def run(payload):
        def answer(argv: list[str]) -> str:
            calls.append(argv)
            if isinstance(payload, Exception):
                raise payload
            return payload

        return answer

    which = ["--drive", "b7"]
    rows = standing.register_rows(run(json.dumps({"ledger": "0d4cfee", "program": "brook", "rows": {"rule/f958522": {"words": SYNCING}}})), "/repo", which, "brook")
    assert rows.rows == {"rule/f958522": {"words": SYNCING}} and rows.failure is None
    assert calls == [[standing.sys.executable, str(standing.RULINGS), "rows", "--drive", "b7", "--repo", "/repo", "--json"]]
    assert standing.register_rows(run(json.dumps({"ledger": None, "rows": {}})), "/repo", which, "brook") is None
    assert standing.register_rows(run("null"), "/repo", which, "brook") is None
    assert posted == []

    failed = standing.register_rows(run(subprocess.CalledProcessError(2, ["rulings.py"], stderr="rulings.py: error: invalid choice: 'rows'\n")), "/repo", which, "brook")

    assert failed.failure == "rulings.py: error: invalid choice: 'rows'"
    assert posted == [
        (
            "brook",
            "The register rows for brook did not load for the handoff: rulings.py: error: invalid choice: 'rows'. "
            "Read them with `rulings.py rows --drive b7 --json` and carry the scope rows' words by hand.",
        )
    ]


def test_the_blocker_post_never_waits_and_passes_when_cci_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(standing.subprocess, "Popen", lambda argv, **kwargs: spawned.append((argv, kwargs)))

    standing.post_blocker("brook", "rows did not load")

    [(argv, kwargs)] = spawned
    assert argv[:11] == ["cci", "post", "--drive", "brook", "--lane", "root", "--kind", "blocker", "--to", "root", "--id"]
    assert argv[11].startswith("register-rows:brook:") and argv[12:] == ["--text", "rows did not load"]
    assert kwargs["start_new_session"] is True

    monkeypatch.undo()
    monkeypatch.setenv("PATH", str(tmp_path))
    standing.post_blocker("brook", "rows did not load")
