from __future__ import annotations

from contextlib import contextmanager
import json

import pytest

from long_running import register_reads


@pytest.mark.parametrize("kept_any", [False, True])
@pytest.mark.parametrize("cached_repo", ["/repo", "/other"])
def test_kept_any_reuses_only_the_matching_repository_snapshot(tmp_path, kept_any, cached_repo):
    cached_rows = {"old": {"words": "old snapshot"}}
    kept = tmp_path / "register-brook.json"
    kept.write_text(json.dumps({"repo": cached_repo, "tips": "old tips", "ledger": "old-ledger", "rows": cached_rows}))
    calls = []
    locks = []
    held_calls = []

    def run(argv):
        calls.append(argv)
        if argv[0] == "git":
            return "new tips"
        if argv[3:5] == ["ledger", "list"]:
            return json.dumps([{"id": "new-ledger", "updated_at": "2"}])
        assert argv[3:5] == ["ledger", "show"]
        return json.dumps({"id": argv[5], "status": "active", "rows": [{"key": "new", "fields": {"words": "new snapshot"}, "updated_at": "2"}]})

    @contextmanager
    def lock(path):
        locks.append(path)
        yield

    def held(path):
        held_calls.append(path)
        return False

    identity, rows = register_reads.load(run, lock, held, "/repo", "brook", tmp_path, kept_any=kept_any)

    assert held_calls == []
    assert calls[0] == ["git", "-C", "/repo", "for-each-ref", "--format=%(refname) %(objectname)", register_reads.LEDGER_REFS]
    if kept_any and cached_repo == "/repo":
        assert (identity, rows) == ("old-ledger", cached_rows)
        assert len(calls) == 1
        assert locks == []
    else:
        assert identity == ("old-ledger" if cached_repo == "/repo" else "new-ledger")
        assert rows == {"new": {"words": "new snapshot", "updated_at": "2"}}
        assert locks == [kept.with_suffix(".lock")]
        assert all(argv[0] == "ccn" for argv in calls[1:])
        assert json.loads(kept.read_text())["tips"] == "new tips"
