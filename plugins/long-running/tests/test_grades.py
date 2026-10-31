from __future__ import annotations

from long_running import grades
import pytest


def test_a_head_record_naming_no_earlier_head_states_no_move():
    record = {"seq": 1, "drive": "d1", "lane": "p8", "kind": "head", "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "HEAD #29020 cccc333333: opened"}
    assert grades.head_moves([record], "d1", [], "Forge-AI/monorepo", "29020") == []


def test_a_head_move_counts_as_patch_identical_once_a_fidelity_grade_at_its_new_head_says_so():
    found = [
        {"seq": 1, "drive": "d1", "lane": "p8", "kind": "head", "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "HEAD #29020 cccc333333 (was bbbb222222): replay, range-diff 13 identical, 1 changed"},
        {"seq": 2, "drive": "d1", "lane": "fidelity-desk-3", "kind": "review", "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": 'fidelity: ok cccc333333 a patch-identical replay onto #42745 7adb738: 13 commits "=", the first differs only in context lines.'},
        {"seq": 3, "drive": "d1", "lane": "fidelity-desk-3", "kind": "review", "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "fidelity: ok dddd444444 patch-identical in substance to cccc333: 21 commits \"=\"; no added or removed line changes."},
    ]
    moves = grades.head_moves(found, "d1", ["fidelity-desk-3"], "Forge-AI/monorepo", "29020")
    assert grades.patch_chain(moves, "dddd444444", "bbbb222222") == [3, 1]


def test_a_go_that_opens_with_its_clock_time_names_its_heads():
    assert grades.go_heads({"text": "1:19 AM go #43421 at e63e524f1a6981c77ff969c578e1a208a44d1dba (restack onto dev; his tap kept per the re-tap rule)."}) == {"43421": "e63e524f1a6981c77ff969c578e1a208a44d1dba"}
    assert grades.go_heads({"text": "2:02 AM go #43911 at 52a51b6 (his tap on the board at 2:02 AM). Enqueue."}) == {"43911": "52a51b6"}
    assert grades.go_heads({"text": "12:40 PM merge #43735 at 2d12b6f and #43421 at e63e524"}) == {"43735": "2d12b6f", "43421": "e63e524"}
    assert grades.go_heads({"text": "1:19 AM no go #43421 at e63e524 until the finder reads it"}) == {}


def test_a_carried_go_keeps_the_port_or_test_only_claim_of_the_go_it_carries():
    def go(text: str) -> dict:
        return {"seq": 5, "at": "2026-10-29T08:00:00Z", "drive": "d1", "lane": "root", "kind": "go", "text": text}

    grade = {"seq": 9, "at": "2026-10-29T08:05:00Z", "refs": {"prs": ["Forge-AI/monorepo#44672"]}, "text": "fidelity: ok 7161713 pure port, no interaction: carried from 1ec2d0a (cci #5), patch-identical by git patch-id over its own commits."}
    for source, carried in [("go port #44672 at 1ec2d0a", "go port #44672 at 7161713"), ("go #44672 at 1ec2d0a, a Test-only change", "go test-only #44672 at 7161713"), ("go #44672 at 1ec2d0a", "go #44672 at 7161713")]:
        [moved] = grades.carried_gos([go(source)], [grade], "Forge-AI/monorepo", lambda pr, head: True)
        assert moved["text"] == carried and grades.go_heads(moved) == {"44672": "7161713"}


@pytest.mark.parametrize(
    ("record", "seqs"),
    [
        ({"text": "go #12 and go #13 are withdrawn: the grades they cited moved"}, {12, 13}),
        ({"re": 12, "text": "Withdrawn: the finder found a blocker"}, {12}),
        ({"re": 12, "text": "go #12 is not withdrawn; the grade stands"}, set()),
        ({"text": "Answering go #12: the fidelity grade #140953 it cited was withdrawn. The go stands."}, {140953}),
        ({"text": "go #123 is withdrawn"}, {123}),
    ],
)
def test_withdrawn_names_every_record_a_record_withdraws(record, seqs):
    assert grades.withdrawn(record) == seqs


def carry_source(pr, head, seq=5, claim=""):
    return {"seq": seq, "at": "2026-10-29T08:00:00Z", "drive": "d1", "lane": "root", "kind": "go", "text": f"go {claim}#{pr} at {head}"}


def carry_grade(pr, head, earlier, minute=1):
    return {"seq": 10 + minute, "at": f"2026-10-29T08:{minute:02d}:00Z", "refs": {"prs": [f"Forge-AI/monorepo#{pr}"]}, "text": f"fidelity: ok {head} patch-identical carried from {earlier}"}


@pytest.mark.parametrize("metric", ["carries", "carry_of", "visited"])
def test_carry_chain_parses_each_grade_once_and_visits_only_its_pr(monkeypatch, metric):
    counts = {"carries": 0, "carry_of": 0, "visited": 0}
    for name in ("carries", "carry_of"):
        original = getattr(grades, name)

        def counted(text, name=name, original=original):
            counts[name] += 1
            return original(text)

        monkeypatch.setattr(grades, name, counted)
    reached = grades.reached

    def measured(gone, pr, sha):
        class Entries:
            def items(self):
                for entry in gone.items():
                    counts["visited"] += 1
                    yield entry

        return reached(Entries(), pr, sha)

    monkeypatch.setattr(grades, "reached", measured)
    sources = [carry_source(pr, "ddddddd") for pr in range(100, 600)] + [carry_source(12, "aaaaaaa")]
    records = [carry_grade(12, "ccccccc", "bbbbbbb", 2), carry_grade(12, "bbbbbbb", "aaaaaaa")]
    moved = grades.carried_gos(sources, records, "Forge-AI/monorepo", lambda pr, head: True)
    assert [record["text"] for record in moved] == ["go #12 at bbbbbbb", "go #12 at ccccccc"]
    assert counts[metric] == {"carries": 2, "carry_of": 2, "visited": 18}[metric]


def test_carry_chain_keeps_prefix_insertion_order_duplicate_waves_and_callback_order():
    sources = [carry_source(12, "abcdef1", 5), carry_source(12, "abcdef1234", 6, "test-only "), carry_source(12, "abcdef1", 7, "port ")]
    records = [carry_grade(12, "ccccccc", "bbbbbbb", 3), carry_grade(12, "bbbbbbb", "abcdef1234"), carry_grade(12, "bbbbbbb", "abcdef1234", 2)]
    calls = []
    moved = grades.carried_gos(sources, records, "Forge-AI/monorepo", lambda pr, head: calls.append((pr, head)) or True)
    assert calls == [("12", "bbbbbbb"), ("12", "bbbbbbb"), ("12", "ccccccc")]
    assert [(record["seq"], record["text"], record["graded_at"]) for record in moved] == [
        (7, "go port #12 at bbbbbbb", records[1]["at"]),
        (7, "go port #12 at bbbbbbb", records[2]["at"]),
        (7, "go port #12 at ccccccc", records[0]["at"]),
    ]


def test_carry_parsing_keeps_first_wave_errors_after_prior_lands_calls():
    calls = []
    with pytest.raises(TypeError):
        grades.carried_gos([carry_source(12, "aaaaaaa")], [carry_grade(12, "bbbbbbb", "aaaaaaa"), {"text": None}], "Forge-AI/monorepo", lambda pr, head: calls.append(head) or True)
    assert calls == ["bbbbbbb"]


def test_carry_rechecks_rejected_lands_on_the_next_wave_and_stops_at_cycles():
    calls = []

    def lands(pr, head):
        calls.append((pr, head))
        return pr != "12" or calls.count((pr, head)) > 1

    sources = [carry_source(12, "aaaaaaa"), carry_source(13, "ccccccc")]
    records = [carry_grade(12, "bbbbbbb", "aaaaaaa"), carry_grade(13, "ddddddd", "ccccccc"), carry_grade(12, "aaaaaaa", "bbbbbbb")]
    moved = grades.carried_gos(sources, records, "Forge-AI/monorepo", lands)
    assert calls == [("12", "bbbbbbb"), ("13", "ddddddd"), ("12", "bbbbbbb")]
    assert [record["text"] for record in moved] == ["go #13 at ddddddd", "go #12 at bbbbbbb"]


@pytest.mark.parametrize(
    ("grade", "kept"),
    [
        ("ok cccc333333 a review fix: no new behavior since the tap at aaaa111111.", True),
        ("ok cccc333333 No new behavior since the owner's tap at aaaa111.", True),
        ("ok cccc333333 a nil check; no new behaviour since his tap at aaaa111111.", True),
        ("ok cccc333333 No new behavior since the owner’s tap at aaaa111.", True),
        ("ok cccc333333 no new behavior since the owner's tap at bbbb222.", False),
        ("ok cccc333333 carried from the owner's tap at aaaa111; stays a tap row.", False),
    ],
    ids=["the-tap", "the-owners-tap-204887", "his-tap", "curly-apostrophe", "another-tap", "carried-keeps-none"],
)
def test_a_grade_keeps_the_tap_however_it_names_whose_tap_it_was(grade, kept):
    """Root #204975: grade #204887 'No new behavior since the owner's tap at 1f93c6d.' was read as no carry because it said the owner's tap."""
    assert grades.keeps_tap(grade, "aaaa111111") is kept


def test_the_go_state_names_the_newest_go_at_the_head_the_carry_that_reached_it_or_the_newest_go_at_an_older_head():
    """10:47 PM Oct 29 the watchdog told the merger a go was withdrawn seven minutes after root re-granted it at the new head (retro step 447)."""

    def go(seq: int, at: str, text: str) -> dict:
        return {"seq": seq, "at": at, "drive": "d1", "lane": "root", "kind": "go", "text": text, "voided": set()}

    first, again = go(5, "2026-10-30T05:40:00Z", "go #45001 at aaaa111"), go(9, "2026-10-30T05:47:00Z", "go #45001 at bbbb222")
    assert grades.go_state([first, again], "45001", "bbbb222222").said() == "go #9 by root at 10:47 PM"
    assert grades.go_state([first, again], "45001", "cccc333333").said() == "no go names this head; the newest, go #9 by root at 10:47 PM, names bbbb222"

    grade = {"seq": 12, "at": "2026-10-30T05:55:00Z", "refs": {"prs": ["Forge-AI/monorepo#45001"]}, "text": "fidelity: ok cccc333 carried from bbbb222, patch-identical by git patch-id over its own commits."}
    gos = [first, again, *grades.carried_gos([first, again], [grade], "Forge-AI/monorepo", lambda pr, head: True)]
    assert grades.go_state(gos, "45001", "cccc333333").said() == "go #9 by root at 10:47 PM, carried to this head"

    withdrawn = again | {"voided": {"45001"}}
    assert grades.go_state([first, withdrawn], "45001", "bbbb222222").said() == "no go names this head; the newest, go #5 by root at 10:40 PM, names aaaa111"
    assert grades.go_state([], "45001", "bbbb222222").said() == "no go names this head"


def test_full_text_reads_a_spilled_body_and_keeps_the_record_text_when_the_path_is_a_binary_attachment(tmp_path):
    body = tmp_path / "body.txt"
    body.write_text("the whole body")
    tarball = tmp_path / "logs.tgz"
    tarball.write_bytes(b"\x1f\x8b\x08\x00binary")
    assert grades.full_text({"text": "short", "refs": {"path": str(body)}}) == "the whole body"
    assert grades.full_text({"text": "short", "refs": {"path": str(tarball)}}) == "short"
