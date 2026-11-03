from __future__ import annotations

from pathlib import Path

import pytest

from hooks import owner_returns


def test_hosted_titles_read_the_explainers_checkout_under_the_configured_worktree_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = tmp_path / "design-docs" / "explainers" / "one-pagers" / "relay" / "index.html"
    page.parent.mkdir(parents=True)
    page.write_text("<title>How the relay works</title>")
    monkeypatch.setattr(owner_returns, "read", lambda argv: f"{tmp_path}\n" if argv == ["git", "config", "ccx.worktreeRoot"] else None)

    assert owner_returns.hosted_titles() == ["How the relay works"]
