from pathlib import Path

import pytest
from conftest import RESTACK, commit_files, git_in
from drive import land, restack, state

MERGED, LOWER, UPPER = "yasyf/stacked#1", "yasyf/stacked#2", "yasyf/stacked#3"


def squash_merged_stack(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, upper: dict[str, str]) -> tuple[Path, str]:
    """yasyf/stacked on an origin whose main squash-merged the PR under a two-PR stack after main moved on, the lower PR retargeted onto main still carrying that PR's commit and the upper one on the lower's branch; the origin and main's head."""
    work, origin = tmp_path / "work", tmp_path / "yasyf" / "stacked.git"
    git_in(tmp_path, "init", "-q", "-b", "main", str(work))
    commit_files(work, "base", {"one.py": "a = 1\n", "two.py": "b = 1\n", "three.py": "c = 1\n"})
    git_in(work, "switch", "-q", "-c", "merged")
    merged = commit_files(work, "the merged change", {"one.py": "a = 2\n"})
    git_in(work, "switch", "-q", "-c", "lower")
    lower = commit_files(work, "the lower change", {"two.py": "b = 2\n"})
    git_in(work, "switch", "-q", "-c", "upper")
    top = commit_files(work, "the upper change", upper)
    git_in(work, "switch", "-q", "main")
    commit_files(work, "another change", {"three.py": "c = 0\n"})
    git_in(work, "merge", "-q", "--squash", "merged")
    git_in(work, "commit", "-q", "-m", "the merged change (#1)")
    main = git_in(work, "rev-parse", "HEAD")
    git_in(tmp_path, "clone", "-q", "--bare", str(work), str(origin))
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.com")
    monkeypatch.setattr(land, "CLONE_URL", f"file://{tmp_path}/{{}}.git")
    with state.edit(demo) as data:
        for key, branch, base, head, now in ((MERGED, "merged", "main", merged, "merged"), (LOWER, "lower", "main", lower, "open"), (UPPER, "upper", "lower", top, "open")):
            data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": head, "branch": branch, "base": base, "default": "main", "state": now, "conflict": key == LOWER}
    return origin, main


def test_a_squash_merge_under_a_stack_restacks_each_branch_above_onto_main_with_only_its_own_commits_and_pushes_them(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    origin, main = squash_merged_stack(demo, monkeypatch, tmp_path, {"four.py": "d = 1\n"})
    seen: dict[str, tuple] = {}
    [found] = RESTACK(demo, [LOWER, UPPER], seen)
    assert (len(found["moved"]), found["kept"]) == (2, [])
    assert git_in(origin, "rev-parse", "lower^") == main
    assert git_in(origin, "log", "--format=%s", "main..upper").splitlines() == ["the upper change", "the lower change"]
    assert git_in(origin, "show", "upper:two.py") == "b = 2"
    assert RESTACK(demo, [LOWER, UPPER], seen) == []
    assert (state.drive_dir(demo) / restack.LOG).read_text().count('"kind": "restack"') == 1


def test_a_branch_whose_replay_conflicts_stays_on_its_head_for_its_builder_while_the_branch_below_it_moves(demo: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    origin, main = squash_merged_stack(demo, monkeypatch, tmp_path, {"three.py": "c = 5\n"})
    upper = git_in(origin, "rev-parse", "upper")
    [found] = RESTACK(demo, [LOWER, UPPER], {})
    assert len(found["moved"]) == 1
    assert found["kept"] == [f"{UPPER}: its 1 commit conflict on {git_in(origin, 'rev-parse', 'lower')[:12]}"]
    assert git_in(origin, "rev-parse", "lower^") == main
    assert git_in(origin, "rev-parse", "upper") == upper
