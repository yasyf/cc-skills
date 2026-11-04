from __future__ import annotations

from pathlib import Path

import pytest

from hooks import owner_replies_stay_short, owner_returns


def test_hosted_titles_read_the_explainers_checkout_under_the_configured_worktree_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = tmp_path / "design-docs" / "explainers" / "one-pagers" / "relay" / "index.html"
    page.parent.mkdir(parents=True)
    page.write_text("<title>How the relay works</title>")
    monkeypatch.setattr(owner_returns, "read", lambda argv: f"{tmp_path}\n" if argv == ["git", "config", "ccx.worktreeRoot"] else None)

    assert owner_returns.hosted_titles() == ["How the relay works"]


def test_an_edit_counts_as_a_pick_card_only_when_the_stored_reply_has_picks(tmp_path: Path) -> None:
    card, status = tmp_path / "r1.json", tmp_path / "r2.json"
    card.write_text('{"id": "r1", "picks": ["Build it", "Hold it"]}')
    status.write_text('{"id": "r2", "md": "Its checks passed."}')

    assert owner_replies_stay_short.has_picks(card)
    assert not owner_replies_stay_short.has_picks(status)
    assert not owner_replies_stay_short.has_picks(tmp_path / "gone.json")
