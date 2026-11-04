from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, Warn, on

from .compaction_handoff import CompactionState, Coordinator, coordinator_tests
from .lane_rotation import DriveActive
from .owner_facing import post_values, posted_text

ACTIVE = [CompactionState(active=True, coordinator=True)]
FAILURE_KINDS = frozenset({"blocker", "incident", "urgent"})
NAMES_LANE = re.compile(r"\blane=[\w.-]+")
SETTLED = re.compile(r"\bFIX-LIVE\b|\bnot-ours:\s*\S|\bclosed:\s*\S")
DEFERS = re.compile(r"\bblocked until\b|\bnoted\b|\bknown\b|\btracked\b", re.IGNORECASE)
READ_SECONDS = 10
BLOCKER = json.dumps({"seq": 339405, "kind": "blocker", "text": "every plat k8s release fails preview"})
ASK = json.dumps({"seq": 339405, "kind": "ask", "text": "which stack reads the eks outputs?"})


def resolutions(evt: BaseHookEvent) -> list[tuple[list[str], str]]:
    posts = [values for call in evt.command.calls("cci") if (values := post_values(evt, call)).get("words", ())[:1] == ("post",)]
    return [([seq.lstrip("#") for value in values["resolves"] for seq in str(value).split(",") if seq], posted_text(values)) for values in posts if values.get("resolves")]


def names_fixer(text: str) -> bool:
    return bool(NAMES_LANE.search(text)) or bool(SETTLED.search(text)) and not DEFERS.search(text)


def resolves_failure(seqs: list[str]) -> bool:
    done = subprocess.run(["cci", "show", *seqs, "--json"], capture_output=True, text=True, timeout=READ_SECONDS)
    return done.returncode == 0 and any(json.loads(line)["kind"] in FAILURE_KINDS for line in done.stdout.splitlines() if line.strip())


class UnfixedResolution:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(not names_fixer(text) and resolves_failure(seqs) for seqs, text in resolutions(evt))


def resolve(text: str, shown: str = BLOCKER, **extra: object) -> Input:
    return Input(command=f"cci post --drive d1 --lane root --kind decide --resolves 339405 --text '{text}'", state=ACTIVE, commands={"cci show": shown}, **extra)


UNNAMED_FIXER = (
    "Warning, nothing was blocked: A coordinator resolution of a blocker, incident or urgent record names who fixes it: spawn the fix lane and name it with `lane=<name>`, or land the fix."
    " A failure that already stopped resolves with `FIX-LIVE`, `not-ours:<who>` or `closed:<why it stopped>`."
)


@on(
    Event.PreToolUse,
    only_if=[Coordinator(), Tool("Bash"), DriveActive(), UnfixedResolution()],
    skip_if=[FromSubagent()],
    tests=coordinator_tests({
        resolve("blocked until #48401 lands"): Warn(pattern=r"^Warning, nothing was blocked: A coordinator resolution of a blocker"),
        resolve("noted, tracked in the teardown stack"): Warn(),
        resolve("closed: a known flake, blocked until Monday"): Warn(),
        resolve("lane=hsbc-pr2-ci-1 lands #48401 by 4:40 PM"): Allow(),
        resolve("FIX-LIVE #48401 landed as 4821788"): Allow(),
        resolve("closed: previews pass since #48401 landed"): Allow(),
        resolve("blocked until #48401 lands", ASK): Allow(),
        resolve("blocked until #48401 lands", agent_id="a1b2c3"): Allow(),
        Input(command="cci post --drive d1 --lane root --kind decide --resolves 339405 --text 'blocked until #48401 lands'", commands={"cci show": BLOCKER}): Allow(),
        Input(command="cci post --drive d1 --lane root --kind go --to landing-desk --text 'go #48401 at b555140'", state=ACTIVE): Allow(),
    }),
)
def resolutions_name_their_fixer(evt: BaseHookEvent) -> HookResult:
    return evt.context(UNNAMED_FIXER)
