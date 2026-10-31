import ast
import hashlib
import io
import json
import os
from pathlib import Path

import grades
import pytest
from test_desk_runner import config, lone_green_pr, mq, runner_module, shell


@pytest.mark.parametrize(("size", "invalid_at"), [(32 * 1024, 0), (8 * 1024 * 1024, 0), (8 * 1024 * 1024, 128 * 1024)])
def test_mq_why_reads_only_to_the_binary_attachment_decode_error(shell, config, tmp_path, capsys, monkeypatch, size, invalid_at):
    historical_path = Path(os.environ["LR_BINARY_BODY_LEGACY_SOURCE"])
    historical = ast.parse(historical_path.read_bytes())
    definition = next(node for node in historical.body if isinstance(node, ast.FunctionDef) and node.name == "full_text")
    namespace = vars(grades).copy()
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(historical_path), "exec"), namespace)
    legacy = namespace["full_text"]
    current = grades.full_text
    attachment = tmp_path / "incident-attachment.bin"
    attachment.write_bytes(b"a" * invalid_at + b"\xff" + b"b" * (size - invalid_at - 1))
    raw = json.loads(config.read_text())
    raw["landing"]["checks"] = ["hold"]
    config.write_text(json.dumps(raw))
    lone_green_pr(shell)
    shell.records.append({"seq": 42, "drive": "d1", "lane": "incident-api-1", "kind": "incident", "status": "open", "to": ["root"], "refs": {"path": str(attachment)}, "text": "INCIDENT api 5xx; the fix is #29020", "source": "post", "at": "2026-10-01T17:59:00Z"})
    original_open = Path.open
    consumed = 0

    class Counted(io.FileIO):
        def read(self, size=-1):
            nonlocal consumed
            result = super().read(size)
            consumed += len(result)
            return result

        def readall(self):
            nonlocal consumed
            result = super().readall()
            consumed += len(result)
            return result

        def readinto(self, buffer):
            nonlocal consumed
            result = super().readinto(buffer)
            consumed += result
            return result

    def opened(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if path == attachment:
            assert mode == "r" and buffering == -1
            return io.TextIOWrapper(io.BufferedReader(Counted(path, "r")), encoding=encoding, errors=errors, newline=newline)
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", opened)
    reads, outputs = {}, {}
    posts = len(shell.posts)
    for name, reader in [("legacy", legacy), ("current", current)]:
        consumed = 0
        monkeypatch.setattr(grades, "full_text", reader)
        monkeypatch.setattr(runner_module, "full_text", reader)
        assert mq(shell, config, "why", "29020") == 0
        outputs[name] = capsys.readouterr().out
        reads[name] = consumed
        assert "hold fail open INCIDENT #42, whose lane squash-merges it directly" in outputs[name]
        assert len(shell.posts) == posts
    print(json.dumps({"caller": "actual mq.main -> Landing.why -> check_hold -> holding -> full_text", "historical_source": hashlib.sha256(historical_path.read_bytes()).hexdigest(), "current_source": hashlib.sha256(Path(grades.__file__).read_bytes()).hexdigest(), "attachment_bytes": size, "invalid_utf8_at": invalid_at, "read_bytes": reads, "posts": len(shell.posts) - posts, "output_equal": outputs["legacy"] == outputs["current"]}))
    assert outputs["legacy"] == outputs["current"]
    assert reads["legacy"] == size
    assert reads["current"] <= min(size, invalid_at + 64 * 1024)
