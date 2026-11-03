from __future__ import annotations

import re
import subprocess
from collections import defaultdict

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Warn, on

from .lane_reports import ACTIVE
from .lane_rotation import DriveActive

POLL_ROOT = re.compile(r"^(?:\S*/)?(?:g?timeout|sleep|tail|bounded\.py)\s|desk-wait\.sh|inboxes\.py wait|\bcci (?:watch|tail -f)")
PS_SECONDS = 10
LANE = "awatcher-lane-1-0123456789abcdef"
TABLE = "ps -A -o pid=,ppid=,command="
ENV_PS = "ps eww -o pid=,command= -p"
ENVS = f"{ENV_PS} 4100"
ROWS = "  4100     1 timeout 240 zsh /s/landing-desk-3/loop3.sh\n  4101  4100 zsh /s/landing-desk-3/loop3.sh\n  4102  4101 sleep 30\n   900     1 sh /s/remote-desks-5/sprite-desk.sh\n"
REAPED = (
    "Reaped {count} orphaned watcher processes this lane left running: {roots}. A poll loop runs under "
    "`bounded.py <seconds> -- <command>` (or desk-wait.sh `--`), which ends its whole tree; GNU `timeout` "
    "leaves any nested `timeout` and its children behind."
)


def rows() -> list[tuple[int, int, str]]:
    out = subprocess.run(TABLE.split(), capture_output=True, text=True, timeout=PS_SECONDS, check=True).stdout
    return [(int(pid), int(ppid), process) for pid, ppid, process in (line.split(None, 2) for line in out.splitlines() if line.strip())]


def owned_by(agent_id: str, pids: list[int]) -> set[int]:
    out = subprocess.run([*ENV_PS.split(), ",".join(map(str, pids))], capture_output=True, text=True, timeout=PS_SECONDS).stdout
    mark = re.compile(rf"(?:^|\s)CCI_AGENT_ID={re.escape(agent_id)}(?:\s|$)")
    return {int(line.split(None, 1)[0]) for line in out.splitlines() if mark.search(line)}


@on(
    Event.Stop | Event.SubagentStop,
    only_if=[FromSubagent(), DriveActive()],
    tests={
        Input(
            agent_id=LANE,
            state=ACTIVE,
            commands={TABLE: ROWS, ENVS: f"4100 timeout 240 zsh /s/landing-desk-3/loop3.sh HOME=/h CCI_AGENT_ID={LANE} PWD=/s\n", "kill -TERM": ""},
        ): Warn(pattern=r"Reaped 3 orphaned watcher processes .*timeout 240 zsh /s/landing-desk-3/loop3\.sh"),
        Input(agent_id=LANE, state=ACTIVE, commands={TABLE: ROWS, ENVS: "4100 timeout 240 zsh /s/landing-desk-3/loop3.sh CCI_AGENT_ID=aother-lane-1-fedcba9876543210\n"}): Allow(),
        Input(agent_id=LANE, state=ACTIVE, commands={TABLE: "   900     1 sh /s/remote-desks-5/sprite-desk.sh\n  4102  3000 sleep 30\n"}): Allow(),
        Input(agent_id=LANE, commands={TABLE: ROWS}): Allow(),
    },
)
def reap_orphaned_lane_watchers(evt: BaseHookEvent) -> HookResult | None:
    """When a lane stops, SIGTERM every poll tree it left reparented to launchd: a `timeout`, `sleep`, `tail`, `cci watch` or desk-wait root whose environment carries the lane's CCI_AGENT_ID, with all its descendants (owner, 8:45 PM Nov 2: "get rid of all those idle watchers now and fix it so they don't stick around in the future")."""
    table = rows()
    roots = [pid for pid, ppid, process in table if ppid == 1 and POLL_ROOT.search(process)]
    if not roots or not (mine := owned_by(evt.agent_id or "", roots)):
        return None
    children: dict[int, list[int]] = defaultdict(list)
    for pid, ppid, _ in table:
        children[ppid].append(pid)
    victims, stack = set(), list(mine)
    while stack:
        if (pid := stack.pop()) not in victims:
            victims.add(pid)
            stack.extend(children[pid])
    subprocess.run(["kill", "-TERM", *map(str, sorted(victims))], capture_output=True, timeout=PS_SECONDS)
    processes = {pid: process for pid, _, process in table}
    return evt.context(REAPED.format(count=len(victims), roots="; ".join(processes[pid][:120] for pid in sorted(mine))))
