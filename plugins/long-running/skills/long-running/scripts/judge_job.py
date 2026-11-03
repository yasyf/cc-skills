"""One judge call on a Sprite, from the job judge_watch.py wrote to DIR, so no model call runs on the Mac: the verdict and the call's summary go to DIR/result.json."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from long_running import desk_result, judge


def main(directory: Path) -> int:
    job = json.loads((directory / "job.json").read_text())
    call = judge.ask(job["kind"], job["evidence"], job["artifact"], backend=job["backend"], timeout=judge.JUDGE_SECONDS, env=None)
    desk_result.write(directory, json.dumps({"verdict": asdict(call.verdict) if call.verdict else None, "summary": call.summary()}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
