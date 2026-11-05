from __future__ import annotations

from types import SimpleNamespace

from hooks.owner_cards_name_their_subject import post_text, whole_post
from hooks.owner_returns import REPLY

PR = "https://github.com/Forge-AI/monorepo/pull/49499"
LINK = {"label": "The one-pager", "url": "https://docs.poetic.design/one-pager"}


def test_the_judge_reads_urls_links_prs_and_picks_but_no_hidden_cite():
    evt = SimpleNamespace(tool_name=REPLY, _tool_input={"title": "Which PR", "md": f"The fix is {PR}. <!-- cci #384714 -->", "picks": ["Keep it :: It stays."], "links": [LINK], "prs": [PR]})
    read = whole_post(evt, post_text(evt, None))
    assert f"The fix is {PR}." in read
    assert "Keep it :: It stays." in read
    assert LINK["url"] in read
    assert read.count(PR) == 2
    assert "384714" not in read
