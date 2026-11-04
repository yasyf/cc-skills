from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound, CompactionState
from .lane_landing import DRIVE_ENV
from .refusal_kinds_need_owner_answer import lane_prs, read_pr
from .tests.node_roll_fixtures import (
    BODIES,
    CLASS_CHANGE,
    COUNTED,
    POOL_CHANGE,
    POOL_TEST_ONLY,
    UNCOUNTED,
)
from .tests.refusal_fixtures import sources

ACTIVE = [CompactionState(active=True, coordinator=True)]
LANE = "alane-a-0123456789abcdef"
NODE_FILE = re.compile(
    r"^infra/(?:rows/[^/]+/nodeClasses|rows/(?:[^/]+/)*nodePools|lib/k8s/parts/autoNodePool)\.ts$"
)
CHANGED = re.compile(r"^(?:\+\+\+ b|--- a)/(?P<path>\S+)$", re.MULTILINE)
COUNT = re.compile(r"\b\d[\d,]*\s+nodes?\b", re.IGNORECASE)
POOL = re.compile(r"\bpools?\b", re.IGNORECASE)
WARNING = (
    "Warning, nothing was blocked: {files} can replace every node of the pools on it. Name each pool this PR drifts and its node count"
    " in the body, such as `forge-executor (51 nodes)`, so the roll is seen before a release."
)


def unnamed_roll(diff: str, body: str) -> str | None:
    files = sorted({path for path in CHANGED.findall(diff) if NODE_FILE.match(path)})
    if not files or (POOL.search(body) and COUNT.search(body)):
        return None
    more = f" and {len(files) - 1} more node files" if len(files) > 1 else ""
    return WARNING.format(files=f"`{files[0]}`{more}")


def ship(command: str, diff: str) -> Input:
    return Input(
        command=command,
        agent_id=LANE,
        state=ACTIVE,
        env=DRIVE_ENV,
        cwd=str(BODIES),
        commands=sources(diff),
    )


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    max_fires=1,
    tests={
        ship(
            f"ccx vcs ship -m 'infra: grow node disks' --pr-body-file {UNCOUNTED}",
            CLASS_CHANGE,
        ): Warn(
            pattern=r"^Warning, nothing was blocked: `infra/rows/k8s/nodeClasses\.ts` can replace every node[\s\S]*forge-executor \(51 nodes\)"
        ),
        ship(f"ccx vcs ship --pr-body-file {COUNTED}", CLASS_CHANGE): Allow(),
        ship(
            f"gh pr create --base dev --title 'infra: move a pool' --body-file {UNCOUNTED}",
            POOL_CHANGE,
        ): Warn(pattern=r"`infra/rows/k8s/nodePools\.ts`"),
        ship(f"gh pr edit 1 --body-file {COUNTED}", POOL_CHANGE): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCOUNTED}", POOL_TEST_ONLY): Allow(),
        ship(f"ccx vcs ship --no-pr --pr-body-file {UNCOUNTED}", CLASS_CHANGE): Allow(),
        Input(
            command=f"ccx vcs ship --pr-body-file {UNCOUNTED}",
            commands=sources(CLASS_CHANGE),
        ): Allow(),
    },
)
def node_rolls_name_pools(evt: BaseHookEvent) -> HookResult | None:
    for req in lane_prs(evt):
        if (read := read_pr(req)) and (said := unnamed_roll(read[0], read[2])):
            return evt.context(said)
    return None
