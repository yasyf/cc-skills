from __future__ import annotations

import grades


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
