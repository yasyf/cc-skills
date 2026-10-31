"""The typed bus refuses at post what tonight's runner read wrong or not at all."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from lr import bus
from lr.bus import Bus, Record, Refused

HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
GRADE = dict(pr=45821, head=HEAD, grader="finder", verdict="clear")


class FakeCci:
    def __init__(self, rows=()):
        self.calls: list[list[str]] = []
        self.rows = list(rows)

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[1] == "post":
            body = json.loads(open(argv[argv.index("--body-file") + 1]).read())
            return json.dumps({"seq": 7, "at": "2026-10-31T06:00:00Z", "kind": argv[argv.index("--kind") + 1], "lane": argv[argv.index("--lane") + 1], "to": [argv[i + 1] for i, flag in enumerate(argv) if flag == "--to"], "body": body})
        return "\n".join(json.dumps(row) for row in self.rows)


def row(seq: int, kind: str, body: dict, to=("supervisor",), lane="lane-7") -> dict:
    return {"seq": seq, "at": "2026-10-31T06:00:00Z", "kind": f"lr.{kind}", "lane": lane, "to": list(to), "body": body}


def test_a_clear_posted_as_the_wrong_kind_is_refused_with_the_right_command():
    with pytest.raises(Refused, match=r"these fields are a grade record, not note; run: lr post grade --pr 45821 --head e718a7434fa01ee9835f992a9a647a825350732c --grader finder --verdict clear --to supervisor"):
        Bus("d", "finder-desk", FakeCci()).post("note", to=["supervisor"], **GRADE)


def test_a_grade_naming_five_prs_is_refused_with_one_command_per_pr():
    with pytest.raises(Refused, match=r"a grade names one PR; post one record per PR: lr post grade --pr 1 .* && lr post grade --pr 2 .* && lr post grade --pr 3 "):
        Bus("d", "finder-desk", FakeCci()).post("grade", **{**GRADE, "pr": [1, 2, 3]})


@pytest.mark.parametrize("kind, body", [("state", dict(lane="lane-7")), ("done", dict(item=3)), ("grade", GRADE), ("item", dict(item=3, action="restack", pr=1, head=HEAD, mover="lane", deadline="2026-10-31T06:20:00Z")), ("note", dict(text="landing is slow"))])
def test_root_hears_only_escalations_asks_and_decisions(kind, body):
    with pytest.raises(Refused, match=rf"a {kind} record never reaches root; .*post an escalation, an ask or a decision"):
        Bus("d", "lane-7", FakeCci()).post(kind, to=["root"], **body)


@pytest.mark.parametrize("kind, body", [("escalation", dict(subject="org/repo#45470", cause="verdict overdue")), ("ask", dict(text="which base?")), ("decision", dict(text="land the bottom"))])
def test_the_three_kinds_that_reach_root(kind, body):
    record = Bus("d", "supervisor", FakeCci()).post(kind, to=["root"], **body)

    assert (record.kind, record.to) == (kind, ("root",))


def test_a_go_by_root_is_not_a_tap():
    with pytest.raises(Refused, match="tap.by must be one of owner, rule, got 'root'"):
        Bus("d", "root", FakeCci()).post("tap", to=["supervisor"], pr=45911, head=HEAD, by="root", value="tapped")


def test_an_exemption_tap_names_its_rule():
    record = Bus("d", "supervisor", FakeCci()).post("tap", to=["supervisor"], pr=44989, head=HEAD, by="rule", value="tapped", rule="test-only")

    assert record.rule == "test-only"


@pytest.mark.parametrize("field, value, message", [("head", "e718a74", "a full 40-character sha"), ("pr", "45821", "an integer"), ("verdict", "lgtm", "one of clear, ask, pass, fail"), ("grader", "codex", "one of finder, fidelity")])
def test_a_field_of_the_wrong_shape_is_refused(field, value, message):
    with pytest.raises(Refused, match=f"grade.{field} must be {message}"):
        Bus("d", "finder-desk", FakeCci()).post("grade", **{**GRADE, field: value})


def test_a_missing_field_is_named():
    with pytest.raises(Refused, match="grade takes pr, head, grader, verdict; missing verdict"):
        Bus("d", "finder-desk", FakeCci()).post("grade", pr=1, head=HEAD, grader="finder")


def test_an_unknown_kind_lists_the_twelve():
    with pytest.raises(Refused, match="unknown kind 'evidence'; the kinds are head, grade, tap, verdict, item, done, escalation, decision, ask, answer, note, state"):
        Bus("d", "lane-7", FakeCci()).post("evidence", text="x")


def test_a_post_is_one_cci_call_with_a_json_body_and_an_identity_key():
    cci = FakeCci()
    record = Bus("drive", "finder-desk", cci).post("grade", to=["supervisor"], **GRADE)

    [argv] = cci.calls
    assert argv[:8] == ["cci", "post", "--drive", "drive", "--lane", "finder-desk", "--kind", "lr.grade"]
    assert argv[argv.index("--id") + 1] == f"lr.grade/45821/{HEAD}/finder"
    assert argv[argv.index("--pr") + 1] == "45821"
    assert argv[argv.index("--text") + 1] == "grade #45821 e718a7434fa0 finder clear"
    assert record == Record(7, datetime(2026, 10, 31, 6, tzinfo=UTC), "grade", "finder-desk", ("supervisor",), GRADE)
    assert record.grader == "finder"


def test_a_tap_and_its_withdrawal_are_two_records_but_a_repeated_tap_is_one():
    assert bus.identity("tap", dict(pr=1, head=HEAD, by="owner", value="tapped")) == bus.identity("tap", dict(pr=1, head=HEAD, by="owner", value="tapped"))
    assert bus.identity("tap", dict(pr=1, head=HEAD, by="owner", value="withdrawn")) != bus.identity("tap", dict(pr=1, head=HEAD, by="owner", value="tapped"))


def test_a_verdict_needs_no_resolves_chain_to_apply_to_its_head():
    rows = [row(1, "verdict", dict(pr=46228, head=HEAD, verdict="accept"), lane="root")]
    records, rejected = Bus("d", "supervisor", FakeCci(rows)).read(since=0)

    assert rejected == []
    assert [(record.pr, record.head, record.verdict) for record in records] == [(46228, HEAD, "accept")]


def test_read_is_one_tail_from_the_cursor_filtered_to_the_twelve_kinds():
    cci = FakeCci([row(1, "head", dict(pr=1, head=HEAD, lane="lane-7"))])
    Bus("drive", "supervisor", cci).read(since=239378)

    [argv] = cci.calls
    assert argv[:7] == ["cci", "tail", "--drive", "drive", "--since", "239378", "--json"]
    assert [flag for flag in argv if flag.startswith("--kind=")] == [f"--kind=lr.{kind}" for kind in bus.KINDS]


def test_read_sets_aside_a_body_the_bus_would_have_refused():
    rows = [row(1, "grade", GRADE), row(2, "grade", dict(pr=[1, 2], head=HEAD, grader="finder", verdict="clear")), row(3, "state", dict(lane="lane-7"), to=("root",))]
    records, rejected = Bus("d", "supervisor", FakeCci(rows)).read(since=0)

    assert [record.seq for record in records] == [1]
    assert [raw["seq"] for raw in rejected] == [2, 3]


def test_every_kind_in_the_table_has_a_whole_spec():
    for kind, spec in bus.KINDS.items():
        assert set(spec) <= {"fields", "optional", "identity", "audience"}, kind
        assert set(spec["identity"]) <= set(spec["fields"]) | set(spec.get("optional", {})), kind
        assert spec["audience"] == "any" or set(spec["audience"]) <= {"supervisor", "lane", "pool", "owner", "root"}, kind
