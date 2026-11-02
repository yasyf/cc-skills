from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from . import release


def run(argv: list[str], prepare: Callable[[], dict], *, cwd: Path, env: Mapping[str, str], checkpoint: Callable[[], None], timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    output = bytearray()
    pending = bytearray()
    with tempfile.TemporaryFile() as errors:
        checkpoint()
        child = subprocess.Popen(release.command(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, cwd=cwd, env=env)

        def remaining() -> float:
            checkpoint()
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            return min(left, 0.05)

        def failed() -> None:
            errors.seek(0)
            stderr = errors.read().decode()
            if child.returncode == -signal.SIGALRM:
                raise subprocess.TimeoutExpired(argv, timeout, output.decode(), stderr)
            if child.returncode:
                raise subprocess.CalledProcessError(child.returncode, argv, output.decode(), stderr)

        def finish() -> None:
            while child.poll() is None:
                interval = remaining()
                try:
                    child.wait(timeout=interval)
                except subprocess.TimeoutExpired:
                    continue
            failed()

        def frame() -> dict:
            while b"\n" not in pending:
                if select.select([child.stdout], [], [], remaining())[0]:
                    data = os.read(child.stdout.fileno(), 65536)
                    if not data:
                        finish()
                        raise EOFError("match-answers ended before its next frame")
                    output.extend(data)
                    pending.extend(data)
            line, _, rest = pending.partition(b"\n")
            pending[:] = rest
            return json.loads(line.decode())

        try:
            os.set_blocking(child.stdout.fileno(), False)
            os.set_blocking(child.stdin.fileno(), False)
            if frame() != {"type": "ready", "protocol": 1}:
                raise ValueError("unexpected match-answers ready frame")
            try:
                data = memoryview((json.dumps(prepare(), ensure_ascii=False) + "\n").encode())
            except BaseException:
                child.stdin.close()
                try:
                    child.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                raise
            while data:
                if select.select([], [child.stdin], [], remaining())[1]:
                    data = data[os.write(child.stdin.fileno(), data):]
            result = frame()
            finish()
            if pending.strip() or child.stdout.read().strip():
                raise ValueError("unexpected trailing match-answers output")
            if result["type"] != "result":
                raise ValueError("unexpected match-answers result frame")
            return result["answers"]
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdin.close()
            child.stdout.close()
