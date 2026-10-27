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
