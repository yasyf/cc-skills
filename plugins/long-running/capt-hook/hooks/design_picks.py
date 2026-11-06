from __future__ import annotations

import json
import re

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .compaction_handoff import Bound, bus_name, drive_entry
from .gate5_release import LR
from .lane_landing import DRIVE_ENV, SPRITE_ENV, incident_lane, lane_name
from .owner_quotes import bodies
from .pr_body_frozen import PR_EDIT, SHIP
from .release_cards import Unread, read
from .tests.register_fixtures import FIXTURES
from .vet_before_push import branch_diff, in_lane

PICK = re.compile(r"\bowner:m\d+(?:\.\d+)?\b|\b(?:ccn|answer)[: ][0-9a-f]{7,}\b")
PLACEMENT = re.compile(r"\b(?:volumeclaim|persistentvolume|pvc|storageclass|reclaim|retention|evict)\w*", re.IGNORECASE)
ROWS = "infra/rows/"
CARDS_SECONDS = 30
REFUSAL = (
    "This PR waits for the owner's pick, since only the incident fix and its headroom ship ahead of his design: {found}."
    " Cite his pick in the PR body as `owner:m<N>` or its ccn answer id, or push the work as a branch with no PR."
)
INCIDENT = "Warning, nothing was refused: this incident lane's PR goes ahead as the incident fix or its headroom, though {found}; anything past the fix waits for his pick."
UNREAD = "Warning, nothing was refused: {why}, so this PR goes ahead without the design-card check. Hold it if it touches the paths `lr board cards paths` prints."

LANE_ENV = DRIVE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
CARDS = f"{LR} board cards paths"
STORAGE = json.dumps({"card-journal-storage": ["go/sand/internal/cloudrunner/journal", "infra/rows/cloudrunner"]})
REPO = {"git rev-parse --show-toplevel": "/w\n", CARDS: STORAGE}
JOURNAL = REPO | {"git diff": "diff --git a/go/sand/internal/cloudrunner/journal/evict.go b/go/sand/internal/cloudrunner/journal/evict.go\n+func evictFinished() {}\n"}
SPRITE_JOURNAL = {command: said for command, said in JOURNAL.items() if command != CARDS} | {f"{CARDS} --drive release-v3": STORAGE}
ROWS_PVC = REPO | {"git diff": "diff --git a/infra/rows/executor/template.ts b/infra/rows/executor/template.ts\n-  storage: '5Gi', reclaimPolicy: 'Retain',\n+  storage: '5Gi', reclaimPolicy: 'Delete',\n"}
ROWS_SCRATCH = REPO | {"git diff": "diff --git a/infra/rows/llm-gateway.ts b/infra/rows/llm-gateway.ts\n+  volumes: [{ name: 'tmp', emptyDir: {} }],\n+  volumeMounts: [{ name: 'tmp', mountPath: '/tmp' }],\n"}
ROWS_CLAIM = REPO | {"git diff": "diff --git a/infra/rows/executor/template.ts b/infra/rows/executor/template.ts\n+  volumeClaimTemplates: [{ metadata: { name: 'data' } }],\n"}
ROWS_LABEL = REPO | {"git diff": "diff --git a/infra/rows/executor/template.ts b/infra/rows/executor/template.ts\n+  labels: { team: 'plat' },\n"}
ELSEWHERE = REPO | {"git diff": "diff --git a/api/src/a.ts b/api/src/a.ts\n+export const a = 1\n"}
SHIP_IT = "ccx vcs ship -m 'cloudrunner: 🐛 evict finished journals'"
PICKED = "gh pr create --title 'cloudrunner: ✨ evict finished journals' --body 'Builds owner:m481, his pick on the journal storage card.'"
FLEETS = REPO | {"git diff": "diff --git a/infra/rows/sandsql/fleets.ts b/infra/rows/sandsql/fleets.ts\n+  storageClass: 'gp3', reclaimPolicy: 'Retain',\n", CARDS: "{}"}
DRY_SHIP = "ccx vcs ship --dry-run --tip-only --no-watch --pr-title-file t --pr-body-file {body} infra/rows/sandsql/fleets.ts"


def shipping(evt: BaseHookEvent) -> list[Call]:
    return [call for call in evt.command.calls("gh") if PR_EDIT.bind(call).values.get("words", ())[:2] == ("pr", "create")] + [
        call for call in evt.command.calls("ccx") if SHIP.bind(call).values.get("words", ())[:2] == ("vcs", "ship")
    ]


def touched(diff: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    lines: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            lines = files.setdefault(line.rsplit(" b/", 1)[1], [])
        elif line.startswith(("+", "-")) and not line.startswith(("+++ ", "--- ")):
            lines.append(line[1:])
    return files


def under(path: str, prefix: str) -> bool:
    return f"{path}/".startswith(f"{prefix.rstrip('/')}/")


def design_cards(evt: BaseHookEvent) -> dict[str, list[str]]:
    if (entry := drive_entry(evt)) is None:
        raise Unread("this session names no registered drive")
    return json.loads(read([str(LR), "board", "cards", "paths", "--drive", bus_name(entry)], CARDS_SECONDS))


@on(  # capt-hook: no-escape the owner ordered that nothing in a subsystem under his open design ships before his pick, and every misread fails open with a warning
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Or(Runs("gh"), Runs("ccx", "vcs", "ship"))],
    tests={
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=JOURNAL): Block(pattern=r"^This PR waits for the owner's pick, since only the incident fix[\s\S]*`go/sand/internal/cloudrunner/journal/evict\.go` under card `card-journal-storage`"),
        Input(cwd="/w", command=PICKED, env=LANE_ENV, commands=JOURNAL): Allow(),
        Input(cwd="/w", command=SHIP_IT, env=SPRITE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}, commands=SPRITE_JOURNAL): Block(pattern=r"`go/sand/internal/cloudrunner/journal/evict\.go` under card `card-journal-storage`"),
        Input(cwd="/w", command=SHIP_IT, env=DRIVE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "incident-journal-disk"}, commands=JOURNAL): Warn(pattern=r"^Warning, nothing was refused: this incident lane's PR goes ahead"),
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=ELSEWHERE): Allow(),
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=ROWS_PVC | {CARDS: "{}"}): Block(pattern=r"`infra/rows/executor/template\.ts` changes where data lives or how long it is kept"),
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=ROWS_CLAIM | {CARDS: "{}"}): Block(pattern=r"`infra/rows/executor/template\.ts` changes where data lives or how long it is kept"),
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=ROWS_SCRATCH | {CARDS: "{}"}): Allow(),
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=ROWS_LABEL | {CARDS: "{}"}): Allow(),
        Input(cwd="/w", command=DRY_SHIP.format(body=FIXTURES / "design-picks" / "picked.md"), env=LANE_ENV, commands=FLEETS): Allow(),
        Input(cwd="/w", command=DRY_SHIP.format(body=FIXTURES / "owner-quotes" / "log-line.md"), env=LANE_ENV, commands=FLEETS): Block(pattern=r"`infra/rows/sandsql/fleets\.ts` changes where data lives or how long it is kept"),
        Input(cwd="/w", command=SHIP_IT, env=LANE_ENV, commands=ELSEWHERE | {CARDS: (1, "cc-present down")}): Warn(pattern=r"^Warning, nothing was refused: `board cards` exited 1: cc-present down"),
        Input(cwd="/w", command="gh pr view 12", env=LANE_ENV, commands=JOURNAL): Allow(),
        Input(cwd="/w", command=SHIP_IT, env=DRIVE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "root"}, commands=JOURNAL): Allow(),
    },
)
def ships_wait_for_his_design_pick(evt: BaseHookEvent) -> HookResult | None:
    if not in_lane(evt) or not (calls := shipping(evt)) or any(PICK.search(body) for body in bodies(evt)):
        return None
    if isinstance(diffed := branch_diff(evt, calls[0], "-U0"), str):
        return evt.context(UNREAD.format(why=diffed))
    if not (files := touched(diffed[1])):
        return None
    found = [f"`{path}` changes where data lives or how long it is kept" for path, lines in files.items() if path.startswith(ROWS) and any(PLACEMENT.search(line) for line in lines)]
    try:
        cards = design_cards(evt)
    except (Unread, ValueError) as failed:
        if not found:
            return evt.context(UNREAD.format(why=failed))
        cards = {}
    found += [f"`{path}` under card `{card}`" for card, prefixes in cards.items() for path in files if any(under(path, prefix) for prefix in prefixes)]
    if not found:
        return None
    if incident_lane(evt, lane_name(evt) or ""):
        return evt.context(INCIDENT.format(found="; ".join(found)))
    return evt.block(REFUSAL.format(found="; ".join(found)))
