from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Runs, Warn, on

from .compaction_handoff import Bound
from .lane_landing import DRIVE_ENV, option
from .pr_ledger import response_text

NOTHING_HOLDS = re.compile(r"cci: --to (?P<lane>(?:go-sweep|go-desk|landing-desk)(?:-\d+)?): (?:.*no (?:live )?lane holds |no lane has registered as \S+ or holds )")
LANDING_ASK_KINDS = frozenset({"ask", "blocked"})
NOTHING_HOLDS_NOTE = "Nothing holds `{lane}`, so nobody reads this: the landing host's go sweep gates every ready head each pass and needs no ask. Read its answer with `lr landing go <n>` on the Mac; a Sprite lane asks `root`."


@on(
    Event.PostToolUse,
    only_if=[Bound(), Runs("cci", "post")],
    tests={
        Input(command="cci post --kind ask --to go-sweep --pr 7 --text 'go?'", env=DRIVE_ENV, output="cci: --to go-sweep: go-sweep stood down and no live lane holds go-sweep; the next lane to register as go-sweep or go-sweep-<n> reads this\n"): Warn(pattern=r"^Nothing holds `go-sweep`"),
        Input(command="cci post --kind blocked --to root,landing-desk --text 'cannot enqueue'", env=DRIVE_ENV, output="cci: --to landing-desk: no lane holds landing-desk; the first lane to register as landing-desk or landing-desk-<n> reads this\n"): Warn(pattern=r"^Nothing holds `landing-desk`"),
        Input(command="cci post --kind head --to landing-desk --text 'HEAD #7 abc'", env=DRIVE_ENV, output="cci: --to landing-desk: no lane holds landing-desk; the first lane to register as landing-desk or landing-desk-<n> reads this\n"): Allow(),
        Input(command="cci post --kind ask --to landing-desk-9 --text 'go?'", env=DRIVE_ENV, output="cci: --to landing-desk-9: no lane has registered as landing-desk-9; landing-desk-3 holds landing-desk, which --to landing-desk reaches, and landing-desk-9 reads this once it registers\n"): Allow(),
        Input(command="cci post --kind ask --to meta-desk --text 'go?'", env=DRIVE_ENV, output="cci: --to meta-desk: no lane holds meta-desk; the first lane to register as meta-desk or meta-desk-<n> reads this\n"): Allow(),
        Input(command="cci post --kind ask --to go-sweep --text 'go?'", output="cci: --to go-sweep: no lane holds go-sweep; the first lane to register as go-sweep or go-sweep-<n> reads this\n"): Allow(),
    },
)
def nothing_holds_a_landing_ask(evt: BaseHookEvent) -> HookResult | None:
    asks = any(kind in LANDING_ASK_KINDS for call in evt.cmd.calls("cci") for kind in option(call.args, "--kind"))
    found = NOTHING_HOLDS.search(response_text(evt.tool_response)) if asks else None
    return evt.context(NOTHING_HOLDS_NOTE.format(lane=found["lane"])) if found else None
