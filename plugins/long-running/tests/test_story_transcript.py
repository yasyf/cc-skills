import os
from pathlib import Path

from story import Stories


def write(folder: Path, name: str, mtime: int) -> Path:
    path = folder / name
    path.write_text("{}\n")
    os.utime(path, (mtime, mtime))
    return path


def test_a_story_follows_its_first_worker_even_when_a_helper_wrote_later(tmp_path: Path) -> None:
    owner = write(tmp_path, "agent-ap8a-step7-2-7ce4.jsonl", 100)
    write(tmp_path, "agent-atest-fold-1-e15f.jsonl", 200)
    assert Stories({"transcripts": str(tmp_path)}, None, None).transcript(["p8a-step7", "test-fold-1"]) == owner


def test_a_worker_name_never_matches_a_longer_name_it_prefixes(tmp_path: Path) -> None:
    write(tmp_path, "agent-ap8a-step7x-1aa.jsonl", 100)
    assert Stories({"transcripts": str(tmp_path)}, None, None).transcript(["p8a-step7"]) is None
