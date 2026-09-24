from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "pr-poll.sh"
FAKES = Path(__file__).resolve().parent / "fakes"

REPO = "acme/widgets"
PR = 7
HEAD = "a" * 40
MOVED_HEAD = "b" * 40
EPOCH = "2000-01-01T00:00:00Z"


def pull(
    *,
    head: str = HEAD,
    mergeable: bool | None = True,
    mergeable_state: str = "clean",
    labels: tuple[str, ...] = (),
    state: str = "open",
    merged: bool = False,
) -> dict:
    return {
        "state": state,
        "merged": merged,
        "head": {"sha": head},
        "base": {"ref": "dev"},
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
        "labels": [{"name": name} for name in labels],
    }


def check_run(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    title: str | None = None,
    summary: str | None = None,
) -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://ci.example/{name}",
        "html_url": f"https://github.com/{REPO}/runs/1",
        "output": {"title": title, "summary": summary},
    }


def event(event_id: int, kind: str, actor: str, *, label: str | None = None, at: str) -> dict:
    return {
        "id": event_id,
        "event": kind,
        "created_at": at,
        "actor": {"login": actor, "type": "Bot" if actor.endswith("[bot]") else "User"},
        "label": {"name": label} if label else None,
    }


def labeled(event_id: int, actor: str = "yasyf", *, at: str = "2026-09-23T23:16:54Z") -> dict:
    return event(event_id, "labeled", actor, label="merge", at=at)


def unlabeled(event_id: int, actor: str = "graphite-app[bot]", *, at: str = "2026-09-23T23:29:06Z") -> dict:
    return event(event_id, "unlabeled", actor, label="merge", at=at)


def comment(comment_id: int, body: str, *, author: str = "yasyf", at: str = "2026-09-23T23:20:00Z") -> dict:
    return {"id": comment_id, "user": {"login": author}, "created_at": at, "body": body}


def surface(
    pr: dict,
    *,
    runs: list[dict] | None = None,
    events: list[dict] | None = None,
    comments: list[dict] | None = None,
    commits: list[dict] | None = None,
) -> dict[str, object]:
    head = pr["head"]["sha"]
    runs = [check_run("build")] if runs is None else runs
    return {
        f"pulls/{PR}": pr,
        f"commits/{head}/check-runs": {"total_count": len(runs), "check_runs": runs},
        f"commits/{head}/status": {"state": "success", "statuses": []},
        f"issues/{PR}/events": events or [],
        f"issues/{PR}/comments": comments or [],
        f"pulls/{PR}/comments": [],
        f"pulls/{PR}/reviews": [],
        "commits": commits or [],
    }


@dataclass
class Run:
    lines: list[str]
    passes: int
    state: dict
    gh_calls: list[str]

    @property
    def done(self) -> str | None:
        return next((line for line in self.lines if line.startswith("DONE ")), None)


@pytest.fixture
def poll(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"watermarks": {"comments": EPOCH, "reviews": EPOCH, "events": {"at": EPOCH, "id": 0}}}))
    runs = 0

    def run(*passes: dict[str, object], env: dict[str, str] | None = None) -> Run:
        nonlocal runs
        runs += 1
        root = tmp_path / f"run{runs}"
        for index, fixtures in enumerate(passes):
            for endpoint, body in fixtures.items():
                target = root / str(index) / f"{endpoint}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(body))
        pass_file = root / "pass"
        pass_file.write_text("0\n")
        log = root / "gh.log"
        log.touch()
        env = {
            **os.environ,
            "PATH": f"{FAKES}:{os.environ['PATH']}",
            "FAKE_GH_ROOT": str(root),
            "FAKE_GH_LOG": str(log),
            "FAKE_PASS_FILE": str(pass_file),
            "FAKE_MAX_PASS": str(len(passes) - 1),
            "PR_POLL_DEADLINE": "0",
            **(env or {}),
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), REPO, str(PR), str(state_file)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return Run(
            lines=result.stdout.splitlines(),
            passes=int(pass_file.read_text()) + 1,
            state=json.loads(state_file.read_text()),
            gh_calls=log.read_text().splitlines(),
        )

    return run
