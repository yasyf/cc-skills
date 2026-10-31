import hashlib
import io
import json
from pathlib import Path

import grades
import pytest
from test_desk_runner import config, lone_green_pr, mq, runner_module, shell


@pytest.mark.parametrize("unrelated", [32, 10000])
def test_mq_why_incident_body_scope_baseline(shell, config, tmp_path, capsys, monkeypatch, unrelated):
    other, selected = tmp_path / "other.txt", tmp_path / "selected.txt"
    other.write_text("An incident body names #900000000.\n")
    selected.write_text("Only this body names #29020.\n")
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = ["hold"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)

    def record(seq, path):
        return {"seq": seq, "drive": "d1", "lane": "incident-api-1", "kind": "incident", "status": "open", "to": ["root"], "refs": {"path": str(path), "prs": [900000001]}, "text": "The summary names no selected PR", "source": "post", "at": "2026-10-01T17:59:00Z"}

    shell.records.extend([*(record(seq, other) for seq in range(1, unrelated + 1)), record(unrelated + 1, selected)])
    assert len(shell.records) == unrelated + 1
    tallies = {path: {"opens": 0, "bytes": 0} for path in (other, selected)}
    original_open = Path.open
    original_names_pr = runner_module.names_pr
    predicates = 0

    class Counted(io.FileIO):
        def __init__(self, path):
            super().__init__(path, "r")
            self.tally = tallies[path]
            self.tally["opens"] += 1

        def read(self, size=-1):
            result = super().read(size)
            self.tally["bytes"] += len(result)
            return result

        def readall(self):
            result = super().readall()
            self.tally["bytes"] += len(result)
            return result

        def readinto(self, buffer):
            result = super().readinto(buffer)
            self.tally["bytes"] += result
            return result

    def opened(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if path in tallies:
            assert mode == "r" and buffering == -1
            return io.TextIOWrapper(io.BufferedReader(Counted(path)), encoding=encoding, errors=errors, newline=newline)
        return original_open(path, mode, buffering, encoding, errors, newline)

    def names_pr(record, pr):
        nonlocal predicates
        if record.get("kind") == "incident":
            predicates += 1
        return original_names_pr(record, pr)

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(runner_module, "names_pr", names_pr)
    posts = len(shell.posts)
    assert mq(shell, config, "why", "29020") == 0
    output = capsys.readouterr().out
    incident_calls = [argv for argv in shell.calls if argv[:2] == ["cci", "tail"] and "--kind=incident" in argv]
    metrics = {path.name: value for path, value in tallies.items()}
    print(json.dumps({"caller": "actual mq.main -> Landing.why -> holding", "source": hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest(), "grades_source": hashlib.sha256(Path(grades.__file__).read_bytes()).hexdigest(), "records": unrelated + 1, "unrelated": unrelated, "distinct_body_files": 2, "incident_tail_calls": len(incident_calls), "incident_predicates": predicates, "body_io": metrics, "posts": len(shell.posts) - posts, "transport_limit": "Existing supplied CCI fixture; real caller and real body FileIO, not native SQL/index proof"}))
    assert f"hold fail open INCIDENT #{unrelated + 1}, whose lane squash-merges it directly" in output
    assert predicates == unrelated + 1
    assert tallies[other] == {"opens": unrelated, "bytes": unrelated * len("An incident body names #900000000.\n".encode())}
    assert tallies[selected] == {"opens": 1, "bytes": len("Only this body names #29020.\n".encode())}
    assert len(shell.posts) == posts
