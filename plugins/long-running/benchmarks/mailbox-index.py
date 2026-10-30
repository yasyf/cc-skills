from __future__ import annotations

import json
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from long_running.rosters import RosterIndex, UNSAFE_NAME


def reference(root: Path, lane: str):
    joined = [(member["joinedAt"], config.parent) for config in (root / "teams").glob("*/config.json") for member in json.loads(config.read_text())["members"] if member["name"] == lane]
    return max(joined)[1] / "inboxes" / f"{UNSAFE_NAME.sub('-', lane)}.json" if joined else None


def measure(count: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for number in range(count):
            team = root / "teams" / str(number)
            team.mkdir(parents=True)
            (team / "config.json").write_text(json.dumps({"members": [{"name": "desk", "joinedAt": number, "prompt": "private" * 4096}]}))
        before = {"config_reads": 0, "config_bytes": 0, "json_decodes": 0}
        read, loads = Path.read_text, json.loads

        def read_text(path, *args, **kwargs):
            value = read(path, *args, **kwargs)
            before["config_reads"] += 1
            before["config_bytes"] += len(value.encode())
            return value

        def decode(value, *args, **kwargs):
            before["json_decodes"] += 1
            return loads(value, *args, **kwargs)

        started = perf_counter()
        with patch.object(Path, "read_text", read_text), patch.object(json, "loads", decode):
            expected = reference(root, "desk")
        old_seconds = perf_counter() - started
        started = perf_counter()
        with RosterIndex(root) as index:
            assert index.lookup("desk") == expected
            cold_seconds = perf_counter() - started
            cold = dict(index.work)
            started = perf_counter()
            for _ in range(100):
                assert index.lookup("desk") == expected
                assert index.lookup("absent") is None
            warm_seconds = perf_counter() - started
            warm = {key: index.work[key] - value for key, value in cold.items()}
            config = root / "teams" / "0" / "config.json"
            config.write_text(json.dumps({"members": [{"name": "desk", "joinedAt": count + 1}]}))
            assert index.lookup("desk") == reference(root, "desk")
            changed = {key: index.work[key] - value for key, value in cold.items()}
            assert warm["config_reads"] == warm["config_bytes"] == warm["json_decodes"] == warm["root_enumerations"] == 0
            assert changed["config_reads"] == changed["json_decodes"] == 1
            return {"teams": count, "old_one_query": before, "new_cold": cold, "new_warm_200_queries": warm, "new_one_changed_config": changed, "seconds": {"old_one_query": old_seconds, "new_cold": cold_seconds, "new_warm_200_queries": warm_seconds}}


if __name__ == "__main__":
    print(json.dumps({"metrics": "Logical config reads, JSON decodes and bytes; timings are CI wall time, not physical disk I/O", "local_filesystem_contract": "Native write/rename operations on local APFS or Linux filesystems; mmap-only and remote filesystem changes are outside the watcher guarantee", "measurements": [measure(count) for count in (32, 256)]}, indent=2))
