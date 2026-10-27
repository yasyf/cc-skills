from __future__ import annotations

import grades


def test_a_head_record_naming_no_earlier_head_states_no_move():
    record = {"seq": 1, "drive": "d1", "lane": "p8", "kind": "head", "refs": {"prs": ["Forge-AI/monorepo#29020"]}, "text": "HEAD #29020 cccc333333: opened"}
    assert grades.head_moves([record], "d1", [], "Forge-AI/monorepo", "29020") == []
