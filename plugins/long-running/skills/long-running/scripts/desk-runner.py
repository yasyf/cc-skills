#!/usr/bin/env python3
"""The scripted desk: root commands become durable actions addressed to a lane's current Orca dispatch, the Run inbox is read by sequence, ready bottom prefixes enqueue, and deadlines are checked, all without a model turn.

    desk-runner.py relay  --config PATH --key KEY --lane LANE --text TEXT [--reply-to MSG] [--deadline-minutes N]
    desk-runner.py launch --config PATH --key KEY --lane LANE --model M --effort E --brief PATH [--owner-directed]
    desk-runner.py policy --config PATH --key KEY --landing prefix|whole --revision REV --source TEXT [--supersedes REV]
    desk-runner.py run    --config PATH --desk orca|landing [--once]
    desk-runner.py rebind --config PATH
    desk-runner.py show   --config PATH

STDLIB ONLY. Every action lives in the store `actions.py` owns: one container per lane,
`desk-lane-<lane>`, whose owner is the lane's current Orca dispatch, plus `desk-landing`
and `desk-runner`. An action id is the command's key, so a command submitted twice
yields one action. A relaunch offers the lane's container to the new dispatch, and that
dispatch's first `started` reply takes it at the next owner generation, so a stale
dispatch can never start or finish an action.

A relay is accepted, then started and completed by the lane itself: it replies
`started <key>` and `done <key>: <result>` on the action's thread. The send is its own
`send:` action, delivery evidence only. A send whose response is lost, Orca's runtime
unreachable or its output not JSON, becomes `unverifiable` and is settled from Orca's request receipt or the recipient's mailbox.
With neither thirty seconds on, the runner sends the relay again on the same thread,
under the same key, and repeats that once every thirty seconds until Orca holds a
receipt or the message shows on the thread. A delivered resend logs nothing. A status
message the lane posts on the Run that names the relay's R-number or cci sequence
acknowledges it, as `started` when its subject starts so and `done` otherwise, and
settles every send of it, since a lane that already read its mailbox leaves no
message there to find. Two resends all lost the same way log `UNACKED` once, with
the count and the relay text on the record's path, and nothing more is sent. Any other
Orca error fails the send, though Orca names a request id on every refused mutation,
so a refusal is never resent as lost. A reply Orca refuses with `answer_conflict`, its question
already holding another answer, goes at once to the dispatch as a message on the relay's thread
instead. A send with a receipt is never resent,
and a send to a dispatch that is no longer the lane's live one logs `UNVERIFIABLE`
and is never resent. An accepted relay to a lane with no live dispatch and no
launch in flight fails at once as `RELAY-FAILED`, unless a send already delivered it;
a delivered relay is never failed or reported again. A lane has no live dispatch only
when it has no receipt or Orca answers that its dispatch is gone. When Orca gives no
answer, a runtime or connection error, a timeout, or output that is not its JSON, the
pass stops there and changes no dispatch's liveness and no relay's state. The first
such pass logs `ORCA-UNREACHABLE` once for the streak, each failed pass doubles the
wait before the next, up to two minutes, and the first pass Orca answers again logs
`ORCA-BACK`. `cci post` keeps only the first
line of a text over its 400-character limit in the record, clipped, and writes the
whole text to a file the record names by path. The runner reads a relay record's or a
root reply's text from that file and relays all of it, line breaks kept: a relay's
first line is the directive and its text runs to the end of the record. A file whose
first line is not the record's text is an attachment, and the record's own text is
relayed, unless cci clipped the record. A record whose file cannot be read on this
machine is refused as `RELAY-FAILED` naming the path, and nothing is relayed.

`run --desk orca` relays, launches, consumes the Run mailbox, sweeps stale mail, and
checks relay deadlines. Each pass takes the Run's oldest unacknowledged Delivery before
it reads the inbox and acknowledges it once that read is relayed, so Orca stops counting
a relayed or skipped message as unread. Each relayed message id is saved, and a Delivery
message with no saved id is relayed before the acknowledgement whatever its sequence, or
logged `UNRELAYED` when it sits behind the cursor and its type has no relay. Prompts
belong to `prompt_watch.py`, the one reader
of Orca's agentWait; the sweep reads its last poll and escalates `PROMPT-WATCH-DOWN` once an hour
while that poll is over two minutes old. It also reads the drive's desk inbox stream and turns
each new `orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>` line into one relay
per lane, a reply to the lane's latest open question no other reply claims, logged once as
`RELAYED` or `RELAY-FAILED`. Each new `orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>`
line is the `launch` command under its key, `NOW` meaning `--owner-directed` and `role=` meaning `--role`, refused as
`LAUNCH-FAILED` while the lane has a live dispatch or a launch in flight; every verified
launch logs `LAUNCHED` with its dispatch and terminal. The runner reads each lane's
dispatch and terminal from the Run's receipt directory, `~/.claude/scratch/orca-launch/<run>`,
where orca-launch.sh writes by default, so a lane launched by hand is seen like one the
runner launched. An `orca-desk: alert <slug> <link> :: <what fired>`
line, which monitor-watch writes, records the transition and launches nothing. An
`orca-desk: incident` line in the same form, with `reported=<HH:MM>` or `reported=<H:MM AM|PM>`
before the `::` naming the first report's Pacific time, which the alerts desk writes when it judges a lane necessary,
launches `<slug>-fix` on the incident alias from the alert-fix brief, unless another owner holds the
incident: the root's `done` resolving a record that names the slug, its `stopped` addressed to
`<slug>-fix`, or another drive's root `answer` addressed to `root@<this drive>` naming the slug. The
runner checks at intake and again before the launch starts, fails a pending launch, and posts one
`state` record `INCIDENT-OWNED <key> <lane>: incident <slug> owned by <who>; no launch`.
Every incident fix lane the runner launched is on a clock from its verified launch: a
launch on the incident alias, or on a brief whose `ccx:` line says `role=incident` or names
an `incident=`, unless the launch's own role or that brief names a role other than `fix` or
`incident`; `desk-launch.py incident` launches its evidence lane with `role=evidence`. Each pass
reads the lane's own cci records of kind `mechanism`, `opened`, `ready`, `fix-live`,
`not-ours`, `stopped`, and `stand-down`, a `report` or `incident` that starts `MECHANISM`
or `READY`, and its `mechanism:`, `ready:`, and `fix-live:` Run statuses. A lane's records
count under `<name>` and `<name>-fix` alike, since the desk launches `<name>-fix` and its
lane may post as `<name>`. With no mechanism at
`deadlines.incident_mechanism_minutes` (default 10), or no PR at
`deadlines.incident_pr_minutes` (default 15), the runner launches `<lane>-opus` once per
launch, urgent, on opus xhigh in Claude fast mode, from the lane's own brief under a
fallback header it attaches to the briefs log as `<lane>-opus.full.md`, and logs
`INCIDENT-CLOCK <lane> mechanism` or `INCIDENT-CLOCK <lane> pr`. With no `ready` at
`deadlines.incident_ready_minutes` (default 20) it logs `INCIDENT-CLOCK <lane> ready`,
naming the missing milestones and the minutes each phase took. A later milestone counts
for the earlier ones, and each threshold logs once per launch. A `-opus` lane is clocked
and gets no fallback of its own; a lane whose `<lane>-opus` already has a live dispatch or
a launch in flight gets no second. When the Orca launch of `<lane>-opus` fails, on load or
otherwise, the runner launches it once more under `<key>:retry`; when that fails too while
the lane's clock runs, it logs `INCIDENT-CLOCK <lane> in-process`, naming both failures and
the fallback brief, for the root to start `<lane>-opus` in its own session as a
`long-running:lane-ship` Agent. A fix lane gets one Opus fallback: a lane an incident
executor adopted gets none from the clock while that executor's own Opus backup is launching
or live, and `incident.py` launches no backup while the clock's `<lane>-opus` is. The clock
stops at `ready`, `fix-live`, `not-ours`, `stopped`, or `stand-down`, or when the dispatch
settles, and `show` prints each clocked lane's minutes and milestones.
Only an incident fix lane's partial-fix reports launch case lanes: when its `ready:` or
`fix-live:` Run status carries `UNFIXED <lane>: <case>` lines in its body, the runner launches
`<first>-case<n>` on the incident alias for each case in the same pass, where `<first>` is
the lane's name without its trailing `-opus` and `-case<n>` parts and `<n>` is the lowest
no lane container holds. A `worker_done` launches nothing. The brief is the
unfixed-case brief filled with the case verbatim, the reporting lane, the path of that
lane's brief, and the PRs its cci `opened` records name, attached to the briefs log as
`<first>-case<n>.full.md`. The runner logs
`INCIDENT <lane> <time>: its fix does not cover: <case> | case lane <first>-case<n> launching …`
for the root to ratify. Only a line that names the reporting lane counts, and
`UNFIXED <lane>: none` launches nothing. A case launches once across a lane and its `-opus`
fallback, however often and by whichever of them it is reported, and a case whose launch is
refused launches on the next report that names it. An incident launches at most
`incident.case_lanes` case lanes (default 2). Every other new case of a report goes to the
root in one `DECIDE` that carries, per case, the ready
`orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>` line from a brief
already attached: each case past that cap, each new case a case lane reports, since case
lanes are one generation, and each case of a report whose text, or whose lane's newest
`mechanism` record or status, says `UNKNOWN`. An incident ends at a `not-ours` or
`stand-down` record its fix lane or `-opus` fallback posts, at a root `stand-down` or `stopped` addressed
to either or to the case lane, and, for a report, at a `stopped` its lane posted before it.
A report whose subject or first body line opens with `NOT-OURS`, `stood down`, or `Stopped` launches no case
lane and files no `DECIDE`. A report after the end, or from a lane the root withdrew, logs
`UNFIXED <lane> <time>: <cases> | no case lane launched: …` and launches nothing, and a
case launch still pending at the end fails and logs `WITHDRAWN`. A case already known,
such as a case lane's own, launches nothing. A case
lane is an incident fix lane, so the clock covers it.
Each cci record addressed to `orca-desk` past the runner's saved sequence is read as one
desk inbox line, starting at the drive's newest record, except a record `cci import` took
from the desk inbox itself or one that carries `--re`; a record that is no directive logs
`DIRECTIVE-FAILED`, and a failed `cci tail` logs `CCI-TAIL-FAILED` and rereads from the same
sequence. An `orca-desk: withdraw <target>` line names a launch key, `R<n>` or a case
launch's `unfixed:<msg>:<n>`, or a lane. It fails each launch under the key, or each of the
lane's launches, that the runner accepted and has not started, and stops the dispatch the
key or lane launched with worker-stop, as `orca-gc --done` does. It logs `WITHDRAWN` only once
Orca shows the dispatch settled and its terminal gone or at an idle prompt within 30 seconds,
and `WITHDRAW-UNCONFIRMED` with the dispatch and terminal otherwise, as when worker-stop
answers `stop_unknown`. The
lane is marked withdrawn: its `UNFIXED` lines launch no case lane, and an Orca question it
leaves, such as the offer to dispatch it again, logs `WITHDRAWN` instead of `DECIDE`; a
case lane whose dispatch the root stopped by hand is marked the same way. A launch still
starting, a settled launch with no dispatch, a key or lane the runner holds nothing under,
or a worker-stop Orca refuses logs `WITHDRAW-FAILED`. Each root cci record carrying `--re`, read in sequence
order with the records addressed to `orca-desk`, is a reply: one to the runner's `DECIDE` on a worker question replies to that
question, one to a lane's own record replies to that lane's open Orca question, which the cci
reply superseded, and a `withdraw` of a launch record addressed to `orca-desk` withdraws that
launch.
Orca lets only the terminal bound to the Run call
worker-start. Every pass the runner reads the Run's coordinator from Orca and acts as that
terminal: each orca call and each launcher it starts carries ORCA_TERMINAL_HANDLE set to the
coordinator and an empty ORCA_PANE_KEY, so a root that rotates to a new terminal needs no
runner restart. It records the coordinator, the Run's generation, and how the binding was
obtained, at start and before each pass that launches. When the Run has no coordinator, or
Orca finds that terminal gone, it refuses to start and prints the rebind command; a binding
lost mid-run holds every launch and escalates UNBOUND once. `rebind` runs
`orca orchestration run-use` from the current terminal and records it; `show` prints the
binding first. No Mac launch starts while the 1-minute load
is above the core count, because an Orca worktree checkout times out under that load, and
each ten minutes of it logs one `LOAD-HIGH` blocker telling the root to move the load. An
incident or `--owner-directed` launch fails at once with `LAUNCH-FAILED`; any other launch
waits for at most `deadlines.load_hold_minutes`, then reports the failure to root through
cci and the Run mailbox. With `orca.sprite` configured, a launch whose brief's `ccx:` line
says `place=remote`, or `cpu=high` with no `place=`, runs `orca.sprite.launcher`, the
repository's worker-launch.sh, on a Sprite instead, never waiting on Mac load. The
runner counts no Sprites and sets no limit: the provider admits or refuses each one.
`place=local` keeps a `cpu=high` launch on the Mac. Fable, incident, an incident clock
fallback, and a `role=desk` or `role=watch` brief stay local whatever the line says. A lane name a Sprite refuses,
or a Sprite launch whose receipts prove worker-start never ran, a provider refusal
among them, starts the same launch locally, and the fallback logs `SPRITE-FALLBACK`.
The proof is read from the brief's `.worker` directory: no `<lane>.json`,
and a nonzero exit in `prepare.status` or `attach.status`,
or no `prepare.status` when the runner itself saw the launcher exit. A runner that
restarted mid-launch saw no exit. Any other Sprite launch that prints no ready line
may have left a remote worker: it becomes `unverifiable` and logs
`SPRITE-UNVERIFIABLE` with that directory and the Task and Dispatch its `<lane>.json`
names, and nothing is launched locally, retried, or cleaned up. A ready Sprite
launch's receipts are copied beside the local ones. `run --desk landing` gates and enqueues ready
prefixes under the accepted landing policy, verifies landings by squash, and routes
blockers and restacks. Each landing pass first fast-forwards the landing checkout to the remote trunk, so every
`stack-enqueue` it runs is the trunk's; a checkout that cannot fast-forward logs `CHECKOUT-STALE` to root and gates nothing that pass.
A prefix `ccx vcs pr status` reads out of the queue at the heads it
admitted is gated again, unless the PR's Merge activity comment names a merge conflict,
which goes to the lane as a blocker. A red check or a downstack failure is gated again, and `stack-enqueue`
decides whether the PR's own head failed or a batch-mate did. A drop for downstack failures on a PR outside the PR's own stack is the
batch's failure: it routes no blocker, logs one `AWAITS-GO` line to root, and the same head re-enqueues once a root
`go` record naming the PR is posted after the drop, with no new head. The lane a PR's blocker or restack goes to is the poster of the newest cci `head`, `opened`, `ready`,
or `claim` record naming the PR, else the lane that registered its ledger row; a blocker posted on cci also goes to the root
when `cci who` lists no live session for that lane. A gate's `BLOCKED` verdict goes to the lane at most once per head, and only for red CI,
a merge conflict, a changes-requested review or fidelity grade, or a queue ejection, never for a pending or running check or a
missing approval or an ejection blaming a PR outside its stack, and never for a PR that is held, carries GitHub's `hold` label, or is
stacked above such a PR until that hold lifts. A PR whose base is another open row's branch gets no `graphite FAILING_REQUIRED` blocker
unless a commit status other than `landing-gates`, `release-pr-check`, `emergency-head`, or `fidelity` is red on its own head. It gates only tips the ledger lists as `ours`, every tip of one stack in one call, and never enqueues a prefix
holding a PR that no drive lane registered or posted opened on cci. It logs one `NOT-OURS`
escalation per head of an open stack-top PR it skips for that reason. Every PR an open cci `hold` record
names is held until a `lift --resolves <seq>` closes that record: the runner puts GitHub's `hold` label on it, which
the required `landing-gates` check enforces, and removes that label once the record closes. A `hold:all <reason>` line in
the holds file holds every open row except those a `release #<n>` or `release lane:<name>` line names. A
`<lane> HOLD <reason>` or `HOLD <lane> <reason>` line holds a lane's open rows the way `lane:<name>` does. `holds.py`
parses the file. A line that matches none of its forms, or a lane line naming a lane no ledger row carries in any state,
may hold something the runner cannot name. The runner checks the file first thing on each pass, an empty ledger included: on such a pass it
gates and enqueues nothing, and logs `HOLDS-UNREAD` once per distinct line, quoting it. Each enqueue re-reads the file under the same
rule just before its call and completes as `blocked` on such a line. The same moment it reads every hold fresh, whatever the
pass read earlier: the holds file, the `hold` label GitHub serves for each PR, and the PR's cci `hold` records. An enqueue completes as
`refused`, with one `REFUSED` line naming each hold, when a line, label, or open record holds a PR in its prefix, when a `hold` record of any status was
posted after the enqueue was accepted, or when the PR's newest `hold` record is newer than its newest `ready` record, so a stale
`ready` never enqueues a PR after a hold. The same read repeats once `stack-enqueue` returns success: a hold that appeared during the
call completes the enqueue as `dequeued`, after `stack-enqueue --dequeue` takes each held PR and each PR above one back out of the queue
top first, with a `DEQUEUED` line; a dequeue that fails logs `DEQUEUE-FAILED`. Both outcomes enqueue again once nothing holds the prefix. The holds file stays the root's interface, and the
runner mirrors it onto GitHub's `hold` label, which the required `landing-gates` check fails on every path into the queue.
On each pass that reads the file, every open ledger row a PR line, a lane line, `hold:all`, or an open cci `hold` record
holds gets the label, and a label the runner added comes off once nothing holds the row. A `cci tail` that fails skips
the label mirror for that pass. The action book records each label the runner adds or removes,
so a restart posts none again, a `hold` label a human added is never removed, and a row that lands or closes keeps its label.
Removing a label the runner added is a lift: once a ledger refresh after the add reads the PR without it, and only the PR's `#<n>`
holds-file lines hold it, the runner removes those lines through `holds.py`, never puts the label back, and logs `HOLD-LIFTED`.
A label write GitHub refuses logs `LABEL-FAILED` once and runs again on each pass. A prefix
enqueues only when every PR in it has a fidelity ok at its current head: the newest cci `review` record from
a fidelity lane (`landing.fidelity_lane`, one lane name or a list of them, which the landing desk refuses to start without) about that PR whose text starts
`fidelity: ok` or `fidelity: missing` and names that head by a short sha, and it reads `fidelity: ok`; a head no fidelity lane has graded
also passes when its `fidelity` commit status reads `success`, and the status mirror leaves such a head's status alone. A head no
grade names carries the ok of an older head an ok names, whose own newest grade is ok, when `git patch-id --stable` of both heads'
diffs against the PR's base, read from GitHub's compare, is equal, cited as `fidelity: ok at <head>, patch-identical to <graded head>`;
a compare GitHub refuses for the PR's own head logs `PATCH-READ-FAILED` once and carries nothing.
Any other head gets one cci `blocker` per head and grade, to the PR's lane and the fidelity lane, and nothing
in its prefix is enqueued. On every pass the runner also mirrors the newest grade
of each open row's head into that head's `fidelity` commit status, `success` for `fidelity: ok` and `failure` for `fidelity: missing`,
described by the grade's first line and cci seq, once per grade record. The required `landing-gates` check holds any head whose
`fidelity` status is not `success`, so a head pushed after its grade waits for its own whoever enqueues it. A head with no grade reads `pending`, "awaiting <fidelity lane>'s grade of this
head", until the lane grades it; the runner reads the head's statuses afresh just before that post and skips it when any `fidelity` status is already there or the read fails, and a post GitHub refuses logs `STATUS-FAILED` once and runs again on each pass.
A pass posts at most three statuses, the heads of PRs a `release` line names or a believed-queued prefix holds first, then the rest by PR number, so a backlog of low-numbered replays never starves a released head.
Each pass reads the commit statuses of every open row's head and every Graphite queue draft's head in one GraphQL query, and reuses a
head's statuses for 60 s unless the runner itself posts to that head or re-dispatches its grade; a head read that fails logs
`STATUS-READ-FAILED` once and counts as unread for that pass. Each pass starts with one free `rate_limit` read of the GitHub App
installation's quotas through `ccx vcs gh`. With the REST or GraphQL quota under 5% of its limit, the pass reads nothing from GitHub and
logs one `GITHUB-BACKOFF` naming the calls left and the reset per quota window; accepted enqueues stay accepted and root `go` records
stay on cci, so both run on the first pass after the reset. A quota read that fails logs `QUOTA-READ-FAILED` once an hour, and the pass runs. A `cci tail` that fails while mirroring skips the mirror for that pass, and one that fails anywhere
else in a landing pass ends that pass before it gates or enqueues further; either logs `CCI-TAIL-FAILED` and the runner keeps running. A PR whose ledger row lands carrying the `merge-fast`
label, and whose body has a `Release-With:` line, sends one cci `release` record to `landing.release_owner` and the root in the pass
that reads the landing, with `--topic <pr> --pr <pr>`, naming the squash, the components, and the release to start. Until a cci
`release` record on the drive from another lane, stamped after the landing, names one of those components with `--target` or the
PR with `--pr`, the runner posts one `blocker` to both each `deadlines.release_start_minutes` (default 10) after the landing, with
the minutes elapsed. A `release` record on another drive, which `cci tail` returns as peer mail, settles nothing. A record the
drive's root posts with `--re` or `--resolves` naming the release record or one of its blockers ends them too. A landing older
than the landing runner's first pass, or a landed row with no `landed_at`, is history and sends nothing. Each record carries
`--id release:<pr>:<step>`, so a repost after a crash returns the stored record and no step is posted twice. A record cci refuses,
or a post that prints no `#<seq>`, is posted again on the next pass and escalated to root as `RELEASE-FAILED`, once per
`deadlines.release_start_minutes` step. A worker's question escalates to root as `DECIDE`, and only a reply this
drive's root posts with `--re` on that `DECIDE` or on the lane's own record answers it.
Between orca passes the runner waits ten seconds, cut short within a quarter second by a desk
inbox append or an exiting launch, and its sweep shows only dispatches it has not
named for reclaim. Each escalation runs
`cci post --drive <drive> --lane desk-runner --to root --kind <k> --topic <key>`,
once per cause. The key is the line's second token. DECIDE maps to decide, INCIDENT to incident,
INCIDENT-OWNED to state, UNBOUND, UNOWNED, UNACKED, INCIDENT-CLOCK, and HOLDS-UNREAD to blocker, any *-FAILED to defect, and all other labels to report.
Text over cci's 400-character limit is clipped; the full line is written to
`<receipts>/cci/<hash>.txt` and attached with `--path`. A line relaying an Orca question,
escalation, worker_done, status, decision gate or handoff is a summary: its receipt carries
the full line and then the whole message, subject and body, kept under
`<receipts>/cci/messages/<id>.md` when the message arrives. Landing restack routes
sent through cci use `--kind blocker --to <lane> --topic <pr>` and the same text limit.
The config requires `drive` and `orca.desk_inbox`, and `run --desk landing` requires `landing.release_owner`,
a cci `--to` address such as `peer:<session>`. A quiet pass writes nothing.
The orca runner rotates every *.md inbox beside `orca.desk_inbox` once an hour,
using the default six-hour window.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import actions
import cci
import holds
import inboxes
import prompt_screen
from incident import CLAUDE_FAST_MODE, CLOCK_FALLBACK, latest

SCRIPTS = Path(__file__).resolve().parent
PACIFIC = ZoneInfo("America/Los_Angeles")
LANE_PREFIX = "desk-lane-"
LANDING = "desk-landing"
RUNNER = "desk-runner"
ORCA_DESK = "orca-desk"
LAUNCH_STATE = Path(".claude/scratch/orca-launch")
CCI_TEXT = 400
ESCALATION_KINDS = {"DECIDE": "decide", "INCIDENT": "incident", "INCIDENT-OWNED": "state", "UNBOUND": "blocker", "UNOWNED": "blocker", "UNACKED": "blocker", "INCIDENT-CLOCK": "blocker", "HOLDS-UNREAD": "blocker", "LOAD-HIGH": "blocker", "GATES-REDISPATCH": "state"}
UNLAUNCHED = "unlaunched"
BINDING = "binding"
STACK_ENQUEUE = ".agents/skills/submit-pr/scripts/stack-enqueue"
TRUNK = "dev"
ENQUEUE_OUTCOMES = {0: "enqueued", 1: "blocked", 2: "unsettled", 3: "stranded"}
ENQUEUE_FAILED = 5
DEQUEUE_RELEASED = (0, 1)
TRANSIENT_FAILURE = re.compile(
    r"handshake operation timed out|urlopen error|Connection reset|reset by peer|Remote end closed|Temporary failure in name resolution|nodename nor servname|Name or service not known|HTTP(?: Error)? 5\d\d"
)
TRANSIENT_ATTEMPTS = 3
TRANSIENT_PAUSE_SECONDS = 20
INACTIVE = frozenset({"completed", "failed"})
RECLAIM_NAMED = 10
MODELS = re.compile(r"opus|sonnet|fable|astra|codex|sol|incident|claude-[\w.-]+|gpt-[\w.-]+")
INCIDENT_MODEL = "incident"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
QUEUED = frozenset({"QUEUED_TO_MERGE", "WAITING_TO_MERGE", "REBASING", "MERGED"})
RETRYABLE = frozenset({"blocked", "superseded", "foreign", "dropped", "refused", "dequeued"})
BELIEVED_QUEUED = frozenset({"enqueued", "noop"})
ADMITTED = BELIEVED_QUEUED | {"dropped", "ejected"}
OUT_OF_QUEUE = frozenset({"not queued", "evicted"})
OWNER_KINDS = ("head", "opened", "ready", "claim")
FIDELITY_OK = "fidelity: ok"
FIDELITY_GRADES = (FIDELITY_OK, "fidelity: missing")
FIDELITY_STATUS = "fidelity"
DIFF_MEDIA = "Accept: application/vnd.github.diff"
HOLD_LABEL = "hold"
LABEL_SEEN = "hold-label-seen"
LABEL_GONE = "(HTTP 404)"
STATUS_TEXT = 140
SHORT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
MERGE_ACTIVITY = "### Merge activity"
DROP_BLOCKER = re.compile(r"merge conflicts?")
DOWNSTACK_FAILURE = re.compile(r"downstack failures on PR #\[?(\d+)")
MERGE_FAST = "merge-fast"
RELEASE_WITH = re.compile(r"^Release-With:[ \t]*(\S.*)$", re.MULTILINE)
RELEASE_SINCE = "release-since"
POSTED = re.compile(r"#(\d+)")
SWEEP_EVERY = timedelta(minutes=5)
PASS_SECONDS = 10
COMMAND_TIMEOUT_SECONDS = 1800
TIMED_OUT = 124
WAKE_SECONDS = 0.25
STOP_CONFIRM = timedelta(seconds=30)
STOP_POLL_SECONDS = 2
SHOW_WORKERS = 8
GATE_WORKERS = 4
MIRROR_WRITES = 3
GATES_STATUS = "landing-gates"
GATE_STATUSES = frozenset({GATES_STATUS, FIDELITY_STATUS, "release-pr-check", "emergency-head"})
RED_STATES = frozenset({"failure", "error"})
FAILING_REQUIRED = re.compile(r"^graphite FAILING\w*$")
GATES_WORKFLOW = "landing-gates.yaml"
GATES_GRACE = timedelta(minutes=5)
HEAD_FLOOR = timedelta(seconds=60)
HEADS_PER_QUERY = 100
QUOTA_FLOOR = 0.05
QUOTA_RESOURCES = ("core", "graphql")
QUEUE_DRAFT_TITLE = "[Graphite MQ] Draft PR"
ROTATE_EVERY = timedelta(hours=1)
HEARTBEAT = "orca-runner.heartbeat.json"
ORPHANED_SEND = timedelta(minutes=2)
RESEND_AFTER = timedelta(seconds=30)
RESEND_LIMIT = 2
RESEND = ":resend"
RETRY = ":retry"
SEND_ATTEMPTS = 3
INBOX_PAGE_SIZE = 100
UNPARSEABLE = "unparseable"
UNREAD = "unread"
UNREACHABLE = frozenset({"runtime_unavailable", "remote_runtime_unavailable", "runtime_timeout", "invalid_runtime_response", UNPARSEABLE})
ANSWER_CONFLICT = "answer_conflict"
UNREACHABLE_FACT = "orca-unreachable"
UNREACHABLE_WAIT = timedelta(minutes=2)
HANDOFF_WAIT = timedelta(minutes=5)
VERDICT_LINE = re.compile(r"^#(?P<pr>\d+) (?P<verdict>[A-Z]+) (?P<sha>[0-9a-f]{0,10}) ?(?P<detail>.*)$", re.MULTILINE)
UNNAMED_RIDER = "rides the batch through"
ROUTED_BLOCKER = re.compile(r"^(?:graphite (?:FAILING\w*|MERGE_CONFLICTS)|(?:rules-review|fidelity) (?:failure|error):|conflicts with origin/|GitHub reads CONFLICTING|carries .* already squashed on|changes requested by |ejected from the merge queue)")
BLOCKER_HINT = re.compile(r"^(?:run ccx vcs stack submit from|read the failure |.+, so the red check is its own$)")
WOULD_ENQUEUE = re.compile(r"^would enqueue ((?:#\d+ ?)+) in one call$", re.MULTILINE)
STATUS_LINE = re.compile(r"^#(?P<pr>\d+) (?P<status>[A-Z_]+) ", re.MULTILINE)
RELAY_REF = re.compile(r"(?:^|cci#)(R?\d+)$")
RUN_REF = re.compile(r"R\d+|\d+")
ACK = re.compile(r"^(?P<verb>started|done)\b[: ]*(?P<rest>.*)$", re.DOTALL)
LAUNCHED = re.compile(r"^(?P<lane>\S+) (?P<how>ready|unsupervised) task=\S+ dispatch=(?P<dispatch>\S+) terminal=(?P<terminal>\S+) worktree=\S+$", re.MULTILINE)
LANDING_POLICIES = ("prefix", "whole")
RELAY_GRAMMAR = "orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>"
LAUNCH_GRAMMAR = "orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>"
ALERT_GRAMMAR = "orca-desk: alert <slug> <link> :: <what fired>"
INCIDENT_GRAMMAR = "orca-desk: incident <slug> <link> reported=<HH:MM | H:MM AM/PM, Pacific> :: <what fired>"
HOLD_GRAMMAR = "orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>"
UNHOLD_GRAMMAR = "orca-desk: unhold <slug>"
WITHDRAW_GRAMMAR = "orca-desk: withdraw <R<n> | unfixed:<msg>:<n> | lane>"
INBOX_DIRECTIVE = re.compile(r"^(?:-\s+)?(?:(?P<key>R\d+)\s+(?:\([^)]*\)\s+)?)?orca-desk: (?P<verb>relay|launch|alert|incident|hold|unhold|withdraw)\b(?P<rest>.*)$", re.DOTALL)
RELAY_TO = re.compile(r"^ to (?P<lanes>[\w.-]+(?:(?:, (?:and )?| and )[\w.-]+)*):\s+(?P<text>\S.*)$", re.DOTALL)
LANE_LIST = re.compile(r", (?:and )?| and ")
LAUNCH_SPEC = re.compile(r"^ (?P<lane>[\w.-]+)(?P<now> NOW)? (?P<model>\S+) (?P<effort>\S+)(?: role=(?P<role>[\w-]+))? brief=(?P<brief>/\S+)$")
ALERT_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*) (?P<link>\S+) :: (?P<what>\S.*)$")
INCIDENT_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9_.-]*) (?P<link>\S+) reported=(?P<reported>(?:[01]?\d|2[0-3]):[0-5]\d|(?:1[0-2]|0?[1-9]):[0-5]\d [AP]M) :: (?P<what>\S.*)$")
INCIDENT_BAR = timedelta(minutes=20)
ALERT_TEMPLATE = SCRIPTS.parent / "reference" / "alert-fix-brief.md"
UNFIXED_TEMPLATE = SCRIPTS.parent / "reference" / "unfixed-case-brief.md"
UNFIXED = re.compile(r"^[ \t>*-]*(?P<tick>`?)UNFIXED (?P<lane>[\w.-]+):[ \t]*(?P<case>\S.*?)(?P=tick)[ \t]*$", re.MULTILINE)
UNFIXED_CASES = "unfixed-cases"
UNFIXED_KEY = "unfixed:"
UNKNOWN_CAUSE = re.compile(r"\bUNKNOWN\b")
HANDED_BACK = re.compile(r"^(?:[\w-]+:\s*)?(?:NOT-OURS|stood down|stopped)\b", re.IGNORECASE)
MONITOR_ID = re.compile(r"(?:monitors/|\bmonitor |\bDatadog )(\d{4,})")
INCIDENT_ANNOTATION = re.compile(r"^ccx:.*\bincident=([\w.-]+)", re.MULTILINE)
NAMES_NOTHING = "none"
ROLE = re.compile(r"^ccx:(?:.*[ \t])?role=([\w-]+)", re.MULTILINE)
FIX_ROLES = frozenset({"fix", INCIDENT_MODEL})
CLOCK = "incident-clock"
MILESTONE_STATUSES = "milestone-statuses"
MILESTONE_SUBJECTS = ("fix-live:", "mechanism:", "ready:")
CLOCK_MILESTONES = {"mechanism": "mechanism", "opened": "PR", "ready": "ready"}
CLOCK_ENDS = ("fix-live", "not-ours", "stopped", "stand-down")
STAND_DOWN = "stand-down"
INCIDENT_ENDS = ("not-ours", STAND_DOWN)
STOPPED = "stopped"
TEXT_MARK = re.compile(r"^(?P<mark>MECHANISM|READY)\b")
TEXT_MARK_KINDS = ("report", "incident")
FIX_ALIAS = "-fix"
CASE = "-case"
LINEAGE = re.compile(rf"(?:{re.escape(CLOCK_FALLBACK)}|{re.escape(CASE)}\d+)+$")
CLAUDE_ARGS = "ORCA_LAUNCH_CLAUDE_ARGS"
FALLBACK_BRIEF = """ccx: lane={backup} role=incident effort=xhigh

# {backup}: Opus fallback for {lane}

You are the Opus fallback for `{lane}`. The orca desk's incident clock started you
{minutes} minutes after `{lane}` launched, because `{lane}` had posted no {missing}.
Work the incident in the brief below in parallel with `{lane}`; the first PR wins.
Before you start, read what `{lane}` has posted with
`cci tail --drive {drive} --lane {lane} --since 2h`, so you repeat none of it.
Post on cci as `{backup}`, and address `{lane}` and `root` on every record.
Wherever the brief below tells its lane to post or report as `{lane}`, post as
`{backup}`.

The brief `{lane}` was launched on follows, unchanged.

{brief}"""
CCX_KEY = r"^ccx:(?:.*[ \t])?{}(?:\s|$)"
CPU_HIGH = re.compile(CCX_KEY.format("cpu=high"), re.MULTILINE)
PLACE = re.compile(CCX_KEY.format("place=(remote|local)"), re.MULTILINE)
STANDING_DESK = re.compile(CCX_KEY.format("role=(?:desk|watch)"), re.MULTILINE)
SPRITE_LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,54}")
SPRITE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SPRITE_IDS = {"task": "taskId", "dispatch": "dispatchId"}
SPRITE_EXITS = ("prepare.status", "attach.status")
SPRITE_MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "sol": "gpt-6.1-sol", "codex": "gpt-6.1-sol", "astra": "gpt-6-astra"}
SPRITE_LOCAL_MODELS = frozenset({"fable", INCIDENT_MODEL})
SPRITE_ATTACH = SCRIPTS.parents[2] / "bin" / "orca-remote-attach.sh"
LAUNCHER = SCRIPTS.parents[2] / "bin" / "orca-launch.sh"
HOLD_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*) owner=(?P<owner>[\w.-]+) :: (?P<what>\S.*)$")
UNHOLD_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*)$")
WITHDRAW_SPEC = re.compile(r"^ (?P<target>[\w.:-]+)$")
LAUNCH_KEY = re.compile(r"R\d+|unfixed:[\w-]+:\d+|inbox@\S+")
WITHDRAWN = "withdrawn"
FENCED = "dispatch_inactive"
DECIDE_LINE = re.compile(r"^DECIDE (?P<msg>msg_\S+) (?P<lane>[\w.-]+): ")
ROOT = "root"


@dataclass
class Done:
    code: int
    out: str
    err: str


class CciTailFailed(RuntimeError):
    pass


class OrcaUnreachable(RuntimeError):
    pass


class Shell:
    """The single side-effect boundary: every subprocess, the clock, and the load average pass through here.

    Input reaches a child from a file, never a pipe the runner writes while the child's output
    fills, and a child still running after COMMAND_TIMEOUT_SECONDS is killed and fails its pass.
    """

    def run(self, argv: list[str], stdin: str | None = None, env: dict[str, str] | None = None) -> Done:
        with tempfile.TemporaryFile("w+") as feed:
            feed.write(stdin or "")
            feed.seek(0)
            try:
                proc = subprocess.run(argv, stdin=feed, capture_output=True, text=True, env={**os.environ, **env} if env else None, timeout=COMMAND_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return Done(TIMED_OUT, "", f"{' '.join(argv)} timed out after {COMMAND_TIMEOUT_SECONDS}s and was killed")
        return Done(proc.returncode, proc.stdout, proc.stderr)

    def spawn(self, argv: list[str], out: Path, env: dict[str, str]) -> subprocess.Popen:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as sink:
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT, env={**os.environ, **env}, start_new_session=True)

    def stat(self, path: Path) -> tuple[int, int] | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_size, info.st_mtime_ns

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def load(self) -> float:
        return os.getloadavg()[0]

    def cores(self) -> int:
        return os.cpu_count() or 1

    def env(self, name: str) -> str:
        return os.environ.get(name, "")


def brief_incidents(brief: Path) -> set[str]:
    return set(INCIDENT_ANNOTATION.findall(brief.read_text())) if brief.is_file() else set()


def incident_fix(model: str, brief: Path, role: str) -> bool:
    """Whether a launch is an incident fix lane's: the incident alias, or a brief whose `ccx:` line says `role=incident` or names an `incident=`, and in either case a launch and brief naming no role but fix or incident."""
    roles = (set(ROLE.findall(brief.read_text())) if brief.is_file() else set()) | ({role} if role else set())
    return roles <= FIX_ROLES and (model == INCIDENT_MODEL or INCIDENT_MODEL in roles or bool(brief_incidents(brief) - {NAMES_NOTHING}))


def fix_launch(incident: actions.Incident) -> actions.Action | None:
    """A lane container's newest verified launch, when it is an incident fix lane's."""
    launch = next((action for action in reversed(incident.actions.values()) if action.kind == "launch" and action.status == "verified"), None)
    if not launch:
        return None
    spec = json.loads(launch.target)
    return launch if incident_fix(spec["model"], Path(spec["brief"]), spec.get("role", "")) else None


def first_lane(lane: str) -> str:
    """The lane an incident's fallback and case lanes descend from: the name without its trailing `-opus` and `-case<n>` parts."""
    return LINEAGE.sub("", lane)


def is_case_lane(lane: str) -> bool:
    return CASE in lane.removeprefix(first_lane(lane))


def unfixed_cases(text: str, lane: str) -> dict[str, str]:
    """The cases this lane's own `UNFIXED <lane>: <case>` lines name, in order and as first written, keyed by the text with its whitespace runs collapsed; `UNFIXED <lane>: none` names no case."""
    cases: dict[str, str] = {}
    for match in UNFIXED.finditer(text):
        if match["lane"] == lane and match["case"].rstrip(".").lower() != NAMES_NOTHING:
            cases.setdefault(" ".join(match["case"].split()), match["case"])
    return cases


def handed_back(message: dict) -> bool:
    """Whether a lane's report opens, in its subject or the first line of its body, with NOT-OURS, `stood down`, or `Stopped`: the lane gave its incident back, so the cases it lists are no case lane's to launch."""
    return any(HANDED_BACK.match(line.strip()) for line in (message.get("subject") or "", (message.get("body") or "").strip().partition("\n")[0]))


def clock_mark(record: dict) -> str:
    """The incident clock milestone a lane's cci record marks, or empty: its kind, or `mechanism` or `ready` for a `report` or `incident` whose text starts MECHANISM or READY."""
    if record["kind"] in TEXT_MARK_KINDS:
        return mark["mark"].lower() if (mark := TEXT_MARK.match(record["text"])) else ""
    return record["kind"] if record["kind"] in (*CLOCK_MILESTONES, *CLOCK_ENDS) else ""


def lane_aliases(lane: str) -> tuple[str, str]:
    """The names one incident lane posts under: the desk launches `<name>-fix`, and its lane may post as `<name>`."""
    base = lane.removesuffix(FIX_ALIAS)
    return base, f"{base}{FIX_ALIAS}"


def whole_minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def ccn_failure(done: Done) -> str:
    return (done.err or done.out).strip()[-300:] or f"ccn exited {done.code}"


def clock_phases(launched: datetime, reached: dict[str, str], now: datetime) -> str:
    """How long each phase of an incident lane took, from its milestones' own stamps: `launch to mechanism 7 min, mechanism to PR 6 min, PR to now 7 min`."""
    marks = sorted((actions.parse_stamp(at), CLOCK_MILESTONES[name]) for name, at in reached.items() if name in CLOCK_MILESTONES)
    points = [(launched, "launch"), *marks, (now, "now")]
    return ", ".join(f"{start} to {end} {whole_minutes(at, until)} min" for (at, start), (until, end) in itertools.pairwise(points))


def sprite_placed(brief: Path) -> bool:
    if not brief.is_file():
        return False
    text = brief.read_text()
    if STANDING_DESK.search(text):
        return False
    placed = PLACE.search(text)
    return placed[1] == "remote" if placed else bool(CPU_HIGH.search(text))


def sprite_model(model: str) -> str:
    return SPRITE_MODELS.get(model, model)


def sprite_attempt(brief: Path) -> Path:
    return brief.parent / f"{brief.name.rsplit('.', 1)[0]}.worker"


def exit_status(path: Path) -> str:
    return path.read_text().strip() if path.is_file() else ""


def sprite_never_started(attempt: Path, lane: str, exited: bool) -> bool:
    """Whether a Sprite attempt's receipts prove worker-start never ran: no `<lane>.json` was claimed, and prepare or attach exited nonzero or the launcher exited with no prepare exit recorded."""
    if (attempt / f"{lane}.json").exists():
        return False
    prepared, attached = (exit_status(attempt / step) for step in SPRITE_EXITS)
    return prepared not in ("", "0") or attached not in ("", "0") or (exited and not prepared)


def sprite_started_as(receipt: Path) -> str:
    """` as task=<id> dispatch=<id>` for the plain ids a worker-start receipt names, empty when it names none."""
    try:
        result = json.loads(receipt.read_text())["result"]
        named = {name: result[key] for name, key in SPRITE_IDS.items() if key in result}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return ""
    ids = [f"{name}={value}" for name, value in named.items() if isinstance(value, str) and SPRITE_ID.fullmatch(value)]
    return f" as {' '.join(ids)}" if ids else ""


def pacific(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%H:%M")


def lane_container(lane: str) -> str:
    return f"{LANE_PREFIX}{lane}"


@dataclass
class Config:
    source: Path
    store: Path | None
    drive: str
    view: Path
    desk_inbox: Path
    run: str
    receipts: Path
    briefs_repo: str
    briefs_log: str
    launch_env: dict[str, str]
    sprite: dict | None
    gc: str | None
    start_minutes: int
    launch_minutes: int
    load_hold_minutes: int
    enqueue_minutes: int
    hold_minutes: int
    release_start_minutes: int
    incident_mechanism_minutes: int
    incident_pr_minutes: int
    incident_ready_minutes: int
    case_lanes: int
    landing: dict | None
    alert_facts: Path | None

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text())
        orca = raw["orca"]
        deadlines = raw.get("deadlines", {})
        alert = raw.get("alert", {})
        return cls(
            source=path.expanduser().resolve(),
            store=Path(raw["store"]).expanduser() if raw.get("store") else None,
            drive=raw["drive"],
            view=Path(raw["view"]).expanduser(),
            desk_inbox=Path(orca["desk_inbox"]).expanduser(),
            run=orca["run"],
            receipts=Path.home() / LAUNCH_STATE / orca["run"],
            briefs_repo=str(Path(orca["briefs"]["repo"]).expanduser()),
            briefs_log=orca["briefs"].get("log", ""),
            launch_env=orca.get("launch_env", {}),
            sprite=orca.get("sprite"),
            gc=orca.get("gc"),
            start_minutes=deadlines.get("start_minutes", 10),
            launch_minutes=deadlines.get("launch_minutes", 15),
            load_hold_minutes=deadlines.get("load_hold_minutes", 5),
            enqueue_minutes=deadlines.get("enqueue_minutes", 15),
            hold_minutes=deadlines.get("hold_minutes", 15),
            release_start_minutes=deadlines.get("release_start_minutes", 10),
            incident_mechanism_minutes=deadlines.get("incident_mechanism_minutes", 10),
            incident_pr_minutes=deadlines.get("incident_pr_minutes", 15),
            incident_ready_minutes=deadlines.get("incident_ready_minutes", 20),
            case_lanes=raw.get("incident", {}).get("case_lanes", 2),
            landing=raw.get("landing"),
            alert_facts=Path(alert["facts"]).expanduser() if alert.get("facts") else None,
        )


class Book:
    """Generation-checked edits over the actions store; a change raising ValueError writes nothing."""

    def __init__(self, store: actions.Store, shell: Shell):
        self.store = store
        self.shell = shell

    def stamp(self) -> str:
        return actions.stamp(self.shell.now())

    def ensure(self, container: str, owner: str) -> actions.Incident:
        return self.store.create(actions.Incident(container, owner, 0, self.stamp(), {}))

    def load(self, container: str) -> actions.Incident:
        return self.store.load(container)

    def containers(self, prefix: str) -> list[str]:
        return [name for name in self.store.ids() if name.startswith(prefix)]

    def edit(self, container: str, change: Callable[[actions.Incident], object]) -> object:
        while True:
            generation = self.store.load(container).owner_generation
            try:
                with self.store.owned(container, generation) as incident:
                    return change(incident)
            except actions.StaleGeneration:
                continue

    def attempt(self, container: str, change: Callable[[actions.Incident], object]) -> bool:
        try:
            self.edit(container, change)
        except (ValueError, actions.UnknownAction):
            return False
        return True

    def as_owner(self, container: str, owner: str, change: Callable[[actions.Incident], object]) -> bool:
        """Apply `change` only while `owner` holds the container at the generation read; a transfer in between refuses it."""
        incident = self.store.load(container)
        if incident.owner != owner:
            return False
        try:
            with self.store.owned(container, incident.owner_generation) as live:
                change(live)
        except (actions.StaleGeneration, ValueError, actions.UnknownAction):
            return False
        return True

    def known(self, container: str, action_id: str) -> actions.Action | None:
        return self.store.load(container).actions.get(action_id) if self.store.path(container).is_file() else None

    def accept(self, container: str, action_id: str, kind: str, target: str, authority: str, deadline: datetime | None) -> tuple[actions.Action, bool]:
        if known := self.known(container, action_id):
            return known, False

        def change(incident: actions.Incident) -> tuple[actions.Action, bool]:
            created = action_id not in incident.actions
            action = incident.accept(action_id, kind, target, authority, self.shell.now())
            if created and deadline:
                action.deadline = actions.stamp(deadline)
            return action, created

        return self.edit(container, change)

    def actions(self, container: str, kind: str | None = None, status: str | None = None) -> list[actions.Action]:
        return [action for action in self.store.load(container).actions.values() if (kind is None or action.kind == kind) and (status is None or action.status == status)]


@dataclass
class Dispatch:
    lane: str
    id: str
    terminal: str
    status: str
    live: bool
    fenced: bool = False


class Orca:
    def __init__(self, shell: Shell, config: Config):
        self.shell = shell
        self.config = config
        self.sender: dict[str, str] = {}

    def call(self, *argv: str) -> dict:
        done = self.shell.run(["orca", *argv, "--json"], env=self.sender or None)
        try:
            return json.loads(done.out)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": UNPARSEABLE, "message": (done.out or done.err).strip()[:300]}}

    def ask(self, *argv: str) -> dict:
        """`call`, raising OrcaUnreachable when Orca gave no answer: a runtime or connection error, a timeout, or output that is not its JSON."""
        reply = self.call(*argv)
        if not reply.get("ok") and (error := reply["error"])["code"] in UNREACHABLE:
            raise OrcaUnreachable(": ".join(filter(None, [error["code"], error.get("message")])))
        return reply

    def receipt(self, lane: str) -> str:
        """The lane's current dispatch, or empty while no launch has written a whole receipt."""
        path = self.config.receipts / f"{lane}.json"
        try:
            return json.loads(path.read_text())["result"]["dispatchId"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return ""

    def terminal(self, lane: str) -> str:
        path = self.config.receipts / f"{lane}.terminal"
        return path.read_text().strip() if path.is_file() else ""

    def lanes(self) -> list[str]:
        return sorted(path.stem for path in self.config.receipts.glob("*.terminal"))

    def lane_of(self, handle: str) -> str:
        return next((lane for lane in self.lanes() if handle in (self.terminal(lane), f"dispatch:{self.receipt(lane)}")), handle)

    def show(self, lane: str, dispatch: str | None = None) -> Dispatch | None:
        """The lane's dispatch as Orca shows it, or None when the lane has no receipt or Orca answers that the dispatch is gone; raises OrcaUnreachable when Orca gives no answer."""
        dispatch = dispatch or self.receipt(lane)
        shown = self.ask("orchestration", "worker-show", "--dispatch", dispatch) if dispatch else {}
        if not shown.get("ok"):
            return None
        result = shown["result"]
        observation = result.get("observation") or {}
        return Dispatch(
            lane=lane,
            id=dispatch,
            terminal=(result.get("terminal") or {}).get("handle") or self.terminal(lane),
            status=result["dispatch"]["status"],
            live=observation.get("status") == "live",
            fenced=bool(result["dispatch"].get("capabilityRevokedAt")),
        )

    def stop(self, dispatch: str) -> tuple[str, str]:
        """Fence the dispatch as `orca-gc --done` does: worker-stop, or worker-abandon once Orca already fenced it; its state, or empty and the error code."""
        stopped = self.call("orchestration", "worker-stop", "--dispatch", dispatch)
        if not stopped.get("ok") and stopped["error"]["code"] == FENCED:
            stopped = self.call("orchestration", "worker-abandon", "--dispatch", dispatch)
        if not stopped.get("ok"):
            return "", stopped["error"]["code"]
        return stopped["result"]["state"], ""

    def mailbox(self, terminal: str) -> list[dict] | None:
        listed = self.call("orchestration", "check", "--terminal", terminal, "--all")
        return (listed.get("result") or {}).get("messages") or [] if listed.get("ok") else None

    def request_state(self, request: str) -> str:
        shown = self.call("orchestration", "request-show", "--request", request)
        return (shown.get("result") or {}).get("state", "absent") if shown.get("ok") else UNREAD

    def at_rest(self, terminal: str) -> bool:
        screen = self.call("terminal", "read", "--terminal", terminal, "--screen")
        return bool(screen.get("ok") and prompt_screen.input_state(screen["result"]["terminal"]) == prompt_screen.IDLE)

    def wake(self, terminal: str, text: str) -> bool:
        screen = self.call("terminal", "read", "--terminal", terminal, "--screen")
        if not (screen.get("ok") and prompt_screen.idle_prompt(screen["result"]["terminal"])):
            return False
        self.shell.run(["orca", "terminal", "send", "--terminal", terminal, "--text", text, "--enter", "--json"])
        return True


def clipped_by_cci(text: str) -> bool:
    return len(text) == CCI_TEXT and text.endswith("…")


def kept_by_cci(text: str) -> str:
    """What `cci post` keeps in the record of a text it writes to a file: the first non-blank line, clipped to cci's text limit."""
    return inboxes.clip(next((line.strip() for line in text.split("\n") if line.strip()), ""), CCI_TEXT)


def request_id(reply: dict) -> str:
    return ((reply.get("error") or {}).get("data") or {}).get("orchestrationRequestId", "")


def thread_of(container: str, key: str) -> str:
    return f"{container}/{key}"


def delivers(message: dict, container: str, action: actions.Action) -> bool:
    if action.kind == "reply":
        return message.get("thread_id") in (json.loads(action.target)["reply_to"], thread_of(container, action.action_id)) and (message.get("body") or "").rstrip().endswith(f"({action.authority_ref})")
    return message.get("thread_id") == thread_of(container, action.action_id)


def resends(tries: list[actions.Action]) -> list[actions.Action]:
    return [attempt for attempt in tries if RESEND in attempt.action_id]


class Runner:
    def __init__(self, shell: Shell, config: Config, store: actions.Store):
        self.shell = shell
        self.config = config
        self.book = Book(store, shell)
        self.orca = Orca(shell, config)
        self.launching: dict[str, subprocess.Popen] = {}
        self.sprites: set[str] = set()
        self.pass_binding: dict | None = None
        self.unreachable_passes = 0
        self.book.ensure(RUNNER, RUNNER)

    def now(self) -> datetime:
        return self.shell.now()

    def escalate(self, key: str, kind: str, about: str, text: str) -> None:
        """Record one escalation per key; `flush` posts it to root through cci with the key as its topic."""
        self.record(f"escalation:{key}", f"{kind} {key} {about}: {' '.join(text.split())}")

    def record(self, action_id: str, line: str) -> None:
        self.book.accept(RUNNER, action_id, "escalation", line, "runner", None)

    def orca_down(self, down: OrcaUnreachable) -> None:
        """Count a pass Orca gave no answer to, and log `ORCA-UNREACHABLE` once for the streak it starts or continues."""
        self.unreachable_passes += 1
        streak = self.book.edit(RUNNER, lambda incident: dict(incident.facts.setdefault(UNREACHABLE_FACT, {"since": self.book.stamp(), "error": str(down)})))
        since = pacific(actions.parse_stamp(streak["since"]))
        self.escalate(f"orca:{streak['since']}", "ORCA-UNREACHABLE", "runner", f"{streak['error']}; since {since} the runner reads no dispatch as gone and fails no relay, and retries each pass with backoff up to {int(UNREACHABLE_WAIT.total_seconds())}s; ORCA-BACK follows when Orca answers")

    def orca_back(self) -> None:
        """End an unreachable streak with one `ORCA-BACK` once a pass Orca answered completes."""
        self.unreachable_passes = 0
        if UNREACHABLE_FACT not in self.book.load(RUNNER).facts:
            return
        streak = self.book.edit(RUNNER, lambda incident: incident.facts.pop(UNREACHABLE_FACT, None))
        if streak:
            since = actions.parse_stamp(streak["since"])
            self.escalate(f"orca-back:{streak['since']}", "ORCA-BACK", "runner", f"Orca answers again; it gave no answer from {pacific(since)} for {whole_minutes(since, self.now())} min ({streak['error']})")

    def pass_wait(self) -> float:
        return min(PASS_SECONDS * 2**self.unreachable_passes, UNREACHABLE_WAIT.total_seconds())

    def cci_post(self, kind: str, text: str, to: str, topic: str, key: str, message: str = "", pr: str = "", record_id: str = "") -> Done:
        """Post one cci record; a body over cci's text limit, or the whole Orca message it relays, goes to a receipts file named by `key` and rides as its path. With `record_id`, cci stores the record once and a repost returns the stored one."""
        argv = ["cci", "post", "--drive", self.config.drive, "--lane", RUNNER, "--kind", kind, "--to", to, "--topic", topic]
        if pr:
            argv += ["--pr", pr]
        if record_id:
            argv += ["--id", record_id]
        if len(text) > CCI_TEXT or message:
            body = self.config.receipts / "cci" / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.txt"
            body.parent.mkdir(parents=True, exist_ok=True)
            body.write_text("\n\n".join(filter(None, [text, message.strip()])) + "\n")
            argv += ["--path", str(body)]
        if len(text) > CCI_TEXT:
            text = text[: CCI_TEXT - 1] + "…"
        return self.shell.run([*argv, "--text", text])

    def flush(self) -> bool:
        failures = []
        for action in self.book.actions(RUNNER, kind="escalation", status="accepted"):
            label, topic = action.target.split()[:2]
            kind = ESCALATION_KINDS.get(label, "defect" if label.endswith("-FAILED") else "report")
            message = self.message_file(topic)
            done = self.cci_post(kind, action.target, "root", topic, action.action_id, message.read_text() if message.is_file() else "")
            if done.code != 0:
                failures.append(f"cci post exited {done.code}: {(done.err or done.out).strip()[:300]}")
                continue
            self.book.attempt(RUNNER, lambda incident, key=action.action_id: (incident.start(key, self.now()), incident.complete(key, {"at": self.book.stamp(), "posted": done.out.strip()})))
        if failures:
            print(f"{len(failures)} escalation(s) left for the next pass; {failures[0]}", file=sys.stderr)
        return not failures

    def beat(self) -> None:
        drive = self.config.desk_inbox.parent
        restart = f"nohup desk-runner.py run --config {self.config.source} --desk orca > {drive.parent / 'orca-runner.log'} 2>&1 < /dev/null &"
        beat = {"pid": os.getpid(), "at": self.now().isoformat(), "config": str(self.config.source), "terminal": self.shell.env("ORCA_TERMINAL_HANDLE"), "restart": restart}
        drive.mkdir(parents=True, exist_ok=True)
        staged = drive / f".{HEARTBEAT}"
        staged.write_text(json.dumps(beat) + "\n")
        staged.replace(drive / HEARTBEAT)

    def stop_marker(self) -> Path:
        return self.config.source.with_name(f"{self.config.source.name}.stop")

    def stop_requested(self) -> bool:
        marker = self.stop_marker()
        return marker.is_file() and marker.read_text().strip() == str(os.getpid())

    def handoff(self) -> int:
        beat = self.config.desk_inbox.parent / HEARTBEAT
        if not beat.is_file():
            print("desk-runner handoff: no orca runner has beaten here")
            return 0
        pid = json.loads(beat.read_text())["pid"]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"desk-runner handoff: runner {pid} is gone; start the next one")
            return 0
        marker = self.stop_marker()
        marker.write_text(f"{pid}\n")
        deadline = self.now() + HANDOFF_WAIT
        while marker.exists():
            if self.now() >= deadline:
                print(f"desk-runner handoff: runner {pid} wrote no exit mark within {HANDOFF_WAIT}; {marker} stays until it does", file=sys.stderr)
                return 1
            self.shell.sleep(WAKE_SECONDS)
        print(f"desk-runner handoff: runner {pid} exited; start the next one")
        return 0

    def binding(self, via: str = "") -> dict:
        """Ask Orca whether the Run's coordinator is a live terminal that coordinates it, and record the answer, with how it was obtained, in the runner's state."""
        prior = self.book.load(RUNNER).facts.get(BINDING) or {}
        shown, held = self.follow_coordinator()
        terminal = held.get("coordinator_handle") or ""
        current = self.orca.call("orchestration", "run-current") if terminal else {}
        coordinates = ((current.get("result") or {}).get("run") or {}).get("id") or ""
        state = {
            "terminal": terminal,
            "run": self.config.run,
            "bound": bool(terminal) and coordinates == self.config.run,
            "generation": held.get("consumer_generation"),
            "at": self.book.stamp(),
        }
        if not shown.get("ok"):
            error = shown.get("error") or {}
            state["why"] = f"orca orchestration run-show failed: {error.get('code')}: {error.get('message')}"
        elif not terminal:
            state["why"] = f"Orca binds {self.config.run} to no terminal"
        elif not current.get("ok"):
            error = current.get("error") or {}
            state["why"] = f"orca orchestration run-current as coordinator {terminal} failed: {error.get('code')}: {error.get('message')}"
        elif not state["bound"]:
            state["why"] = f"coordinator {terminal} coordinates {coordinates or 'no Run'}, not {self.config.run}"
        if via:
            state["via"] = via
        elif state["bound"] and prior.get("bound") and (prior.get("terminal"), prior.get("generation")) == (terminal, state["generation"]):
            state["via"] = prior.get("via", "")
        elif state["bound"]:
            state["via"] = f"coordinator read from Orca at generation {state['generation']}"
        self.book.edit(RUNNER, lambda incident: incident.facts.update({BINDING: state}))
        return state

    def follow_coordinator(self) -> tuple[dict, dict]:
        """Orca resolves the sender from the pane key before the handle, so the pane key is cleared for the coordinator's handle to count."""
        self.orca.sender = {}
        shown = self.orca.call("orchestration", "run-show", "--id", self.config.run)
        held = ((shown.get("result") or {}).get("run")) or {}
        if coordinator := held.get("coordinator_handle"):
            self.orca.sender = {"ORCA_TERMINAL_HANDLE": coordinator, "ORCA_PANE_KEY": ""}
        return shown, held

    def rebind_command(self) -> str:
        return f"desk-runner.py rebind --config {self.config.source}"

    def unbound(self, state: dict) -> str:
        return f"desk-runner cannot start workers on {self.config.run}: {state['why']}. From the coordinator's Orca terminal run `{self.rebind_command()}`; the runner follows the new coordinator on its next pass, so it needs no restart"

    def binding_line(self, state: dict | None) -> str:
        if not state:
            return f"binding: unchecked; `{self.rebind_command()}` binds this terminal"
        checked = pacific(actions.parse_stamp(state["at"]))
        if not state["bound"]:
            return f"binding: UNBOUND {state['why']}; checked {checked}; fix: `{self.rebind_command()}` from the coordinator's terminal"
        return f"binding: terminal {state['terminal']} coordinates {state['run']} at generation {state['generation']}, via {state['via']}; checked {checked}"

    def bound_for_launch(self) -> bool:
        """Check the binding once per delivery pass that launches; an unbound runner holds its launches and escalates once per binding state."""
        if self.pass_binding is None:
            self.pass_binding = self.binding()
            state = self.pass_binding
            if not state["bound"]:
                self.escalate(f"unbound:{state['terminal']}:{state['generation']}", "UNBOUND", "runner", f"{self.unbound(state)}; launches wait until it is bound")
        return self.pass_binding["bound"]

    def rebind(self) -> int:
        if not self.shell.env("ORCA_TERMINAL_HANDLE"):
            print(f"desk-runner rebind: no ORCA_TERMINAL_HANDLE; run `{self.rebind_command()}` from the coordinator's Orca terminal", file=sys.stderr)
            return 3
        previous = ((self.orca.call("orchestration", "run-show", "--id", self.config.run).get("result") or {}).get("run")) or {}
        used = self.orca.call("orchestration", "run-use", "--id", self.config.run)
        if not used.get("ok"):
            error = used.get("error") or {}
            print(f"desk-runner rebind: orca orchestration run-use failed: {error.get('code')}: {error.get('message')}", file=sys.stderr)
            return 1
        state = self.binding(via=f"orca orchestration run-use by desk-runner rebind at {pacific(self.now())}, replacing {previous.get('coordinator_handle') or 'no terminal'} at generation {previous.get('consumer_generation')}")
        print(self.binding_line(state))
        return 0 if state["bound"] else 1

    def lane(self, lane: str) -> str:
        container = lane_container(lane)
        self.book.ensure(container, self.orca.receipt(lane) or UNLAUNCHED)
        return container

    def accept_relay(self, key: str, lane: str, text: str, reply_to: str, minutes: int) -> tuple[actions.Action, bool]:
        target = json.dumps({"text": text, "reply_to": reply_to})
        return self.book.accept(self.lane(lane), key, "reply" if reply_to else "relay", target, key, self.now() + timedelta(minutes=minutes))

    def read_inbox(self) -> None:
        """Act on each complete line appended to the desk inbox since the saved byte offset; a first read starts at the end, so history never replays."""
        path = self.config.desk_inbox
        fact = f"desk-inbox:{path}"
        cursor = self.book.load(RUNNER).facts.get(fact)
        if cursor is None:
            start = inboxes.Inbox(path).end() if path.is_file() else 0
            self.book.edit(RUNNER, lambda incident: incident.facts.update({fact: start}))
            return
        if not path.is_file():
            return
        for line in inboxes.Inbox(path).lines(cursor):
            self.inbox_line(str(line.start), line.text.strip())
            self.book.edit(RUNNER, lambda incident, at=line.end: incident.facts.update({fact: at}))

    def read_cci(self) -> None:
        """Act in sequence order on each cci record past the saved sequence that is addressed to the orca desk, as one desk inbox line, or that this drive's root posted with `--re`, as a reply; a peer drive's root reply relays nothing. A reply to the runner's `DECIDE` on a worker question answers that question, a reply to a lane's own record answers that lane's open question, which the cci reply superseded, and a `withdraw` of a launch record withdraws that launch. A first read starts at the newest record, and records imported from the desk inbox were already read there."""
        fact = f"cci:{self.config.drive}:{ORCA_DESK}"
        cursor = self.book.load(RUNNER).facts.get(fact)
        try:
            if cursor is None:
                newest = cci.records(self.cci_tail, self.config.drive, "--limit", "1")
                self.book.edit(RUNNER, lambda incident: incident.facts.update({fact: max((record["seq"] for record in newest), default=0)}))
                return
            addressed = cci.records(self.cci_tail, self.config.drive, "--to", ORCA_DESK, since=cursor)
            replies = [record for record in cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, since=cursor) if record.get("re")]
            already_read = f"import:{self.config.desk_inbox.resolve()}"
            for record in sorted({record["seq"]: record for record in [*addressed, *replies]}.values(), key=lambda record: record["seq"]):
                if record.get("re"):
                    if record["lane"] == ROOT and record["drive"] == self.config.drive:
                        self.root_reply(record, self.cci_record(record["re"]))
                elif record["source"] != already_read:
                    self.cci_line(record)
                self.book.edit(RUNNER, lambda incident, at=record["seq"]: incident.facts.update({fact: at}))
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def tail_failed(self, failure: CciTailFailed) -> None:
        self.escalate(f"cci-tail:{actions.stamp(self.now())[:15]}", "CCI-TAIL-FAILED", "runner", str(failure))

    def cci_record(self, seq: int) -> dict | None:
        out = self.cci_tail(["cci", "tail", "--drive", self.config.drive, "--since", str(seq - 1), "--json", "--budget", str(cci.PAGE_BYTES)])
        return next((record for line in out.splitlines() if line.strip() and (record := json.loads(line))["seq"] == seq), None)

    def root_reply(self, record: dict, closed: dict | None) -> None:
        at = f"cci#{record['seq']}"
        if not closed:
            return
        if record["kind"] == "withdraw":
            directive = INBOX_DIRECTIVE.match(closed["text"].strip())
            if ORCA_DESK in (closed.get("to") or []) and directive and directive["verb"] == "launch":
                self.withdraw(at, at, directive["key"] or f"inbox@cci#{closed['seq']}")
            return
        if closed["lane"] == RUNNER and (decide := DECIDE_LINE.match(closed["text"])):
            if text := self.whole_text(at, at, decide["lane"], record):
                self.relay_to(at, at, decide["lane"], text, self.unclaimed(at, decide["lane"], [decide["msg"]]))
            return
        dispatch = self.orca.show(closed["lane"])
        if dispatch and dispatch.status not in INACTIVE and (questions := self.open_questions(dispatch)) and (text := self.whole_text(at, at, closed["lane"], record)):
            self.relay_to(at, at, closed["lane"], f"The root answered on cci #{record['seq']} (re #{closed['seq']}), which supersedes this question: {text}", self.unclaimed(at, closed["lane"], questions))

    def whole_text(self, at: str, key: str, about: str, record: dict) -> str:
        """The record's text in full: the file its `refs.path` names when cci clipped the record or kept that file's first line as the record's text, else the record's own text; empty, logging `RELAY-FAILED`, when that file cannot be read here."""
        kept, path = record["text"].strip(), (record.get("refs") or {}).get("path")
        if not path:
            return kept
        try:
            written = Path(path).read_text(errors="replace").strip()
        except OSError:
            self.record(f"escalation:relay:{at}", f"RELAY-FAILED {key} {about}: the record's full text at {path} is not readable here; nothing was relayed; post it again on the machine this runner runs on")
            return ""
        return written if clipped_by_cci(kept) or kept_by_cci(written) == kept else kept

    def cci_tail(self, argv: list[str]) -> str:
        done = self.shell.run(argv)
        if done.code != 0:
            raise CciTailFailed(f"cci tail exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return done.out

    def cci_line(self, record: dict) -> None:
        seq, line = record["seq"], record["text"].strip()
        directive = INBOX_DIRECTIVE.match(line)
        if directive and directive["verb"] == "relay" and not (line := self.whole_text(f"cci#{seq}", directive["key"] or f"inbox@cci#{seq}", "inbox", record)):
            return
        if not self.inbox_line(f"cci#{seq}", line):
            self.record(f"escalation:cci:{seq}", f"DIRECTIVE-FAILED cci#{seq} inbox: a record addressed to {ORCA_DESK} is one `orca-desk: <relay|launch|alert|incident|hold|unhold|withdraw> …` line, or a reply with --re; nothing was done")

    def inbox_line(self, at: str, line: str) -> bool:
        directive = INBOX_DIRECTIVE.match(line)
        if not directive:
            return False
        key = directive["key"] or f"inbox@{at}"
        verbs = {"relay": self.relay_line, "launch": self.launch_line, "alert": self.alert_line, "incident": self.incident_line, "hold": self.hold_line, "unhold": self.unhold_line, "withdraw": self.withdraw_line}
        verbs[directive["verb"]](at, key, directive["rest"])
        return True

    def relay_line(self, at: str, key: str, rest: str) -> None:
        parsed = RELAY_TO.match(rest)
        if not parsed or "; relay " in parsed["text"].partition("\n")[0]:
            self.record(f"escalation:relay:{at}", f"RELAY-FAILED {key} inbox: one relay per line, in the form `{RELAY_GRAMMAR}`; nothing was relayed")
            return
        for lane in LANE_LIST.split(parsed["lanes"]):
            self.relay_to(at, key, lane, parsed["text"])

    def relay_to(self, at: str, key: str, lane: str, text: str, question: str | None = None) -> None:
        """Accept what `relay` accepts for one lane: a reply to `question`, or to the current dispatch's latest open question no other reply claims when none is named, else a plain relay."""
        log = f"escalation:relay:{at}:{lane}"
        dispatch = self.orca.show(lane)
        if not dispatch or dispatch.status in INACTIVE:
            self.record(log, f"RELAY-FAILED {key} {lane}: no live dispatch{f' ({dispatch.id} is {dispatch.status})' if dispatch else ''}")
            return
        question = self.unclaimed(key, lane, self.open_questions(dispatch)) if question is None else question
        action, created = self.accept_relay(key, lane, text, question, self.config.start_minutes)
        if not created and json.loads(action.target)["text"] != text:
            self.record(log, f"RELAY-FAILED {key} {lane}: {key} already holds a different relay to {lane} ({action.status})")
            return
        self.record(log, f"RELAYED {key} {lane}: {f'reply to question {question} of' if question else 'relay to'} dispatch {dispatch.id}")

    def launch_line(self, at: str, key: str, rest: str) -> None:
        """Accept what `launch` accepts, `NOW` meaning `--owner-directed`, once per key and only for a lane with no live dispatch and no launch in flight."""
        log = f"escalation:launch:{at}"
        spec = LAUNCH_SPEC.match(rest)
        if not spec:
            self.record(log, f"LAUNCH-FAILED {key} inbox: one launch per line, in the form `{LAUNCH_GRAMMAR}`; nothing was launched")
            return
        lane, brief = spec["lane"], Path(spec["brief"]).resolve()
        if refusal := self.launch_refusal(key, lane, spec["model"], spec["effort"], brief):
            self.record(log, f"LAUNCH-FAILED {key} {lane}: {refusal}; nothing was launched")
            return
        self.accept_launch(key, lane, spec["model"], spec["effort"], str(brief), bool(spec["now"]), role=spec["role"] or "")

    def withdraw_line(self, at: str, key: str, rest: str) -> None:
        spec = WITHDRAW_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:withdraw:{at}", f"WITHDRAW-FAILED {key} inbox: one withdrawal per line, in the form `{WITHDRAW_GRAMMAR}`; nothing was withdrawn")
            return
        self.withdraw(at, key, spec["target"])

    def withdraw(self, at: str, key: str, target: str) -> None:
        """Withdraw the launch under a launch key, or every launch of a lane named: a launch not yet started fails, so it never launches, and a launched dispatch is stopped and its lane marked withdrawn."""
        log = f"escalation:withdraw:{at}"
        keyed = bool(LAUNCH_KEY.fullmatch(target))
        if keyed:
            held = [(container.removeprefix(LANE_PREFIX), [action]) for container in self.book.containers(LANE_PREFIX) if (action := self.book.load(container).actions.get(target)) and action.kind == "launch"]
        else:
            known = lane_container(target) in self.book.store.ids()
            held = [(target, self.book.actions(lane_container(target), kind="launch") if known else [])] if known or self.orca.receipt(target) else []
        if not held:
            self.record(log, f"WITHDRAW-FAILED {key} {target}: the runner holds no launch or lane under {target}; nothing was withdrawn")
        for lane, launches in held:
            for action in launches:
                if action.status == "accepted":
                    self.book.edit(self.lane(lane), lambda incident, action=action: incident.fail(action.action_id, f"withdrawn by {key}"))
                    self.record(f"{log}:{lane}:{action.action_id}", f"WITHDRAWN {action.action_id} {lane}: by {key}; nothing was launched")
                elif action.status == "started":
                    self.record(f"{log}:{lane}:{action.action_id}", f"WITHDRAW-FAILED {key} {lane}: launch {action.action_id} is still starting; withdraw it again once it logs LAUNCHED")
            if keyed:
                dispatch = launches[0].dispatch_id if launches[0].status == "verified" else None
            else:
                dispatch = self.orca.receipt(lane)
            if dispatch:
                self.stop_withdrawn(f"{log}:{lane}", key, target, lane, dispatch)
            elif keyed and launches[0].status not in ("accepted", "started"):
                self.record(f"{log}:{lane}", f"WITHDRAW-FAILED {key} {lane}: launch {target} is {launches[0].status}; nothing was withdrawn")

    def stop_withdrawn(self, log: str, key: str, target: str, lane: str, dispatch: str) -> None:
        """Mark the lane's dispatch withdrawn, so no report of it launches a case lane and Orca's offer to dispatch it again asks no one, then stop it unless it already settled."""
        self.book.edit(self.lane(lane), lambda incident: incident.facts.update({WITHDRAWN: {"dispatch": dispatch, "by": key}}))
        shown = self.orca.show(lane, dispatch)
        if shown and shown.status in INACTIVE:
            self.record(log, f"WITHDRAWN {target} {lane}: by {key}; dispatch {dispatch} was already {shown.status}, and no report of it launches a case lane")
            return
        state, error = self.orca.stop(dispatch)
        if not state:
            self.record(log, f"WITHDRAW-FAILED {key} {lane}: worker-stop of dispatch {dispatch} failed: {error}; it is marked withdrawn, so no report of it launches a case lane; stop it with orca-gc --done")
            return
        if unconfirmed := self.unconfirmed_stop(lane, dispatch):
            self.record(log, f"WITHDRAW-UNCONFIRMED {key} {lane}: worker-stop of dispatch {dispatch} answered {state}, but after {int(STOP_CONFIRM.total_seconds())}s Orca shows {unconfirmed}; it is marked withdrawn, so no report of it launches a case lane; stop it with orca-gc --done")
            return
        self.record(log, f"WITHDRAWN {target} {lane}: by {key}; dispatch {dispatch} stopped ({state}), and no report of it launches a case lane")

    def unconfirmed_stop(self, lane: str, dispatch: str) -> str:
        """Poll Orca until the stopped dispatch is settled and its terminal is gone or at an idle prompt; empty once it is, or what Orca last showed when STOP_CONFIRM ran out first."""
        deadline = self.now() + STOP_CONFIRM
        while True:
            shown = self.orca.show(lane, dispatch)
            if shown is None:
                seen = f"no dispatch {dispatch}, and terminal {self.orca.terminal(lane) or 'unknown'} unchecked"
            elif shown.status not in INACTIVE:
                seen = f"dispatch {dispatch} still {shown.status} on terminal {shown.terminal}"
            elif shown.live and not self.orca.at_rest(shown.terminal):
                seen = f"dispatch {dispatch} {shown.status}, but terminal {shown.terminal} still at work"
            else:
                return ""
            if self.now() >= deadline:
                return seen
            self.shell.sleep(STOP_POLL_SECONDS)

    def withdrawn_by(self, lane: str) -> str:
        """Who withdrew the lane's current dispatch, or empty."""
        container = lane_container(lane)
        mark = (self.book.load(container).facts.get(WITHDRAWN) or {}) if container in self.book.store.ids() else {}
        return mark.get("by", "") if mark and mark["dispatch"] == self.orca.receipt(lane) else ""

    def alert_line(self, at: str, key: str, rest: str) -> None:
        """Record a monitor transition and launch nothing, or relay a repeat to the fix lane already on it; the alerts desk decides whether an incident lane is necessary."""
        log = f"escalation:alert:{at}"
        spec = ALERT_SPEC.match(rest)
        if not spec:
            self.record(log, f"ALERT-FAILED {key} inbox: one alert per line, in the form `{ALERT_GRAMMAR}`; nothing was recorded")
            return
        slug, link, what = spec["slug"], spec["link"], spec["what"]
        lane = f"{slug}-fix"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            self.relay_to(at, f"{key}:again", lane, f"The alert fired again at {pacific(self.now())}: {what} {link}")
            return
        self.record(log, f"ALERT {slug} {pacific(self.now())}: {what} {link} | no lane launched; the alerts desk writes `{INCIDENT_GRAMMAR}` when it judges one necessary")

    def incident_line(self, at: str, key: str, rest: str) -> None:
        """Launch the alert's incident fix lane on a brief freshly attached to the briefs log from the template, or relay a repeat to the lane already on it; the root ratifies from the INCIDENT line."""
        log = f"escalation:incident:{at}"
        spec = INCIDENT_SPEC.match(rest)
        if not spec:
            self.record(log, f"INCIDENT-FAILED {key} inbox: one incident per line, in the form `{INCIDENT_GRAMMAR}`; nothing was launched")
            return
        slug, link, what = spec["slug"], spec["link"], spec["what"]
        lane = f"{slug}-fix"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            self.relay_to(at, f"{key}:again", lane, f"The alert fired again at {pacific(self.now())}: {what} {link}")
            return
        monitors = set(MONITOR_ID.findall(f"{link} {what}"))
        if monitors and (peer := self.incident_lane(monitors, lane)):
            self.record(log, f"LAUNCH-SKIPPED {key} {lane}: duplicate of {peer}, already on monitor {', '.join(sorted(monitors))}")
            return
        try:
            owner = self.incident_owner(slug, lane)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            owner = ""
        if owner:
            self.escalate(key, "INCIDENT-OWNED", lane, f"incident {slug} owned by {owner}; no launch")
            return
        if refusal := self.launch_refusal(key, lane, INCIDENT_MODEL, "xhigh", ALERT_TEMPLATE):
            self.record(log, f"INCIDENT {slug} again at {pacific(self.now())}: {what} {link} | {lane} not launched: {refusal}")
            return
        incident = "".join(f" incident={monitor}" for monitor in sorted(monitors)[:1])
        reported = datetime.strptime(spec["reported"], "%I:%M %p" if spec["reported"].endswith("M") else "%H:%M")
        brief, failure = self.attach_brief(
            lane,
            lambda briefs: ALERT_TEMPLATE.read_text().format(lane=lane, incident=incident, slug=slug, link=link, what=what, onset=pacific(self.now()), reported=f"{reported:%H:%M}", due=f"{reported + INCIDENT_BAR:%H:%M}", repo=self.config.briefs_repo, log=briefs, facts=self.drive_facts()),
            f"INCIDENT {slug}: fix brief for {lane}: {what} {link}",
        )
        if brief is None:
            self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | {lane} not launched: {failure}")
            return
        self.accept_launch(key, lane, INCIDENT_MODEL, "xhigh", str(brief), True, incident=slug)
        self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | fix lane {lane} launching on incident xhigh, brief {brief}; root: ratify, spawn the evidence and incident-doc lanes, fence the target, start comms in the affected account channels and #outage")

    def drive_facts(self) -> str:
        return self.config.alert_facts.read_text().strip() if self.config.alert_facts else "none recorded for this drive"

    def attach_brief(self, lane: str, render: Callable[[str], str], entry: str) -> tuple[Path | None, str]:
        """Attach the brief `render` writes for the resolved briefs log to that log as `<lane>.full.md`; its path, or why it has none."""
        if failure := self.resolve_briefs_log():
            return None, f"the briefs log did not resolve: {failure}"
        if failure := self.attach(f"{lane}.full.md", render(self.config.briefs_log), entry):
            return None, f"the brief did not attach: {failure}"
        brief = self.attachment(f"{lane}.full.md")
        return (brief, "") if brief else (None, "the attached brief has no path")

    def unfixed(self, lane: str, message: dict) -> None:
        """Launch an incident lane for each case an incident fix lane's `ready:` or `fix-live:` report names as `UNFIXED <lane>: <case>`, up to `incident.case_lanes` per incident, so a case its fix does not cover is never left as backlog. A case is known once its launch is taken, and known across the lane, its fallback, and its case lanes. Every other new case goes to the root as one `DECIDE` carrying a launch line per case: each past the cap, each from a case lane, since case lanes are one generation, and each from a lane whose report or newest mechanism says its cause is UNKNOWN. A report from a lane the root withdrew, or after its incident ended, launches nothing and goes to the root as one line."""
        cases = unfixed_cases(message.get("body") or "", lane)
        if not cases or handed_back(message) or lane_container(lane) not in self.book.store.ids():
            return
        launch = fix_launch(self.book.load(lane_container(lane)))
        if not launch:
            return
        first = first_lane(lane)
        container = self.lane(first)
        taken = self.book.load(container).facts.get(UNFIXED_CASES) or []
        fresh = [(index, known, case) for index, (known, case) in enumerate(cases.items(), 1) if known not in taken]
        if not fresh:
            return
        key = f"{UNFIXED_KEY}{message['id']}"
        unlaunched = f"UNFIXED {lane} {pacific(self.now())}: {'; '.join(case for _, _, case in fresh)} | no case lane launched"
        if by := self.withdrawn_by(lane):
            self.record(f"escalation:{key}", f"{unlaunched}: the root withdrew {lane} ({by})")
            return
        try:
            ended = self.incident_end(first, lane, actions.parse_stamp(message["created_at"]))
            unknown = "" if ended else self.unknown_cause(lane, launch, message)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            ended, unknown = "", "cci could not be read to tell whether the incident is open and its cause known"
        if ended:
            self.record(f"escalation:{key}", f"{unlaunched}: the incident ended with {ended}")
        elif is_case_lane(lane):
            self.offer_cases(key, first, lane, launch, [case for _, _, case in fresh], "a case lane reports new cases, past the one-generation cap")
        elif unknown:
            self.offer_cases(key, first, lane, launch, [case for _, _, case in fresh], f"{unknown}, so no case lane launches until the root ratifies it")
        else:
            room = max(self.config.case_lanes - len(taken), 0)
            for index, known, case in fresh[:room]:
                if self.case_lane(next(self.case_names(first)), lane, launch, case, f"{key}:{index}"):
                    taken = [*taken, known]
                    self.book.edit(container, lambda incident, taken=taken: incident.facts.update({UNFIXED_CASES: taken}))
            if fresh[room:]:
                self.offer_cases(key, first, lane, launch, [case for _, _, case in fresh[room:]], f"past the cap of {self.config.case_lanes} case lanes per incident")

    def case_names(self, first: str) -> Iterator[str]:
        """`<first>-case<n>` for each n no lane container holds yet, in order."""
        held = set(self.book.store.ids())
        return (name for n in itertools.count(1) if lane_container(name := f"{first}{CASE}{n}") not in held)

    def incident_end(self, first: str, lane: str, reported: datetime | None = None) -> str:
        """The record that ended the incident `first` leads, as `#<seq> <KIND> <lane>`: a `not-ours` or `stand-down` its fix lane or Opus fallback posted, the root's `stand-down` or `stopped` to either of them or to `lane`, or a `stopped` that `lane` posted before `reported`; empty while the incident is open."""
        owners = {*lane_aliases(first), *lane_aliases(f"{first}{CLOCK_FALLBACK}")}
        own = set(lane_aliases(lane))
        launch = fix_launch(self.book.load(lane_container(first)))
        since = actions.parse_stamp(launch.verification_receipt["at"]) if launch else datetime.min.replace(tzinfo=timezone.utc)
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in sorted(owners | own | {ROOT}) for arg in ("--lane", name)), *(f"--kind={kind}" for kind in (*INCIDENT_ENDS, STOPPED)))
        for record in posted:
            at = actions.parse_stamp(record["at"])
            if record["drive"] != self.config.drive or at < since:
                continue
            if (
                (record["lane"] in owners and record["kind"] in INCIDENT_ENDS)
                or (record["lane"] == ROOT and record["kind"] in (STAND_DOWN, STOPPED) and (owners | own) & set(record.get("to") or []))
                or (record["lane"] in own and record["kind"] == STOPPED and reported is not None and at < reported)
            ):
                return f"#{record['seq']} {record['kind'].upper()} {record['lane']}"
        return ""

    def incident_owner(self, slug: str, lane: str) -> str:
        """Who else holds the incident `slug`, as `<lane>@<drive> <kind> #<seq>`, or empty while this drive does: the root's `done` that resolves a record naming the slug, its `stopped` addressed to `lane`, or another drive's root `answer` addressed to `root@<this drive>` that names the slug."""
        named = {record["seq"] for record in cci.records(self.cci_tail, self.config.drive, "--kind=incident") if record["drive"] == self.config.drive and (record.get("topic") == slug or slug in record["text"])}
        names = set(lane_aliases(lane))
        posted = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, "--kind=done", "--kind=stopped", since=min(named, default=0))
        answered = cci.records(self.cci_tail, self.config.drive, "--reader", ROOT, "--kind=answer")
        claims = [
            *(record for record in posted if record["drive"] == self.config.drive and ((record["kind"] == "done" and record.get("resolves") in named) or (record["kind"] == STOPPED and names & set(record.get("to") or [])))),
            *(record for record in answered if record["drive"] != self.config.drive and record["lane"] == ROOT and record["kind"] == "answer" and f"{ROOT}@{self.config.drive}" in (record.get("to") or []) and slug in record["text"]),
        ]
        return next((f"{record['lane']}@{record['drive']} {record['kind']} #{record['seq']}" for record in claims), "")

    def unknown_cause(self, lane: str, launch: actions.Action, message: dict) -> str:
        """Which of the lane's records says its cause is UNKNOWN: the report itself, or its newest mechanism record or status since its launch; empty when none does."""
        if UNKNOWN_CAUSE.search(f"{message.get('subject') or ''}\n{message.get('body') or ''}"):
            return f"its report {message['id']} says the cause is UNKNOWN"
        launched = actions.parse_stamp(launch.verification_receipt["at"])
        marks = [(record["at"], f"#{record['seq']}", record["text"]) for record in self.lane_records(lane, "mechanism", *TEXT_MARK_KINDS) if clock_mark(record) == "mechanism"]
        statuses = self.book.load(lane_container(lane)).facts.get(MILESTONE_STATUSES) or {}
        marks += [(status["at"], f"its status {msg}", path.read_text()) for msg, status in statuses.items() if status["name"] == "mechanism" and (path := self.message_file(msg)).is_file()]
        newest = max((mark for mark in marks if actions.parse_stamp(mark[0]) >= launched), key=lambda mark: actions.parse_stamp(mark[0]), default=None)
        return f"its mechanism {newest[1]} says the cause is UNKNOWN" if newest and UNKNOWN_CAUSE.search(newest[2]) else ""

    def case_brief(self, lane: str, parent: str, launch: actions.Action, case: str) -> tuple[Path | None, str]:
        """The unfixed-case brief for the case, freshly attached to the briefs log; its path, or why it has none."""
        prs = self.opened_prs(parent, launch)
        return self.attach_brief(
            lane,
            lambda briefs: UNFIXED_TEMPLATE.read_text().format(
                lane=lane, parent=parent, case=case, parent_brief=json.loads(launch.target)["brief"], prs=prs, onset=pacific(self.now()), reported=pacific(self.now()), due=pacific(self.now() + INCIDENT_BAR), log=briefs, facts=self.drive_facts()
            ),
            f"INCIDENT {parent}: unfixed case brief for {lane}: {case}",
        )

    def case_lane(self, lane: str, parent: str, launch: actions.Action, case: str, key: str) -> bool:
        """Launch a case lane on the incident alias from the unfixed-case brief, freshly attached to the briefs log, and say whether the launch was taken; the root ratifies from the INCIDENT line."""
        log = f"escalation:{key}"
        line = f"INCIDENT {parent} {pacific(self.now())}: its fix does not cover: {case}"
        if refusal := self.launch_refusal(key, lane, INCIDENT_MODEL, "xhigh", UNFIXED_TEMPLATE):
            self.record(log, f"{line} | case lane {lane} not launched: {refusal}")
            return False
        brief, failure = self.case_brief(lane, parent, launch, case)
        if brief is None:
            self.record(log, f"{line} | case lane {lane} not launched: {failure}")
            return False
        self.accept_launch(key, lane, INCIDENT_MODEL, "xhigh", str(brief), True)
        self.record(log, f"{line} | case lane {lane} launching on incident xhigh, brief {brief}; root: ratify, and keep the reporter told that this case is still open")
        return True

    def offer_cases(self, key: str, first: str, parent: str, launch: actions.Action, cases: list[str], reason: str) -> None:
        """Put cases to the root as one `DECIDE` that carries, for each, the launch line that starts its lane from an attached brief."""
        if self.book.known(RUNNER, f"escalation:{key}"):
            return
        names = self.case_names(first)
        offers = []
        for case in cases:
            lane = next(names)
            brief, failure = self.case_brief(lane, parent, launch, case)
            offers.append(f"{case} | to launch its lane, post `orca-desk: launch {lane} NOW incident xhigh brief={brief}`" if brief else f"{case} | no launch line: {failure}")
        self.record(f"escalation:{key}", f"DECIDE {key} {parent}: {reason}: {'; '.join(offers)}")

    def opened_prs(self, lane: str, launch: actions.Action) -> str:
        """The PRs a lane's own cci `opened` records name since its launch, each once, as `#1 owner/repo#2`."""
        launched = actions.parse_stamp(launch.verification_receipt["at"])
        try:
            opened = self.lane_records(lane, "opened")
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return "unknown, cci could not be read"
        prs = {pr_name(pr) for record in opened if actions.parse_stamp(record["at"]) >= launched for pr in record["refs"].get("prs", [])}
        return " ".join(sorted(prs)) or "none opened yet"

    def lane_records(self, lane: str, *kinds: str) -> list[dict]:
        """A lane's own cci records of these kinds on this drive, under either of its `lane_aliases`; `cci tail` also returns the caller's peer mail, which is dropped."""
        names = lane_aliases(lane)
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in names for arg in ("--lane", name)), *(f"--kind={kind}" for kind in kinds))
        return [record for record in posted if record["drive"] == self.config.drive and record["lane"] in names and record["kind"] in kinds]

    def holds(self, slug: str) -> list[actions.Action]:
        return [action for action in self.book.actions(RUNNER, kind="hold", status="accepted") if json.loads(action.target)["slug"] == slug]

    def hold_line(self, at: str, key: str, rest: str) -> None:
        """Start the clock on an urgent hold; `aged_holds` turns one older than `deadlines.hold_minutes` into a DECIDE line for the root."""
        spec = HOLD_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:hold:{at}", f"HOLD-FAILED {key} inbox: one hold per line, in the form `{HOLD_GRAMMAR}`; no clock started")
            return
        if self.holds(spec["slug"]):
            return
        target = json.dumps({"slug": spec["slug"], "owner": spec["owner"], "what": spec["what"]})
        self.book.accept(RUNNER, f"hold:{spec['slug']}@{self.book.stamp()}", "hold", target, key, self.now() + timedelta(minutes=self.config.hold_minutes))

    def unhold_line(self, at: str, key: str, rest: str) -> None:
        spec = UNHOLD_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:unhold:{at}", f"HOLD-FAILED {key} inbox: lift a hold in the form `{UNHOLD_GRAMMAR}`")
            return
        for action in self.holds(spec["slug"]):
            self.book.edit(RUNNER, lambda incident, held=action.action_id: incident.verify(held, {"at": self.book.stamp(), "by": key}))

    def aged_holds(self) -> None:
        moment = self.now()
        for action in self.book.actions(RUNNER, kind="hold", status="accepted"):
            if not action.overdue(moment):
                continue
            held = json.loads(action.target)
            minutes = int((moment - actions.parse_stamp(action.accepted_at)).total_seconds() // 60)
            self.record(
                f"escalation:aged:{action.action_id}",
                f"DECIDE hold:{held['slug']} {held['owner']}: held {minutes} min: {held['what']} | the root decides it now with {held['owner']}, ahead of any open owner question on another subject",
            )

    def incident_lane(self, monitors: set[str], lane: str) -> str | None:
        """The lane already on one of these monitors: a launch in flight or the live dispatch of a launch whose brief's `ccx: incident=` names one, or a launch line later in the desk inbox naming one that this pass has yet to read."""
        for container in self.book.containers(LANE_PREFIX):
            other = container.removeprefix(LANE_PREFIX)
            launches = [action for action in self.book.actions(container, kind="launch") if brief_incidents(Path(json.loads(action.target)["brief"])) & monitors]
            if other == lane or not launches:
                continue
            dispatch = self.orca.show(other)
            live = dispatch.id if dispatch and dispatch.status not in INACTIVE else None
            if any(action.status in ("accepted", "started") or (live and action.dispatch_id == live) for action in launches):
                return other
        inbox = self.config.desk_inbox
        offset = self.book.load(RUNNER).facts[f"desk-inbox:{inbox}"]
        for line in inboxes.Inbox(inbox).lines(offset) if inbox.is_file() else []:
            directive = INBOX_DIRECTIVE.match(line.text.strip())
            if line.start <= offset or not directive or directive["verb"] != "launch" or not (spec := LAUNCH_SPEC.match(directive["rest"])) or spec["lane"] == lane:
                continue
            if brief_incidents(Path(spec["brief"])) & monitors:
                return spec["lane"]
        return None

    def launch_refusal(self, key: str, lane: str, model: str, effort: str, brief: Path) -> str:
        try:
            launch_model(model)
        except argparse.ArgumentTypeError as error:
            return str(error)
        if effort not in EFFORTS:
            return f"{effort} is not an effort: {', '.join(EFFORTS)}"
        if not brief.is_file():
            return f"brief {brief} is not a file"
        container = lane_container(lane)
        if container in self.book.store.ids():
            if held := self.book.load(container).actions.get(key):
                return f"{key} already holds a {held.kind} for {lane} ({held.status})"
            if flight := next((action for action in self.book.actions(container, kind="launch") if action.status in ("accepted", "started")), None):
                return f"launch {flight.action_id} for {lane} is {flight.status}"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            return f"dispatch {dispatch.id} is {dispatch.status}; relay to it instead"
        return ""

    def open_questions(self, dispatch: Dispatch) -> list[str]:
        """The questions the dispatch asked the Run that no message on the dispatch's terminal answers, newest first."""
        asked = (self.orca.call("orchestration", "check", "--terminal", f"run:{self.config.run}", "--all", "--types", "question").get("result") or {}).get("messages") or []
        mine = [message for message in asked if message.get("from_handle") in (f"dispatch:{dispatch.id}", dispatch.terminal)]
        if not mine:
            return []
        received = (self.orca.call("orchestration", "check", "--terminal", dispatch.terminal, "--all").get("result") or {}).get("messages") or []
        answered = {message.get("thread_id") for message in received}
        return [message["id"] for message in sorted(mine, key=lambda message: message["created_at"], reverse=True) if message["id"] not in answered]

    def unclaimed(self, key: str, lane: str, questions: list[str]) -> str:
        """The first of `questions` no other unfailed reply to the lane already answers, or empty: Orca refuses a second, different answer to one question."""
        claimed = {json.loads(reply.target)["reply_to"] for reply in self.book.actions(self.lane(lane), kind="reply") if reply.action_id != key and reply.status != "failed"}
        return next((question for question in questions if question not in claimed), "")

    def accept_launch(self, key: str, lane: str, model: str, effort: str, brief: str, owner_directed: bool, fast: bool = False, role: str = "", incident: str = "") -> tuple[actions.Action, bool]:
        urgent = owner_directed or model == INCIDENT_MODEL
        target = json.dumps({"model": model, "effort": effort, "brief": brief, "prior": self.orca.receipt(lane), "urgent": urgent, "fast": fast, "role": role, "incident": incident})
        return self.book.accept(self.lane(lane), key, "launch", target, key, self.now() + timedelta(minutes=self.config.launch_minutes))

    def landing_policy(self) -> actions.Action | None:
        self.book.ensure(LANDING, LANDING)
        verified = self.book.actions(LANDING, kind="policy", status="verified")
        superseded = {json.loads(action.target)["supersedes"] for action in verified}
        return next((action for action in verified if json.loads(action.target)["revision"] not in superseded), None)

    def accept_policy(self, key: str, rule: str, revision: str, source: str, supersedes: str) -> tuple[actions.Action, bool]:
        current = self.landing_policy()
        target = json.dumps({"rule": rule, "revision": revision, "supersedes": supersedes})
        action, created = self.book.accept(LANDING, key, "policy", target, source, None)
        if not created:
            return action, created
        if current and supersedes != json.loads(current.target)["revision"]:
            held = json.loads(current.target)
            reason = f"names {supersedes or 'no'} predecessor; the accepted landing policy is {held['rule']} at {held['revision']} ({current.authority_ref})"
            self.book.attempt(LANDING, lambda incident: incident.fail(key, reason))
            self.escalate(key, "STALE-POLICY", "landing", f"{rule} at {revision} from {source} rejected: {reason}")
        else:
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"at": self.book.stamp(), "supersedes": supersedes}))
        return self.book.load(LANDING).actions[key], created

    def transfer(self, lane: str) -> None:
        """Offer the container to the lane's receipt dispatch when it moved; ownership changes only on that dispatch's ack."""
        container = self.lane(lane)
        incident = self.book.load(container)
        dispatch = self.orca.receipt(lane)
        if dispatch and dispatch not in (incident.owner, incident.pending_owner):
            try:
                self.book.store.transfer(container, incident.owner_generation, dispatch)
            except actions.StaleGeneration:
                return

    def deliver(self) -> None:
        self.pass_binding = None
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            self.transfer(lane)
            pending = self.book.actions(container, status="accepted")
            relays = [action for action in pending if action.kind in ("relay", "reply")]
            for action in pending:
                if action.kind == "launch" and not self.holds_case_launch(container, lane, action) and not self.holds_owned_launch(container, lane, action):
                    self.launch(container, lane, action)
            if relays:
                self.deliver_relays(container, lane, relays)
            self.reconcile_sends(container, lane)

    def holds_case_launch(self, container: str, lane: str, action: actions.Action) -> bool:
        """Fail a pending case lane launch once its incident has ended, and say whether the launch must wait: failed, or left for the next pass when cci cannot be read."""
        if not action.action_id.startswith(UNFIXED_KEY):
            return False
        try:
            ended = self.incident_end(first_lane(lane), lane)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True
        if ended:
            self.book.edit(container, lambda incident: incident.fail(action.action_id, f"the incident ended with {ended}"))
            self.record(f"escalation:{container}/{action.action_id}:ended", f"WITHDRAWN {action.action_id} {lane}: the incident ended with {ended}; nothing was launched")
        return bool(ended)

    def holds_owned_launch(self, container: str, lane: str, action: actions.Action) -> bool:
        """Fail a pending incident launch once another owner holds its incident, and say whether the launch must wait: failed, or left for the next pass when cci cannot be read."""
        slug = json.loads(action.target).get("incident")
        if not slug:
            return False
        try:
            owner = self.incident_owner(slug, lane)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True
        if owner:
            self.book.edit(container, lambda incident: incident.fail(action.action_id, f"incident {slug} owned by {owner}"))
            self.escalate(action.action_id, "INCIDENT-OWNED", lane, f"incident {slug} owned by {owner}; no launch")
        return bool(owner)

    def deliver_relays(self, container: str, lane: str, relays: list[actions.Action]) -> None:
        """Send each accepted relay to the lane's live dispatch; with none and no launch in flight to bring one, fail each relay no send delivered as RELAY-FAILED now."""
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            for action in relays:
                self.send(container, dispatch, action)
            return
        if any(action.status in ("accepted", "started") for action in self.book.actions(container, kind="launch")):
            return
        reason = f"no live dispatch{f' ({dispatch.id} is {dispatch.status})' if dispatch else ''}"
        for action in relays:
            if self.delivered(container, action):
                continue
            if self.book.attempt(container, lambda incident, key=action.action_id: incident.fail(key, reason)):
                self.record(f"escalation:relay:{container}/{action.action_id}", f"RELAY-FAILED {action.action_id} {lane}: {reason}; nothing was relayed")

    def delivered(self, container: str, action: actions.Action) -> bool:
        return any(send.authority_ref == action.action_id and send.status == "completed" for send in self.book.actions(container, kind="send"))

    def sends(self, container: str, key: str, dispatch: str) -> list[actions.Action]:
        prefix = f"send:{key}:{dispatch}"
        return [action for action in self.book.actions(container, kind="send") if action.action_id.rsplit("#", 1)[0] == prefix]

    def send(self, container: str, dispatch: Dispatch, action: actions.Action) -> None:
        tries = self.sends(container, action.action_id, dispatch.id)
        if any(attempt.status != "failed" for attempt in tries):
            return
        if len(tries) >= SEND_ATTEMPTS or resends(tries):
            self.escalate(f"{container}/send:{action.action_id}:{dispatch.id}", "SEND-FAILED", dispatch.lane, f"{action.authority_ref} to {dispatch.id} failed {len(tries)} times: {tries[-1].reason}")
            return
        send_id = f"send:{action.action_id}:{dispatch.id}#{len(tries) + 1}"
        self.book.accept(container, send_id, "send", dispatch.id, action.action_id, None)
        if not self.book.attempt(container, lambda incident: incident.start(send_id, self.now(), dispatch_id=dispatch.id)):
            return
        self.post(container, dispatch, action, send_id, "")

    def post(self, container: str, dispatch: Dispatch, action: actions.Action, send_id: str, retry: str) -> None:
        spec = json.loads(action.target)
        thread = thread_of(container, action.action_id)
        answers = action.kind == "reply" and not self.answered_elsewhere(container, action, dispatch)
        if answers:
            argv = ["orchestration", "reply", "--id", spec["reply_to"], "--body", f"{spec['text']}\n\n({action.authority_ref})"]
        elif action.kind == "reply":
            argv = [
                "orchestration", "send", "--to", f"dispatch:{dispatch.id}", "--type", "dispatch", "--subject", f"{action.authority_ref}: on answered question {spec['reply_to']}",
                "--body", f"{spec['text']}\n\n({action.authority_ref})", "--thread-id", thread, "--payload", json.dumps({"action": thread}),
            ]
        else:
            body = f"{spec['text']}\n\nReply on thread {thread}: subject `started {action.action_id}` before acting, then `done {action.action_id}: <result>`."
            argv = [
                "orchestration", "send", "--to", f"dispatch:{dispatch.id}", "--type", "dispatch", "--subject", f"{action.authority_ref}: act {action.action_id}",
                "--body", body, "--thread-id", thread, "--payload", json.dumps({"action": thread}),
            ]
        reply = self.orca.call(*argv, *(["--retry-request", retry] if retry else []))
        if reply.get("ok"):
            message = ((reply.get("result") or {}).get("message") or {}).get("id", "sent")
            self.book.attempt(container, lambda incident: incident.complete(send_id, {"message": message, "at": self.book.stamp()}))
            if action.kind == "reply":
                self.book.attempt(container, lambda incident: (incident.start(action.action_id, self.now()), incident.complete(action.action_id, {"message": message, "at": self.book.stamp()})))
            if not answers:
                self.orca.wake(dispatch.terminal, f"{action.authority_ref}: read Orca message on thread {thread} now")
        elif answers and reply["error"]["code"] == ANSWER_CONFLICT:
            self.book.attempt(container, lambda incident: incident.fail(send_id, f"{ANSWER_CONFLICT}: {reply['error'].get('message', '')}"[:300]))
            self.send(container, dispatch, action)
        elif reply["error"]["code"] in UNREACHABLE:
            self.book.attempt(container, lambda incident: lost(incident, send_id, {"request_id": request_id(reply)}))
        else:
            self.book.attempt(container, lambda incident: incident.fail(send_id, json.dumps(reply.get("error"))[:300]))

    def answered_elsewhere(self, container: str, action: actions.Action, dispatch: Dispatch) -> bool:
        """Whether Orca refused a reply of `action` because its question already holds another answer, so the text goes to the dispatch as a message instead."""
        return any(send.status == "failed" and (send.reason or "").startswith(ANSWER_CONFLICT) for send in self.sends(container, action.action_id, dispatch.id))

    def reconcile_sends(self, container: str, lane: str) -> None:
        """Settle every send whose response was lost from Orca's request receipt or the recipient's mailbox; while both reads succeed and show neither, resend it on the same thread once per `RESEND_AFTER`, and report `RESEND_LIMIT` resends all lost the same way as UNACKED."""
        cutoff = self.now() - ORPHANED_SEND
        for send in self.book.actions(container, kind="send", status="started"):
            if actions.parse_stamp(send.started_at) < cutoff:
                self.book.attempt(container, lambda incident, key=send.action_id: incident.lose(key))
        for send in self.book.actions(container, kind="send", status="unverifiable"):
            action = self.book.load(container).actions[send.authority_ref]
            request = (send.response or {}).get("request_id", "")
            state = self.orca.request_state(request) if request else "absent"
            if state in ("pending", UNREAD):
                continue
            dispatch = self.orca.show(lane)
            current = dispatch if dispatch and dispatch.id == send.target else None
            if state == "completed" and current:
                self.post(container, current, action, send.action_id, request)
                continue
            mailbox = self.orca.mailbox(current.terminal) if current else []
            if mailbox is None:
                continue
            found = next((message for message in mailbox if delivers(message, container, action)), None)
            if found:
                receipt = {"message": found["id"], "at": found.get("created_at", self.book.stamp()), "reconciled": True}
                self.book.attempt(container, lambda incident, key=send.action_id, receipt=receipt: incident.complete(key, receipt))
                if action.kind == "reply":
                    self.book.attempt(container, lambda incident, key=action.action_id, receipt=receipt: (incident.start(key, self.now()), incident.complete(key, receipt)))
            elif not current or current.status in INACTIVE:
                self.escalate(f"{container}/{send.action_id}", "UNVERIFIABLE", lane, f"{action.authority_ref} send to {send.target} lost its response; Orca holds no receipt and no message on its thread; it was not resent")
            elif self.now() - actions.parse_stamp(send.started_at) >= RESEND_AFTER:
                sent_again = len(resends(self.sends(container, action.action_id, current.id)))
                if sent_again >= RESEND_LIMIT:
                    self.report_unacked(container, lane, action, send, sent_again)
                else:
                    self.resend(container, current, action, send, sent_again + 1)

    def report_unacked(self, container: str, lane: str, action: actions.Action, send: actions.Action, sent_again: int) -> None:
        log = f"escalation:unacked:{container}/{send.action_id}"
        if self.book.known(RUNNER, log):
            return
        self.keep({"id": action.action_id, "body": json.loads(action.target)["text"]})
        self.record(log, f"UNACKED {action.action_id} {lane}: the send to {send.target} and {sent_again} resends lost their response, and the lane has not named {action.action_id} on its thread or the Run; Orca holds no receipt and no message on its thread; nothing more is sent; check the lane's Run messages before delivering the relay text at this record's path by hand")

    def resend(self, container: str, dispatch: Dispatch, action: actions.Action, send: actions.Action, number: int) -> None:
        send_id = f"{send.action_id.split(RESEND)[0]}{RESEND}{number}"

        def retire_and_start(incident: actions.Incident) -> None:
            incident.fail(send.action_id, f"response lost with no receipt and no message on its thread; resent as {send_id}")
            incident.accept(send_id, "send", dispatch.id, action.action_id, self.now())
            incident.start(send_id, self.now(), dispatch_id=dispatch.id)

        if self.book.attempt(container, retire_and_start):
            self.post(container, dispatch, action, send_id, "")

    def launch_log(self, container: str, key: str) -> Path:
        return (self.config.store or actions.incidents_dir()).parent / "desk-runner-launches" / f"{container}-{key}.out"

    def sprite_log(self, container: str, key: str) -> Path:
        return self.launch_log(container, key).with_suffix(".sprite.out")

    def launch(self, container: str, lane: str, action: actions.Action) -> None:
        """Start orca-launch.sh, or worker-launch.sh for a Sprite, detached, so a readiness wait never holds a relay; `reap` records its printed line."""
        spec = json.loads(action.target)
        if not self.bound_for_launch():
            return
        refusal = self.sprite_refusal(lane, spec)
        if refusal is not None and (held := self.load_refusal()):
            self.move_load(held)
            if spec["urgent"]:
                self.refuse_launch(container, lane, action, held)
            elif self.now() - actions.parse_stamp(action.accepted_at) >= timedelta(minutes=self.config.load_hold_minutes):
                self.expire_launch(container, lane, action, held)
            return
        if not self.book.attempt(container, lambda incident: incident.start(action.action_id, self.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        if refusal is None:
            self.launch_sprite(container, lane, action)
            return
        if refusal:
            self.escalate(f"{container}/{action.action_id}:sprite", "SPRITE-FALLBACK", lane, f"{action.action_id} launches locally: {refusal}")
        self.launch_local(container, lane, action)

    def launch_local(self, container: str, lane: str, action: actions.Action) -> None:
        spec = json.loads(action.target)
        argv = [str(LAUNCHER), lane, spec["model"], spec["effort"], spec["brief"]]
        env = {**self.config.launch_env, **self.orca.sender, "ORCA_LAUNCH_STATE": str(self.config.receipts)}
        if spec.get("fast"):
            env[CLAUDE_ARGS] = " ".join(filter(None, [env.get(CLAUDE_ARGS, self.shell.env(CLAUDE_ARGS)), CLAUDE_FAST_MODE]))
        self.launching[f"{container}/{action.action_id}"] = self.shell.spawn(argv, self.launch_log(container, action.action_id), env)

    def launch_sprite(self, container: str, lane: str, action: actions.Action) -> None:
        spec = json.loads(action.target)
        sprite = self.config.sprite or {}
        argv = [sprite["launcher"], lane, sprite_model(spec["model"]), spec["effort"], spec["brief"]]
        env = {**self.config.launch_env, **self.orca.sender, "WORKER_RUN": self.config.run, "WORKER_REMOTE_ATTACH": str(SPRITE_ATTACH), **sprite.get("env", {})}
        key = f"{container}/{action.action_id}"
        self.launching[key] = self.shell.spawn(argv, self.sprite_log(container, action.action_id), env)
        self.sprites.add(key)

    def sprite_refusal(self, lane: str, spec: dict) -> str | None:
        """None when the launch goes to a Sprite; why it stays local otherwise, empty when it was never a Sprite candidate."""
        if not self.config.sprite or spec["model"] in SPRITE_LOCAL_MODELS or spec.get("fast") or not sprite_placed(Path(spec["brief"])):
            return ""
        if not SPRITE_LANE.fullmatch(lane):
            return f"lane {lane} is not a Sprite name of up to 55 lowercase letters, digits, and dashes"
        return None

    def load_refusal(self) -> str:
        """Why no launch may start on the Mac now, or empty while the 1-minute load is at or below the core count."""
        load, cores = self.shell.load(), self.shell.cores()
        if load <= cores:
            return ""
        return f"load {load:.0f} above {cores} cores"

    def move_load(self, held: str) -> None:
        """Tell the root, once per ten minutes, that launches are refused on load and that the load has to move."""
        self.escalate(f"load:{actions.stamp(self.now())[:15]}", "LOAD-HIGH", "runner", f"{held}: Orca launches are refused while it lasts, because a worktree checkout times out under it. root: move the load off this Mac now; a pass, backfill, dry run, or bulk rewrite runs on in-region compute")

    def refuse_launch(self, container: str, lane: str, action: actions.Action, held: str) -> None:
        """Fail an incident or owner-directed launch at once on load, since it cannot wait out the hold."""
        key = action.action_id
        text = f"{key} refused on {held}; nothing was launched, and the root launches it in-process or once the load is moved"
        if self.book.attempt(container, lambda incident: incident.fail(key, text)):
            self.escalate(f"{container}/{key}", "LAUNCH-FAILED", lane, text)

    def expire_launch(self, container: str, lane: str, action: actions.Action, held: str) -> None:
        key = action.action_id
        text = f"{key} launch failed after {self.config.load_hold_minutes}m held, {held}; move the load off this Mac, then submit it under a new key"
        if not self.book.attempt(container, lambda incident: incident.fail(key, text)):
            return
        self.escalate(f"{container}/{key}", "LAUNCH-HELD", lane, text)
        self.orca.call("orchestration", "send", "--to", f"run:{self.config.run}", "--run", self.config.run, "--type", "status", "--subject", f"LAUNCH-HELD {lane}", "--body", text)

    def reap(self) -> None:
        """Settle each started launch from its printed line; a launch with neither a line nor a new receipt is unverifiable, never relaunched."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            for action in self.book.actions(container, kind="launch", status="started"):
                process = self.launching.get(f"{container}/{action.action_id}")
                if process and process.poll() is None:
                    continue
                self.launching.pop(f"{container}/{action.action_id}", None)
                if f"{container}/{action.action_id}" in self.sprites or (self.sprite_log(container, action.action_id).is_file() and not self.launch_log(container, action.action_id).is_file()):
                    self.sprites.discard(f"{container}/{action.action_id}")
                    self.settle_sprite(container, lane, action, exited=process is not None)
                    continue
                self.settle_launch(container, lane, action)

    def settle_sprite(self, container: str, lane: str, action: actions.Action, exited: bool) -> None:
        """Copy a ready Sprite launch's receipts beside the local ones and settle it; start the same launch locally only when its receipts prove worker-start never ran."""
        key = action.action_id
        out = self.sprite_log(container, key).read_text().strip()
        if not out:
            self.settle_launch(container, lane, action)
            return
        attempt = sprite_attempt(Path(json.loads(action.target)["brief"]))
        try:
            ready = json.loads(out.splitlines()[-1])
            line = f"{lane} ready task={ready['task']} dispatch={ready['dispatch']} terminal={ready['terminal']} worktree={ready['worktree']}"
            receipt = (attempt / f"{lane}.json").read_text()
        except (json.JSONDecodeError, KeyError, TypeError, FileNotFoundError):
            reason = " ".join(out.split())[:300]
            if sprite_never_started(attempt, lane, exited):
                self.escalate(f"{container}/{key}:sprite", "SPRITE-FALLBACK", lane, f"{key} launches locally: {reason}")
                self.launch_local(container, lane, action)
                return
            self.book.attempt(container, lambda incident: incident.lose(key))
            started = sprite_started_as(attempt / f"{lane}.json")
            self.escalate(
                f"{container}/{key}:sprite",
                "SPRITE-UNVERIFIABLE",
                lane,
                f"{key} does not launch locally: its Sprite launch is unverified and a remote worker may be running{started}; receipts in {attempt}, nothing retried or cleaned up: {reason}",
            )
            return
        self.config.receipts.mkdir(parents=True, exist_ok=True)
        (self.config.receipts / f"{lane}.json").write_text(receipt)
        (self.config.receipts / f"{lane}.terminal").write_text(f"{ready['terminal']}\n")
        self.launch_log(container, key).write_text(f"{line}\n")
        self.settle_launch(container, lane, action)

    def settle_launch(self, container: str, lane: str, action: actions.Action) -> None:
        log = self.launch_log(container, action.action_id)
        out = log.read_text().strip() if log.is_file() else ""
        launched = LAUNCHED.search(out)
        prior = json.loads(action.target)["prior"]
        receipt = self.orca.receipt(lane)
        key = action.action_id
        if launched and launched["lane"] == lane:
            proof = {"line": launched.group(0), "dispatch": launched["dispatch"], "at": self.book.stamp()}
        elif not out and receipt and receipt != prior:
            proof = {"line": f"receipt dispatch {receipt}", "dispatch": receipt, "at": self.book.stamp()}
        elif out:
            reason = next((line for line in reversed(out.splitlines()) if line.startswith(f"{lane} failed ")), " ".join(out.split())[:300])
            self.book.attempt(container, lambda incident: incident.fail(key, reason))
            self.escalate(f"{container}/{key}", "LAUNCH-FAILED", lane, reason)
            return
        else:
            self.book.attempt(container, lambda incident: incident.lose(key))
            self.escalate(f"{container}/{key}", "UNVERIFIABLE", lane, "launch outcome unknown: no launch line and no new receipt; it was not relaunched")
            return
        self.book.attempt(container, lambda incident: (incident.complete(key, proof, dispatch_id=proof["dispatch"]), incident.verify(key, proof)))
        self.record(f"escalation:{container}/{key}:launched", f"LAUNCHED {key} {lane}: dispatch {proof['dispatch']} terminal {launched['terminal'] if launched else self.orca.terminal(lane)}")
        self.transfer(lane)
        if launched and launched["how"] == "unsupervised":
            self.escalate(f"{container}/{key}", "UNSUPERVISED", lane, f"launched without Orca supervision: {proof['line']}")

    def check(self) -> bool:
        """Read the Run past the persisted cursor, relay any message of the Delivery Orca handed over before that read that holds no relay record, then acknowledge the Delivery; False while no cursor exists, so nothing dispatches before the first read sets one."""
        key, relayed = f"inbox:{self.config.run}", f"relayed:{self.config.run}"
        facts = self.book.load(RUNNER).facts
        cursor = facts.get(key)
        delivery = self.orca.call("orchestration", "check", "--run", self.config.run).get("result") or {}
        unrelayed = {message["id"] for message in delivery.get("messages") or [] if message["type"] != "heartbeat"} - set(facts.get(relayed, []))
        limit = INBOX_PAGE_SIZE
        while True:
            reply = self.orca.ask("orchestration", "inbox", "--terminal", f"run:{self.config.run}", "--limit", str(limit))
            if not reply["ok"]:
                self.escalate(f"orca-inbox:{actions.stamp(self.now())[:15]}", "ORCA-INBOX", "runner", json.dumps(reply["error"]))
                return cursor is not None
            messages = reply["result"]["messages"]
            if cursor is None:
                newest = max((message["sequence"] for message in messages), default=0)
                self.book.edit(RUNNER, lambda incident: incident.facts.update({key: newest}))
                return True
            if len(messages) < limit or (messages[-1]["sequence"] <= cursor + 1 and unrelayed <= {message["id"] for message in messages}):
                break
            limit *= 2
        for message in reversed(messages):
            behind = message["sequence"] <= cursor
            if behind and message["id"] not in unrelayed:
                continue
            cursor = max(cursor, message["sequence"])
            if message["type"] == "heartbeat":
                self.book.edit(RUNNER, lambda incident: incident.facts.update({key: cursor}))
                continue
            lane = self.orca.lane_of(message["from_handle"])
            if not self.message(message | {"lane": lane}) and behind:
                self.keep(message)
                self.escalate(message["id"], "UNRELAYED", lane, f"{message['type']} from {message['from_handle']} at sequence {message['sequence']}, behind cursor {cursor}, has no relay record and its type has no relay: {message.get('subject') or ''}")
            self.book.edit(RUNNER, lambda incident: (incident.facts.update({key: cursor}), incident.facts.setdefault(relayed, []).append(message["id"])))
            unrelayed.discard(message["id"])
        if delivery.get("deliveryId") and not unrelayed:
            self.orca.call("orchestration", "check", "--run", self.config.run, "--ack", delivery["deliveryId"], "--peek")
        return True

    def message_file(self, msg: str) -> Path:
        return self.config.receipts / "cci" / "messages" / f"{msg}.md"

    def keep(self, message: dict) -> None:
        """Write the whole Orca message where `flush` finds it, so the cci record relaying it carries every word by path."""
        path = self.message_file(message["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{message.get('subject') or ''}\n\n{message.get('body') or ''}".strip() + "\n")

    def message(self, message: dict) -> bool:
        """Relay one Run message by its type; False for a type the runner relays nowhere."""
        decoded = json.loads(message.get("payload") or "{}")
        payload = decoded if isinstance(decoded, dict) else {}
        lane = message.get("lane") or self.orca.lane_of(message.get("from_handle", ""))
        sender = payload.get("dispatchId") or (self.orca.receipt(lane) if self.orca.terminal(lane) == message.get("from_handle") else "")
        subject = message.get("subject") or ""
        acked = ACK.match(subject)
        thread = message.get("thread_id") or ""
        if acked and "/" in thread:
            container, key = thread.split("/", 1)
            result = acked["rest"].removeprefix(key).lstrip(": ").strip()
            self.acknowledge(container, key, sender, acked["verb"], result or (message.get("body") or "").strip(), message["id"])
        elif message["type"] in ("question", "escalation"):
            self.keep(message)
            if not self.withdrawn_ask(lane, message):
                self.escalate(message["id"], "DECIDE", lane, f"{message.get('subject', '')}: {(message.get('body') or '')[:300]}")
        elif message["type"] == "worker_done":
            self.keep(message)
            self.escalate(message["id"], "OUTCOME", lane, f"worker_done {payload.get('outcome', '?')} dispatch={sender}: {message.get('subject', '')}")
        elif message["type"] == "status" and subject.lower().startswith(MILESTONE_SUBJECTS):
            kind = subject.split(":", 1)[0].upper()
            self.keep(message)
            body = " ".join((message.get("body") or "").split())[:300]
            self.escalate(message["id"], kind, lane, f"{subject}: {body}")
            self.stamp_status(lane, message["id"], kind.lower())
            if kind != "MECHANISM":
                self.unfixed(lane, message)
        elif message["type"] == "status" and (key := self.relay_named(lane, f"{subject}\n{message.get('body') or ''}")):
            self.keep(message)
            verb, result = (acked["verb"], acked["rest"].strip()) if acked else ("done", subject)
            self.acknowledge_on_run(lane_container(lane), key, sender, verb, result or (message.get("body") or "").strip(), message["id"])
        elif message["type"] in ("decision_gate", "handoff"):
            self.keep(message)
            self.escalate(message["id"], message["type"].upper(), lane, f"{message.get('subject', '')}: {(message.get('body') or '')[:200]}")
        else:
            return False
        return True

    def relay_named(self, lane: str, text: str) -> str | None:
        container = lane_container(lane)
        if container not in self.book.store.ids():
            return None
        named = set(RUN_REF.findall(text))
        return next(
            (
                action.action_id
                for action in self.book.actions(container)
                if action.kind in ("relay", "reply") and action.status in ("accepted", "started") and (ref := RELAY_REF.search(action.action_id)) and ref[1] in named
            ),
            None,
        )

    def acknowledge_on_run(self, container: str, key: str, sender: str, verb: str, text: str, message: str) -> None:
        receipt = {"message": message, "at": self.book.stamp(), "acknowledged": True}
        for send in self.book.actions(container, kind="send"):
            if send.authority_ref == key and send.status in ("started", "unverifiable"):
                self.book.attempt(container, lambda incident, settled=send.action_id: incident.complete(settled, receipt))
        self.acknowledge(container, key, sender, verb, text, message)

    def withdrawn_ask(self, lane: str, message: dict) -> bool:
        """Take a lane's question as moot when the root withdrew its dispatch, or fenced a case lane's dispatch by hand: the lane is marked withdrawn, never offered to be dispatched again, and the root gets one line in place of a `DECIDE`."""
        by = self.withdrawn_by(lane)
        if not by and is_case_lane(lane) and (dispatch := self.orca.show(lane)) and dispatch.fenced:
            by = f"a stop of dispatch {dispatch.id}"
            self.book.edit(self.lane(lane), lambda incident: incident.facts.update({WITHDRAWN: {"dispatch": dispatch.id, "by": by}}))
        if not by:
            return False
        self.escalate(message["id"], "WITHDRAWN", lane, f"withdrawn by {by}, so it is not dispatched again and its question needs no answer: {message.get('subject', '')}")
        return True

    def stamp_status(self, lane: str, message: str, name: str) -> None:
        """Keep when a lane's milestone status arrived on its container, where the incident clock reads it beside the lane's cci records."""
        container = lane_container(lane)
        if container in self.book.store.ids():
            self.book.edit(container, lambda incident: incident.facts.setdefault(MILESTONE_STATUSES, {}).setdefault(message, {"name": name, "at": self.book.stamp()}))

    def acknowledge(self, container: str, key: str, sender: str, verb: str, text: str, message: str) -> None:
        """Apply a lane's started/done reply only from the dispatch that owns the container; a pending dispatch's first ack takes ownership first."""
        if container not in self.book.store.ids() or key not in self.book.load(container).actions:
            return
        incident = self.book.load(container)
        if sender and sender == incident.pending_owner:
            self.book.store.ack(container, sender)
        moved = verb == "started" and self.book.as_owner(container, sender, lambda live: live.start(key, self.now(), dispatch_id=sender))
        if verb == "done":
            response = {"text": text[:500], "message": message, "at": self.book.stamp()}
            moved = self.book.as_owner(container, sender, lambda live: finish(live, key, response, sender))
        if not moved and sender and sender != self.book.load(container).owner:
            self.stand_down(container, key, sender, message)

    def stand_down(self, container: str, key: str, sender: str, message: str) -> None:
        incident = self.book.load(container)
        notice = f"stand-down:{key}:{sender}"
        text = f"Do not execute {key}: dispatch {incident.owner} owns {container} at generation {incident.owner_generation}."
        action, created = self.book.accept(container, notice, "reply", json.dumps({"text": text, "reply_to": message}), notice, None)
        dispatch = self.orca.show(container.removeprefix(LANE_PREFIX))
        if created and dispatch:
            self.send(container, Dispatch(dispatch.lane, sender, dispatch.terminal, dispatch.status, dispatch.live), action)

    def brief_for(self, lane: str) -> Path | None:
        container = lane_container(lane)
        launched = [json.loads(action.target)["brief"] for action in self.book.actions(container, kind="launch", status="verified")] if container in self.book.store.ids() else []
        candidates = [Path(path) for path in launched[-1:]] + [self.attachment(f"{lane}.full.md"), self.attachment(f"{lane}.md")]
        return next((path for path in candidates if path and path.is_file()), None)

    def briefs(self, *argv: str) -> Done:
        return self.shell.run(["ccn", "-R", self.config.briefs_repo, *argv])

    def resolve_briefs_log(self) -> str:
        """Keep `orca.briefs.log` on a log that resolves: the configured one while `ccn log show` finds it, else the drive's `briefs: <drive>` log, created when the clone has none, with the repoint reported to root; why it could not, or ""."""
        stale = self.config.briefs_log
        if stale and self.briefs("log", "show", stale).code == 0:
            return ""
        title = f"briefs: {self.config.drive}"
        listed = self.briefs("log", "list", "--json")
        if listed.code != 0:
            return ccn_failure(listed)
        found = [log["id"] for log in json.loads(listed.out) if log["title"] == title]
        if not found:
            added = self.briefs("log", "add", title, "--label", f"program:{self.config.drive}", "--json")
            if added.code != 0:
                return ccn_failure(added)
            found = [json.loads(added.out)["id"]]
        self.config.briefs_log = found[0]
        self.escalate(f"briefs-log:{stale or 'unset'}", "BRIEFS-LOG", "runner", f"briefs log {stale or '(unset)'} does not resolve on {self.config.briefs_repo}; briefs now attach to {found[0]} ({title}), so set orca.briefs.log to it")
        return ""

    def attachment(self, name: str) -> Path | None:
        done = self.briefs("attachment", "path", self.config.briefs_log, name)
        return Path(done.out.strip()) if done.code == 0 else None

    def attach(self, name: str, text: str, entry: str) -> str:
        with tempfile.TemporaryDirectory() as staging:
            path = Path(staging) / name
            path.write_text(text)
            done = self.briefs("log", "append", self.config.briefs_log, "--entry", entry, "--attach", str(path), "--replace")
        return "" if done.code == 0 else ccn_failure(done)

    def idle(self, seconds: float) -> None:
        """Wait out the gap between passes, cut short by a desk inbox append or a launch process exiting."""
        inbox, running = self.shell.stat(self.config.desk_inbox), self.running()
        for _ in range(int(seconds / WAKE_SECONDS)):
            if self.shell.stat(self.config.desk_inbox) != inbox or self.running() < running or self.stop_requested():
                return
            self.shell.sleep(WAKE_SECONDS)

    def running(self) -> int:
        return sum(process.poll() is None for process in self.launching.values())

    def rotate_inboxes(self) -> None:
        for path in sorted(self.config.desk_inbox.parent.glob("*.md")):
            inboxes.Inbox(path).rotate(self.now().timestamp())

    def sweep(self) -> None:
        """Unread mail on a live dispatch gets one wake, typed only at its idle prompt; mail a settled dispatch never read, a dispatch that is not live, or a prompt watch that stopped polling escalates once."""
        done = self.shell.run([str(SCRIPTS / "orca-check.sh"), "--stale"], env={"ORCA_CHECK_STATE": str(self.config.receipts), "ORCA_LAUNCH_RUN": self.config.run})
        for line in done.out.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "STALE":
                continue
            lane, status, item = parts[1], parts[3], parts[4]
            if status != "unread":
                self.escalate(f"stale:{item}", "STALE-MAIL", lane, f"{item} unread by a {status} dispatch")
                continue
            if (
                not self.book.known(RUNNER, f"wake:{item}")
                and (terminal := self.orca.terminal(lane))
                and self.orca.wake(terminal, f"unread Orca message {item}; read it now")
            ):
                self.book.accept(RUNNER, f"wake:{item}", "wake", item, lane, None)
        hour = actions.stamp(self.now())[:13]
        named = {action.action_id for action in self.book.actions(RUNNER, kind="reclaim")}
        settled: list[Dispatch] = []
        lanes = [lane for lane in self.orca.lanes() if f"reclaim:{self.orca.receipt(lane)}" not in named]
        with ThreadPoolExecutor(SHOW_WORKERS) as pool:
            shown = list(pool.map(self.orca.show, lanes))
        for lane, dispatch in zip(lanes, shown):
            if not dispatch:
                continue
            if dispatch.status in INACTIVE:
                settled.append(dispatch)
                continue
            if not dispatch.live:
                self.escalate(f"liveness:{dispatch.id}:{hour}", "LIVENESS", lane, f"dispatch={dispatch.id} terminal={dispatch.terminal} is not live; resume it in place, never relaunch on this alone")
        if settled:
            self.reclaim(settled)
        watch = json.loads(self.shell.run([sys.executable, str(SCRIPTS / "prompt_watch.py"), "show", "--cci-drive", self.config.drive, "--json"]).out or "{}")
        for row in watch.get("rows") or []:
            if row["state"] == "down":
                self.escalate(f"prompt-watch:{hour}", "PROMPT-WATCH-DOWN", "runner", row["detail"])

    def reclaim(self, settled: list[Dispatch]) -> None:
        named = " ".join(f"{dispatch.lane}={dispatch.id}:{dispatch.terminal}" for dispatch in settled[:RECLAIM_NAMED])
        more = f" and {len(settled) - RECLAIM_NAMED} more" if len(settled) > RECLAIM_NAMED else ""
        scope = "".join(f" --dispatch {dispatch.id}" for dispatch in settled) if len(settled) <= RECLAIM_NAMED else ""
        step = f"run {self.config.gc} --run {self.config.run}{scope}" if self.config.gc else "close each idle terminal and remove each finished worktree under R195"
        self.escalate(f"reclaim:{settled[0].id}", "RECLAIM", "runner", f"{len(settled)} settled dispatch(es) still hold their terminal: {named}{more}; {step}")
        for dispatch in settled:
            self.book.accept(RUNNER, f"reclaim:{dispatch.id}", "reclaim", dispatch.terminal, dispatch.lane, None)

    def overdue(self, container: str, about: str) -> None:
        moment = self.now()
        for action in self.book.load(container).actions.values():
            if action.kind not in ("relay", "reply", "launch", "enqueue") or not action.overdue(moment) or action.status not in ("accepted", "started"):
                continue
            if action.kind in ("relay", "reply") and action.status == "started":
                continue
            sent = [send for send in self.book.actions(container, kind="send") if send.authority_ref == action.action_id]
            missing = {
                "relay": f"delivered to {sent[-1].target}, no started reply" if sent and sent[-1].status == "completed" else "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "reply": "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "launch": "launch never finished" if action.status == "started" else "launch never started",
                "enqueue": "stack-enqueue never returned" if action.status == "started" else "enqueue never ran",
            }[action.kind]
            self.escalate(f"deadline:{container}/{action.action_id}:{action.status}", "DEADLINE", about, f"{action.authority_ref} {action.kind}: {missing}; accepted {pacific(actions.parse_stamp(action.accepted_at))}")

    def deadlines_orca(self) -> None:
        for container in self.book.containers(LANE_PREFIX):
            self.overdue(container, container.removeprefix(LANE_PREFIX))

    def incident_clock(self) -> None:
        """Hold each live incident fix lane the runner launched to its budget from the verified launch: past a threshold without its milestone, launch the Opus fallback and log INCIDENT-CLOCK, once per launch; `ready`, a closing record, or a settled dispatch stops the clock."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            incident = self.book.load(container)
            launch = fix_launch(incident)
            kept = incident.facts.get(CLOCK) or {}
            current = bool(launch) and kept.get("key") == launch.action_id
            if not launch or (current and kept["ended"]) or not (dispatch := self.orca.show(lane)):
                continue
            launched = actions.parse_stamp(launch.verification_receipt["at"])
            clock = {"key": launch.action_id, "launched": launch.verification_receipt["at"], "reached": kept["reached"] if current else {}, "ended": ""}
            if dispatch.id != launch.dispatch_id:
                clock["ended"] = f"relaunched as dispatch {dispatch.id}"
            elif dispatch.status in INACTIVE:
                clock["ended"] = f"dispatch {dispatch.status}"
            else:
                try:
                    clock["reached"] = self.reached(container, lane, launched)
                except CciTailFailed as failure:
                    self.tail_failed(failure)
                    continue
                clock["ended"] = next((name for name in (*CLOCK_ENDS, "ready") if name in clock["reached"]), "")
                if not clock["ended"]:
                    self.overrun(lane, launch, launched, clock["reached"])
            if clock != kept:
                self.book.edit(container, lambda incident, clock=clock: incident.facts.update({CLOCK: clock}))

    def reached(self, container: str, lane: str, launched: datetime) -> dict[str, str]:
        """Each milestone a lane reached since `launched`, at its earliest stamp: the lane's own cci records by kind, and its milestone statuses from the Run."""
        marks = [(mark, record["at"]) for record in self.lane_records(lane, *CLOCK_MILESTONES, *CLOCK_ENDS, *TEXT_MARK_KINDS) if (mark := clock_mark(record))]
        marks += [(status["name"], status["at"]) for status in (self.book.load(container).facts.get(MILESTONE_STATUSES) or {}).values()]
        reached: dict[str, str] = {}
        for name, at in sorted(marks, key=lambda mark: actions.parse_stamp(mark[1])):
            if actions.parse_stamp(at) >= launched:
                reached.setdefault(name, at)
        return reached

    def overrun(self, lane: str, launch: actions.Action, launched: datetime, reached: dict[str, str]) -> None:
        """Log each threshold the lane has passed without its milestone or a later one, once per launch; the mechanism and PR thresholds launch the Opus fallback first, and the ready threshold names the gap."""
        elapsed = whole_minutes(launched, self.now())
        limits = {"mechanism": self.config.incident_mechanism_minutes, "opened": self.config.incident_pr_minutes, "ready": self.config.incident_ready_minutes}
        names = list(CLOCK_MILESTONES)
        missing = names[max((names.index(name) + 1 for name in reached if name in names), default=0) :]
        for name in missing:
            label = CLOCK_MILESTONES[name]
            log = f"escalation:{CLOCK}:{launch.dispatch_id}:{name}"
            if elapsed < limits[name] or self.book.known(RUNNER, log):
                continue
            phases = clock_phases(launched, reached, self.now())
            if name == "ready":
                text = f"not ready {elapsed} min after its launch at {pacific(launched)}, limit {limits[name]} min; missing {', '.join(CLOCK_MILESTONES[gap] for gap in missing)}; {phases}"
            else:
                text = f"no {label} {elapsed} min after its launch at {pacific(launched)}, limit {limits[name]} min; {phases} | {self.fallback(lane, launch, label, elapsed)}"
            self.record(log, f"INCIDENT-CLOCK {lane} {label.lower()}: {text}")

    def fallback(self, lane: str, launch: actions.Action, missing: str, minutes: int) -> str:
        """Launch `<lane>-opus` once per clocked launch, on opus xhigh in Claude fast mode, from the lane's own brief under a fallback header attached to the briefs log; the clause returned tells the blocker what was done."""
        backup = f"{lane}{CLOCK_FALLBACK}"
        key = f"{CLOCK}:{launch.dispatch_id}"
        if lane.endswith(CLOCK_FALLBACK):
            return f"{lane} is itself an Opus fallback, so none is launched for it"
        if self.book.known(lane_container(backup), key):
            return f"the Opus fallback {backup} is already launched"
        if rival := self.executor_backup(lane):
            return f"no fallback launched for {backup}: the incident executor's Opus backup {rival} is launching or live, and a fix lane gets one Opus fallback"
        brief = self.brief_for(lane)
        if not brief:
            return f"no fallback launched for {backup}: {lane} has no brief file"
        if refusal := self.launch_refusal(key, backup, "opus", "xhigh", brief):
            return f"no fallback launched for {backup}: {refusal}"
        text = FALLBACK_BRIEF.format(backup=backup, lane=lane, minutes=minutes, missing=missing, drive=self.config.drive, brief=brief.read_text())
        attached, failure = self.attach_brief(backup, lambda _: text, f"INCIDENT-CLOCK {lane}: Opus fallback brief for {backup}, no {missing} at +{minutes} min")
        if attached is None:
            return f"no fallback launched for {backup}: {failure}"
        self.accept_launch(key, backup, "opus", "xhigh", str(attached), True, fast=True)
        return f"the Opus fallback {backup} is launching on opus xhigh in fast mode, brief {attached}"

    def in_process(self) -> None:
        """While a fix lane's clock runs, launch its Opus fallback once more after a failed Orca launch, and after a second failure tell the root to start it in-process."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            incident = self.book.load(container)
            launch, clock = fix_launch(incident), incident.facts.get(CLOCK) or {}
            backup = f"{lane}{CLOCK_FALLBACK}"
            if not launch or clock.get("key") != launch.action_id or clock["ended"] or lane_container(backup) not in self.book.store.ids():
                continue
            key = f"{CLOCK}:{launch.dispatch_id}"
            tries = [action for action in self.book.actions(lane_container(backup), kind="launch") if action.action_id in (key, f"{key}{RETRY}")]
            if not tries or any(action.status != "failed" for action in tries) or self.executor_backup(lane):
                continue
            brief = json.loads(tries[0].target)["brief"]
            if len(tries) == 1:
                if not self.launch_refusal(f"{key}{RETRY}", backup, "opus", "xhigh", Path(brief)):
                    self.accept_launch(f"{key}{RETRY}", backup, "opus", "xhigh", brief, True, fast=True)
                continue
            reasons = " | ".join(action.reason or "" for action in tries)
            self.record(
                f"escalation:{CLOCK}:{launch.dispatch_id}:in-process",
                f"INCIDENT-CLOCK {lane} in-process: the Orca launch of the Opus fallback {backup} failed twice: {reasons} | root: start {backup} in-process now, an Agent of type long-running:lane-ship named {backup} on opus in fast mode with the brief {brief}",
            )

    def executor_backup(self, lane: str) -> str:
        """The Opus backup an incident executor holds for a fix lane it adopted, while that backup's launch is in flight or its worker is live, or empty."""
        store = actions.Store()
        for incident in map(store.load, store.ids()):
            backup = latest(incident, "dispatch:backup")
            if backup is None or incident.facts["adopted"].get("fix") != lane:
                continue
            if backup.status in actions.OPEN or (backup.status == "verified" and (shown := self.orca.show(backup.target, backup.dispatch_id)) and shown.status not in INACTIVE):
                return backup.target
        return ""

    def clock_line(self, clock: dict) -> str:
        launched = actions.parse_stamp(clock["launched"])
        marks = sorted((actions.parse_stamp(at), name) for name, at in clock["reached"].items())
        reached = ", ".join(f"{CLOCK_MILESTONES.get(name, name)} +{whole_minutes(launched, at)} min" for at, name in marks)
        status = f"stopped: {clock['ended']}" if clock["ended"] else f"{whole_minutes(launched, self.now())} min of {self.config.incident_ready_minutes}"
        return f"- incident clock [{status}] launched {pacific(launched)}; {reached or 'no milestone yet'}"

    def render(self) -> str:
        lines = [f"# desk-runner {pacific(self.now())}", self.binding_line(self.book.load(RUNNER).facts.get(BINDING))]
        if policy := self.landing_policy():
            held = json.loads(policy.target)
            lines.append(f"landing policy: {held['rule']} at {held['revision']} ({policy.authority_ref})")
        for container in [*self.book.containers(LANE_PREFIX), LANDING]:
            incident = self.book.load(container)
            lines.append(f"## {container} owner {incident.owner} generation {incident.owner_generation}{f' pending {incident.pending_owner}' if incident.pending_owner else ''}")
            if clock := incident.facts.get(CLOCK):
                lines.append(self.clock_line(clock))
            for action in sorted(incident.actions.values(), key=lambda action: action.accepted_at):
                if action.kind in ("send", "policy"):
                    continue
                at = [f"accepted {pacific(actions.parse_stamp(action.accepted_at))}"]
                at += [f"started {pacific(actions.parse_stamp(action.started_at))}"] if action.started_at else []
                at += [f"{action.status} {pacific(actions.parse_stamp(receipt['at']))}"] if (receipt := action.verification_receipt or action.response) and "at" in receipt else []
                status = action.status
                if action.kind == "launch" and status == "accepted" and (held := self.load_refusal()):
                    status = f"HELD {held} for {int((self.now() - actions.parse_stamp(action.accepted_at)).total_seconds() // 60)}m"
                lines.append(f"- {action.action_id} {action.kind} [{status}] {' '.join(at)}")
        return "\n".join(lines) + "\n"

    def write_view(self) -> None:
        text = self.render()
        current = self.config.view.read_text() if self.config.view.is_file() else ""
        if current.split("\n", 1)[1:] != text.split("\n", 1)[1:]:
            self.config.view.parent.mkdir(parents=True, exist_ok=True)
            self.config.view.write_text(text)


def lost(incident: actions.Incident, key: str, response: dict) -> None:
    incident.lose(key)
    incident.action(key).response = response


def finish(incident: actions.Incident, key: str, response: dict, dispatch: str) -> None:
    if incident.action(key).status == "accepted":
        incident.start(key, actions.parse_stamp(response["at"]), dispatch_id=dispatch)
    incident.complete(key, response, dispatch_id=dispatch)


def verify_all(incident: actions.Incident, keys: list[str], receipt: dict) -> None:
    for key in keys:
        incident.verify(key, receipt)


def enqueue_key(prefix: list[str], verdicts: dict) -> str:
    return "enqueue:" + ",".join(f"{pr}@{verdicts[pr]['sha'] if pr in verdicts else ''}" for pr in prefix)


def enqueued_heads(key: str) -> dict[str, str]:
    return dict(entry.split("@", 1) for entry in key.split("#")[0].removeprefix("enqueue:").split(","))


def label_order(action: actions.Action) -> tuple[int, bool]:
    _, _, cycle, step = action.action_id.split(":")
    return int(cycle), step == "lift"


def numbered(prs: list[str]) -> str:
    return " ".join(f"#{pr}" for pr in prs)


def routed_blocker(detail: str, stack: set[str], gated_red: bool) -> str:
    """The segments of a gate's `BLOCKED` detail a lane must act on, with their hints; an ejection blaming a PR outside `stack` is the batch's failure, not this PR's, and is left out, and so is a failing required check when `gated_red` says only gates and the base are red."""
    segments = [segment for segment in detail.split("; ") if not foreign_failure(segment, stack) and not (gated_red and FAILING_REQUIRED.match(segment))]
    if not any(ROUTED_BLOCKER.match(segment) for segment in segments):
        return ""
    return "; ".join(segment for segment in segments if ROUTED_BLOCKER.match(segment) or BLOCKER_HINT.match(segment))


def downstack(open_rows: dict[str, dict], pr: str) -> set[str]:
    """`pr` and every open row below it, walking each base to the open row whose branch it is."""
    by_branch = {row["branch"]: number for number, row in open_rows.items() if row.get("branch")}
    chain: set[str] = set()
    below: str | None = pr
    while below is not None and below not in chain and below in open_rows:
        chain.add(below)
        below = by_branch.get(open_rows[below].get("base"))
    return chain


def foreign_failure(reason: str, stack: set[str]) -> str | None:
    """The PR a `downstack failures on PR #<n>` reason blames when `n` is outside `stack`: the queue batched the two, and its failure is not this PR's."""
    named = DOWNSTACK_FAILURE.search(reason)
    return named[1] if named and named[1] not in stack else None


def stacked(open_rows: dict[str, dict]) -> set[str]:
    """Every open row whose base is another open row's branch, so it cannot land before that parent does."""
    branches = {row["branch"] for row in open_rows.values() if row.get("branch")}
    return {pr for pr, row in open_rows.items() if row.get("base") in branches}


def under_hold(open_rows: dict[str, dict], held: list[str]) -> set[str]:
    by_branch = {row["branch"]: pr for pr, row in open_rows.items() if row.get("branch")}
    covered = set()
    for pr in open_rows:
        below = pr
        while below is not None and below not in held:
            below = by_branch.get(open_rows[below].get("base"))
        if below is not None:
            covered.add(pr)
    return covered


def names_head(named: str, sha: str) -> bool:
    return bool(sha) and (sha.startswith(named) or named.startswith(sha))


def newest_naming(grades: list[dict], sha: str) -> dict | None:
    return max((record for record in grades if any(names_head(named, sha) for named in SHORT_SHA.findall(record["text"]))), key=lambda record: record["seq"], default=None)


def names_pr(record: dict, pr: str) -> bool:
    return int(pr) in record.get("refs", {}).get("prs", []) or re.search(rf"#{pr}\b", record["text"]) is not None


def pr_name(pr: int | str) -> str:
    """A cci PR reference in prose: a bare number as `#12`, `owner/repo#12` as written."""
    return str(pr) if "#" in str(pr) else f"#{pr}"


def prefix_tops(prefix: list[str], rows: dict[str, dict]) -> list[str]:
    bases = {rows[pr].get("base") for pr in prefix}
    return [pr for pr in prefix if rows[pr].get("branch") not in bases]


def heads_query(repo: str, shas: list[str]) -> str:
    owner, name = repo.split("/")
    fields = " ".join(f'h{index}: object(expression: "{sha}") {{ ... on Commit {{ status {{ contexts {{ context state }} }} }} }}' for index, sha in enumerate(shas))
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'


def same_head(one: str, other: str) -> bool:
    return one.startswith(other) or other.startswith(one)



def release_posted(incident: actions.Incident, key: str, step: str, seq: int, at: datetime) -> None:
    if incident.action(key).status == "accepted":
        incident.start(key, at)
        incident.complete(key, {"posted": {}, "at": actions.stamp(at)})
    incident.action(key).response["posted"][step] = seq


class Landing:
    """The ready-prefix workflow: gate every tracked stack read-only, enqueue each ready prefix once per set of heads, verify by squash, route the restack above it, and send a landed merge-fast PR's release to its release owner."""

    def __init__(self, runner: Runner, config: dict):
        self.runner = runner
        self.shell = runner.shell
        self.book = runner.book
        self.repo = config["repo"]
        self.ledger = config["ledger"]
        self.checkout = Path(config["checkout"]).expanduser()
        self.holds = Path(config["holds"]).expanduser()
        self.release_owner = config["release_owner"]
        self.unlabeled: set[str] = set()
        self.landed: set[str] = set()
        self.gated: set[tuple[str, str]] = set()
        self.patch_ids: dict[tuple[str, str], str] = {}
        self.read: dict[str, tuple[datetime, list[dict]]] = {}
        self.reading = threading.Lock()
        self.book.ensure(LANDING, LANDING)

    def ledger_py(self, *argv: str) -> Done:
        return self.shell.run([sys.executable, str(SCRIPTS / "ledger.py"), "-C", str(self.checkout), *argv], env={"CLAUDE_LONG_RUNNING_DRIVE": self.runner.config.drive})

    def rows(self) -> dict[str, dict] | None:
        """Every ledger PR row by number, or None when the ledger cannot be listed: an unread ledger is not an empty one."""
        done = self.ledger_py("list", "--ledger", self.ledger, "--json")
        return {row["pr"]: row for row in json.loads(done.out)} if done.code == 0 else None

    def held(self, rows: dict[str, dict]) -> list[str] | None:
        """Every PR the holds file, a rules-review block, an open cci hold, or GitHub's `hold` label holds, from one read of the file, so the gate routes no blocker for it or for the PRs stacked above it; None when that read carries a line the desk cannot hold by, and then the caller gates and enqueues nothing."""
        entries = holds.read(self.holds)
        if self.holds_unread(entries, rows):
            return None
        numbers = holds.held(entries, rows) | {pr for pr, row in rows.items() if row.get("rules_blocked") or HOLD_LABEL in row.get("labels", "").split(",")}
        numbers |= cci.held_prs(self.runner.cci_tail, self.runner.config.drive).keys()
        return sorted(numbers, key=int)

    def holds_unread(self, entries: list[holds.Entry], rows: dict[str, dict]) -> bool:
        """Whether `entries` carry a line the desk cannot hold by, each escalated once: a line no form matches, or a lane line naming a lane no ledger row carries in any state. Such a line may hold something the desk cannot name, so nothing is gated or enqueued on that read."""
        faults = {
            entry.line: f"no form matches the holds-file line `{entry.line}`, so the desk enqueues nothing until it is fixed"
            f" | in {self.holds} write `#<pr> <why>`, `lane:<name> <why>`, `<lane> HOLD <why>`, `HOLD <lane> <why>`, `hold:all <why>`, or `release #<pr>`"
            for entry in entries
            if entry.unknown
        }
        faults |= {
            line: f"the holds-file line `{line}` names lane {', '.join(lanes)}, which no ledger row carries, so it holds nothing and the desk enqueues nothing until it is fixed"
            f" | in {self.holds} hold each PR as `#<pr> <why>`, or delete the line"
            for line, lanes in holds.rowless(entries, rows).items()
        }
        for line, text in faults.items():
            self.runner.escalate(f"holds:{hashlib.sha1(line.encode()).hexdigest()[:8]}", "HOLDS-UNREAD", "landing", text)
        return bool(faults)

    def whole(self) -> bool:
        policy = self.runner.landing_policy()
        return bool(policy) and json.loads(policy.target)["rule"] == "whole"

    def enqueue_argv(self, tips: list[str], check: bool) -> list[str]:
        whole = ["--whole"] if self.whole() else []
        return [str(self.checkout / STACK_ENQUEUE), *tips, *(["--check"] if check else []), *whole]

    def trees(self, rows: dict[str, dict]) -> list[list[str]]:
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        by_branch = {row["branch"]: pr for pr, row in open_rows.items() if row.get("branch")}
        tracked = {pr: row for pr, row in open_rows.items() if row.get("ours")}
        bases = {row.get("base") for row in tracked.values()}
        grouped: dict[str, list[str]] = {}
        for tip in sorted((pr for pr, row in tracked.items() if row.get("branch") not in bases), key=int):
            root = tip
            while (below := by_branch.get(open_rows[root].get("base"))) is not None:
                root = below
            grouped.setdefault(root, []).append(tip)
        return [grouped[root] for root in sorted(grouped, key=int)]

    def gate(self, rows: dict[str, dict], held: list[str]) -> None:
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        bases = {row.get("base") for row in open_rows.values()}
        for pr, row in open_rows.items():
            if not row.get("ours") and row.get("branch") not in bases:
                self.runner.escalate(f"not-ours:{pr}@{row.get('head', '')}", "NOT-OURS", f"#{pr}", f"skipped: no drive lane registered it or posted it opened on cci, so the desk never gates or enqueues it; the lane that opened it runs ledger.py register --lane <lane> --pr {pr}")
        trees = [[tip] for tree in self.trees(rows) for tip in tree] if self.whole() else self.trees(rows)
        covered = under_hold(open_rows, held)
        children = stacked(open_rows)
        waiting = under_hold(open_rows, sorted(self.unlabeled))
        trees = [kept for tree in trees if (kept := [tip for tip in tree if tip not in waiting])]
        with ThreadPoolExecutor(GATE_WORKERS) as pool:
            gated = [pair for pairs in pool.map(self.proven_stacks, trees) for pair in pairs]
        for tips, done in gated:
            verdicts = {match["pr"]: match for match in VERDICT_LINE.finditer(done.out)}
            if done.code and not verdicts:
                last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
                self.runner.escalate(f"gate:{','.join(tips)}:{hashlib.sha1(last[0].encode()).hexdigest()[:8]}", "GATE-FAILED", numbered(tips), f"stack-enqueue --check exited {done.code} with no verdict: {last[0]}")
                continue
            if would := WOULD_ENQUEUE.search(done.out):
                self.accept(tips, [number.lstrip("#") for number in would.group(1).split()], verdicts, rows)
            for pr, match in verdicts.items():
                if match["verdict"] == "BLOCKED" and pr not in rows and UNNAMED_RIDER in match["detail"]:
                    self.runner.escalate(f"unnamed:{pr}@{match['sha']}", "FOREIGN", numbered(tips), f"#{pr} would ride the batch, and no drive lane registered it or posted it opened on cci; nothing was enqueued; the lane that opened it runs ledger.py register --lane <lane> --pr {pr}")
                elif match["verdict"] == "BLOCKED" and pr in rows and pr not in covered:
                    stack = set(verdicts) | downstack(open_rows, pr)
                    gated_red = pr in children and any(FAILING_REQUIRED.match(segment) for segment in match["detail"].split("; ")) and self.gated_red(pr, match["sha"])
                    if blocker := routed_blocker(match["detail"], stack, gated_red):
                        self.route_blocker(pr, match["sha"], blocker, rows[pr], tips, stack, gated_red)

    def gated_red(self, pr: str, sha: str) -> bool:
        """Whether a stacked PR's failing required check comes only from a gate status or from its unlanded base: no status but a gate's is red on its own head. A status read that fails counts as gated for this pass, and the next pass reads again."""
        statuses = self.head_statuses(pr, sha)
        return statuses is None or not any(status["state"] in RED_STATES and status["context"] not in GATE_STATUSES for status in statuses)

    def proven_stacks(self, tips: list[str]) -> list[tuple[list[str], Done]]:
        gated = []
        while tips:
            first, *rest = tips
            alone = self.checked([first])
            members = {match["pr"] for match in VERDICT_LINE.finditer(alone.out)}
            shared = [first, *(tip for tip in rest if tip in members)]
            joined = alone if len(shared) == 1 else self.checked(shared)
            if joined.code and not VERDICT_LINE.search(joined.out):
                gated += [([first], alone), *(([tip], self.checked([tip])) for tip in shared[1:])]
            else:
                gated.append((shared, joined))
            tips = [tip for tip in rest if tip not in members]
        return gated

    def checked(self, tips: list[str]) -> Done:
        return self.enqueue_run(tips, check=True)

    def enqueue_run(self, tips: list[str], check: bool) -> Done:
        """Run stack-enqueue, repeating it after a pause while it exits with a transient network failure and no verdict; a verdict or any other failure returns at once."""
        argv = self.enqueue_argv(tips, check)
        for attempt in range(TRANSIENT_ATTEMPTS):
            if attempt:
                self.shell.sleep(TRANSIENT_PAUSE_SECONDS)
            done = self.shell.run(argv)
            if done.code != ENQUEUE_FAILED or VERDICT_LINE.search(done.out) or not TRANSIENT_FAILURE.search(done.err + done.out):
                break
        return done

    @property
    def fidelity_lanes(self) -> list[str]:
        configured = json.loads(self.runner.config.source.read_text())["landing"]["fidelity_lane"]
        return [configured] if isinstance(configured, str) else list(configured)

    @property
    def fidelity_names(self) -> str:
        return " or ".join(self.fidelity_lanes)

    def fidelity(self, pr: str, sha: str, base: str) -> dict | None:
        """The newest grade naming `sha`; with none, an ok carried from an older head whose own newest grade is ok and whose patch against `base` is identical to this head's."""
        drive = self.runner.config.drive
        lanes = self.fidelity_lanes
        reviews = [record for lane in lanes for record in cci.records(self.runner.cci_tail, drive, "--kind=review", "--lane", lane, "--pr", pr)]
        grades = [record for record in reviews if record["kind"] == "review" and record["drive"] == drive and record["lane"] in lanes and record["text"].startswith(FIDELITY_GRADES)]
        return newest_naming(grades, sha) or (self.carried(pr, sha, base, grades) if sha and base else None)

    def carried(self, pr: str, sha: str, base: str, grades: list[dict]) -> dict | None:
        graded = dict.fromkeys(
            named
            for record in sorted(grades, key=lambda record: record["seq"], reverse=True)
            if record["text"].startswith(FIDELITY_OK)
            for named in SHORT_SHA.findall(record["text"])
            if not names_head(named, sha)
        )
        oks = {named: grade for named in graded if (grade := newest_naming(grades, named))["text"].startswith(FIDELITY_OK)}
        if not oks or not (ours := self.patch_id(pr, sha, base, escalate=True)):
            return None
        for named, grade in oks.items():
            if self.patch_id(pr, named, base, escalate=False) == ours:
                return {**grade, "text": f"{FIDELITY_OK} at {sha}, patch-identical to {named}"}
        return None

    def patch_id(self, pr: str, sha: str, base: str, escalate: bool) -> str:
        """`git patch-id --stable` of `sha`'s diff against its merge base with `base`, read from GitHub's compare; empty when GitHub refuses the read, which escalates once for the PR's own head and is silent for a short sha a grade names that is no commit."""
        if (base, sha) in self.patch_ids:
            return self.patch_ids[(base, sha)]
        diff = self.shell.run(["gh", "api", f"repos/{self.repo}/compare/{base}...{sha}", "-H", DIFF_MEDIA])
        if diff.code != 0:
            if escalate:
                last = (diff.err or diff.out).strip().splitlines()[-1:] or [f"exit {diff.code}"]
                self.runner.escalate(f"patch:{pr}:{sha}", "PATCH-READ-FAILED", f"#{pr}", f"GitHub refused the diff of {base}...{sha}: {last[0]}; no fidelity ok carries to this head until it reads, and the runner reads it again on the next pass")
            return ""
        done = self.shell.run(["git", "patch-id", "--stable"], stdin=diff.out)
        self.patch_ids[(base, sha)] = done.out.split()[0] if done.code == 0 and done.out.strip() else ""
        return self.patch_ids[(base, sha)]

    def head_statuses(self, pr: str, sha: str) -> list[dict] | None:
        return self.read_heads({sha: pr}).get(sha)

    def cached(self, sha: str) -> list[dict] | None:
        now = self.runner.now()
        with self.reading:
            return next((statuses for head, (at, statuses) in self.read.items() if same_head(head, sha) and now - at < HEAD_FLOOR), None)

    def forget(self, sha: str) -> None:
        with self.reading:
            for head in [head for head in self.read if same_head(head, sha)]:
                del self.read[head]

    def read_heads(self, heads: dict[str, str]) -> dict[str, list[dict] | None]:
        """The commit statuses of each head in `heads`, a sha to the PR it heads: one GraphQL query per `HEADS_PER_QUERY` heads not read in the last `HEAD_FLOOR`. A head GitHub refuses or does not know reads None and logs one `STATUS-READ-FAILED` per PR and head."""
        found = {sha: statuses for sha in heads if (statuses := self.cached(sha)) is not None}
        due = sorted(set(heads) - set(found))
        for start in range(0, len(due), HEADS_PER_QUERY):
            batch = due[start : start + HEADS_PER_QUERY]
            done = self.shell.run(["gh", "api", "graphql", "-f", f"query={heads_query(self.repo, batch)}"])
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            commits = json.loads(done.out)["data"]["repository"] if done.code == 0 else {}
            now = self.runner.now()
            for index, sha in enumerate(batch):
                if (commit := commits.get(f"h{index}")) is None:
                    why = f"GitHub refused the statuses of {sha}: {last[0]}" if done.code else f"GitHub knows no commit {sha} in {self.repo}"
                    self.runner.escalate(f"status-read:{heads[sha]}:{sha}", "STATUS-READ-FAILED", f"#{heads[sha]}", f"{why}; the runner reads it again on the next pass")
                    continue
                statuses = [{"context": context["context"], "state": context["state"].lower()} for context in (commit["status"] or {"contexts": []})["contexts"]]
                with self.reading:
                    self.read[sha] = (now, statuses)
                found[sha] = statuses
        return found

    def quota_low(self) -> bool:
        """Whether the REST or GraphQL quota of the installation this desk reads GitHub through is under `QUOTA_FLOOR` of its limit, from one `rate_limit` read GitHub does not count. Each quota under the floor logs one `GITHUB-BACKOFF` per reset; a read that fails logs one `QUOTA-READ-FAILED` per hour and reads as not low."""
        done = self.shell.run(["ccx", "vcs", "gh", "--", "api", "rate_limit"], env={"GH_REPO": self.repo})
        if done.code != 0:
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            self.runner.escalate(f"quota-read:{self.runner.now():%Y%m%d%H}", "QUOTA-READ-FAILED", "landing", f"the GitHub quota read failed: {last[0]}; the pass runs without it")
            return False
        resources = json.loads(done.out)["resources"]
        low = {name: resources[name] for name in QUOTA_RESOURCES if resources[name]["remaining"] < resources[name]["limit"] * QUOTA_FLOOR}
        for name, quota in low.items():
            reset = pacific(datetime.fromtimestamp(quota["reset"], timezone.utc))
            self.runner.escalate(
                f"github-backoff:{name}:{quota['reset']}",
                "GITHUB-BACKOFF",
                "landing",
                f"GitHub's {name} quota has {quota['remaining']} of {quota['limit']} calls left, under the {QUOTA_FLOOR:.0%} floor; the desk reads nothing from GitHub until it resets at {reset}, and accepted enqueues and root go records run after the reset",
            )
        return bool(low)

    def fidelity_statuses(self, pr: str, sha: str) -> list[dict] | None:
        statuses = self.head_statuses(pr, sha)
        return None if statuses is None else [status for status in statuses if status["context"] == FIDELITY_STATUS]

    def status_success(self, pr: str, sha: str) -> bool:
        return any(status["state"] == "success" for status in self.fidelity_statuses(pr, sha) or [])

    def queue_drafts(self) -> list[dict] | None:
        """The open Graphite queue drafts, or None when GitHub refuses the listing, logged as one `GATES-LIST-FAILED` per hour."""
        listed = self.shell.run(["gh", "pr", "list", "--repo", self.repo, "--state", "open", "--search", f'"{QUEUE_DRAFT_TITLE}" in:title', "--json", "number,title,headRefOid,createdAt", "--limit", "100"])
        if listed.code != 0:
            last = (listed.err or listed.out).strip().splitlines()[-1:] or [f"exit {listed.code}"]
            self.runner.escalate(f"gates-list:{self.runner.now():%Y%m%d%H}", "GATES-LIST-FAILED", "landing", f"GitHub refused the queue draft listing: {last[0]}; the runner reads it again on the next pass")
            return None
        return json.loads(listed.out)

    def regrade_gates(self, drafts: list[dict]) -> None:
        """Re-dispatch the Landing Gates grade of an open Graphite queue draft whose head still carries no `landing-gates` status `GATES_GRACE` after the draft opened, once per head, and post one `GATES-REDISPATCH` state line. A head still without the status `GATES_GRACE` after that dispatch posts one `LANDING-GATES-FAILED` defect. The `pull_request_target` run reads success when it only dispatches the grade, so a grade that died on a GitHub fault leaves the draft blocked with every run green."""
        now = self.runner.now()
        for draft in drafts:
            pr, sha = str(draft["number"]), draft["headRefOid"]
            opened = datetime.fromisoformat(draft["createdAt"].replace("Z", "+00:00"))
            if not draft["title"].startswith(QUEUE_DRAFT_TITLE) or (pr, sha) in self.gated or now - opened < GATES_GRACE:
                continue
            statuses = self.head_statuses(pr, sha)
            if statuses is None:
                continue
            if any(status["context"] == GATES_STATUS for status in statuses):
                self.gated.add((pr, sha))
            else:
                self.redispatch_gates(pr, sha, now)

    def redispatch_gates(self, pr: str, sha: str, now: datetime) -> None:
        key = f"gates:{pr}:{sha}"
        action = self.book.known(LANDING, key) or self.book.accept(LANDING, key, "dispatch", pr, "runner", None)[0]
        if action.status == "completed":
            dispatched = actions.parse_stamp(action.response["at"])
            if now - dispatched >= GATES_GRACE:
                self.runner.escalate(f"gates-missing:{pr}:{sha}", "LANDING-GATES-FAILED", f"#{pr}", f"queue draft head {sha} still has no `{GATES_STATUS}` status {whole_minutes(dispatched, now)} min after the runner re-dispatched its grade; read the log of the {GATES_WORKFLOW} dispatch run for pr={pr} head={sha}")
            return
        done = self.shell.run(["gh", "workflow", "run", GATES_WORKFLOW, "--repo", self.repo, "-f", f"pr={pr}", "-f", f"head={sha}"])
        self.forget(sha)
        if done.code != 0:
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            self.runner.escalate(key, "GATES-DISPATCH-FAILED", f"#{pr}", f"GitHub refused the {GATES_WORKFLOW} dispatch for {sha}: {last[0]}; the runner dispatches it again on the next pass")
            return
        self.book.attempt(LANDING, lambda incident: (incident.start(key, now), incident.complete(key, {"at": self.book.stamp()})))
        self.runner.escalate(f"gates-redispatch:{pr}:{sha}", "GATES-REDISPATCH", f"#{pr}", f"queue draft head {sha} had no `{GATES_STATUS}` status {int(GATES_GRACE.total_seconds() // 60)} min after the draft opened; re-dispatched {GATES_WORKFLOW} for it once")

    def ungraded(self, heads: dict[str, str], rows: dict[str, dict]) -> dict[str, dict | None]:
        grades = {pr: self.fidelity(pr, sha, rows[pr].get("base") or "") for pr, sha in heads.items()}
        return {
            pr: grade
            for pr, grade in grades.items()
            if not (grade and grade["text"].startswith(FIDELITY_OK)) and not (grade is None and self.status_success(pr, heads[pr]))
        }

    def refuse_ungraded(self, heads: dict[str, str], ungraded: dict[str, dict | None], rows: dict[str, dict]) -> None:
        for pr, grade in ungraded.items():
            key = f"fidelity:{pr}:{heads[pr]}:{grade['seq'] if grade else 'ungraded'}"
            if self.book.known(LANDING, key):
                continue
            why = f"{grade['lane']} graded this head on cci #{grade['seq']}: {grade['text']}" if grade else f"no review from {self.fidelity_names} starting `{FIDELITY_OK}` names this head, and its `{FIDELITY_STATUS}` status is not success"
            owner = self.owner(pr, rows[pr])
            to = ",".join([*(self.reach(owner) if owner else []), *self.fidelity_lanes])
            self.post_blocker(key, to, f"#{pr} {heads[pr]}: nothing in {numbered(list(heads))} was enqueued; {why}", pr)

    def mirror_grades(self, rows: dict[str, dict], entries: list[holds.Entry]) -> None:
        """Mirror the fidelity lane's newest grade of each open row's head into that head's `fidelity` commit status, success for `fidelity: ok` and failure for `fidelity: missing`, so the `landing-gates` check reads the grade off the head. A head with no grade reads pending until the fidelity lane grades it, unless the head already carries a `fidelity` status, read just before the post, or that read fails. A grade whose state the head already carries posts nothing. A PR in a believed-queued prefix gets no post once the runner has posted a status on its head, so `landing-gates` can still pass a head enqueued before its first post. A pass posts at most `MIRROR_WRITES` statuses, since each starts a `landing-gates` run on the repo's Actions token: grades for heads the runner has posted no status on first, then grades that flip a head's status, then `pending`, each tier with heads of PRs a `release` line names first, then first posts before retries, then by PR number, and leaves the rest to later passes; a post GitHub refuses stays accepted and is retried. A cci read that fails ends the mirroring for that pass and logs `CCI-TAIL-FAILED`; the rest of the pass runs."""
        posted: dict[tuple[str, str], tuple[str, int, str]] = {}
        for action in self.book.actions(LANDING, kind="status", status="completed"):
            _, pr, head, grade_ref = action.action_id.split(":", 3)
            latest = (action.response["at"], int(grade_ref) if grade_ref.isdigit() else -1, action.response["state"])
            posted[(pr, head)] = max(posted.get((pr, head), latest), latest)
        released = holds.released(entries, rows)
        queued = {pr for prs in self.believed_queued(rows).values() for pr in prs}
        due = []
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            head = row.get("head", "")
            if row.get("state", "open") != "open" or not head or (pr in queued and (pr, head) in posted):
                continue
            try:
                grade = self.fidelity(pr, head, row.get("base") or "")
            except CciTailFailed as failure:
                self.runner.tail_failed(failure)
                return
            if grade:
                key, source, state = f"status:{pr}:{head}:{grade['seq']}", f"cci #{grade['seq']}", "success" if grade["text"].startswith(FIDELITY_OK) else "failure"
                description = f"cci #{grade['seq']} {grade['text'].splitlines()[0]}"
            else:
                key, source, state = f"status:{pr}:{head}:ungraded", "ungraded", "pending"
                description = f"awaiting {self.fidelity_names}'s grade of this head"
            known = self.book.known(LANDING, key)
            if (known and known.status != "accepted") or (pr, head) in posted and posted[(pr, head)][2] == state:
                continue
            need = 2 if state == "pending" else int((pr, head) in posted)
            due.append(((need, pr not in released, known is not None, int(pr)), pr, head, key, source, state, description))
        writes = 0
        for _, pr, head, key, source, state, description in sorted(due, key=lambda item: item[0]):
            if writes == MIRROR_WRITES:
                return
            if state == "pending":
                self.forget(head)
                present = self.fidelity_statuses(pr, head)
                if present is None or present:
                    continue
            writes += 1
            self.book.accept(LANDING, key, "status", pr, source, None)
            payload = {
                "state": state,
                "context": FIDELITY_STATUS,
                "description": description if len(description) <= STATUS_TEXT else description[: STATUS_TEXT - 1] + "…",
            }
            done = self.shell.run(["gh", "api", f"repos/{self.repo}/statuses/{head}", "--method", "POST", "--input", "-"], stdin=json.dumps(payload))
            self.forget(head)
            if done.code == 0:
                self.book.attempt(LANDING, lambda incident, key=key, payload=payload: (incident.start(key, self.runner.now()), incident.complete(key, {**payload, "at": self.book.stamp()})))
            else:
                last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
                self.runner.escalate(key, "STATUS-FAILED", f"#{pr}", f"GitHub refused the `{FIDELITY_STATUS}` commit status on {head}: {last[0]}; the runner posts it again on the next pass")

    def mirror_holds(self, rows: dict[str, dict], entries: list[holds.Entry]) -> None:
        """Add the `hold` label to every open row a hold names and lift the labels the runner added once nothing holds the row. A label the runner added that someone removed from GitHub lifts the row's holds-file lines first, through `lift_removed`. A pass writes at most `MIRROR_WRITES` labels, first writes before retries; a held row whose label is still unwritten stays in `unlabeled`, and the gate leaves it and every PR stacked above it out of that pass."""
        try:
            records = set(cci.held_prs(self.runner.cci_tail, self.runner.config.drive))
        except CciTailFailed as failure:
            self.runner.tail_failed(failure)
            return
        wanted = holds.held(self.lift_removed(rows, entries, records), rows) | records
        labels = self.label_actions()
        due = []
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            latest = max(labels.get(pr, []), key=label_order, default=None)
            cycle, lifting = label_order(latest) if latest else (0, False)
            applied = latest is not None and (latest.status == "completed") != lifting
            if row.get("state", "open") != "open" or (pr in wanted) == applied or (not applied and HOLD_LABEL in row.get("labels", "").split(",")):
                continue
            retrying = latest is not None and latest.status == "accepted"
            key = latest.action_id if retrying else f"hold:{pr}:{cycle if applied else cycle + 1}:{'lift' if applied else 'add'}"
            due.append((retrying, pr, key, applied))
        self.unlabeled = {pr for _, pr, _, applied in due if not applied}
        for _, pr, key, applied in sorted(due, key=lambda item: item[0])[:MIRROR_WRITES]:
            self.book.accept(LANDING, key, "label", pr, str(self.holds), None)
            if applied:
                done = self.shell.run(["gh", "api", f"repos/{self.repo}/issues/{pr}/labels/{HOLD_LABEL}", "--method", "DELETE"])
            else:
                done = self.shell.run(["gh", "api", f"repos/{self.repo}/issues/{pr}/labels", "--method", "POST", "--input", "-"], stdin=json.dumps({"labels": [HOLD_LABEL]}))
            if done.code == 0 or (applied and LABEL_GONE in done.err):
                self.book.attempt(LANDING, lambda incident, key=key: (incident.start(key, self.runner.now()), incident.complete(key, {"at": self.book.stamp()})))
                self.unlabeled.discard(pr)
            else:
                last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
                self.runner.escalate(key, "LABEL-FAILED", f"#{pr}", f"GitHub refused to {'remove' if applied else 'add'} the `{HOLD_LABEL}` label: {last[0]}; the runner tries again on the next pass")

    def label_actions(self) -> dict[str, list[actions.Action]]:
        labels: dict[str, list[actions.Action]] = {}
        for action in self.book.actions(LANDING, kind="label"):
            labels.setdefault(action.target, []).append(action)
        return labels

    def lift_removed(self, rows: dict[str, dict], entries: list[holds.Entry], records: set[str]) -> list[holds.Entry]:
        """Take a `hold` label someone removed from GitHub as the lift of the PR's `#<n>` holds-file lines, when nothing else holds the PR: those lines leave the file through `holds.py`, the label is never put back, and `HOLD-LIFTED` tells the root. The label is on from the runner's own add, or from the first ledger refresh that read it on a row the file holds; a later refresh that reads the row without it is the removal, which also settles the runner's own add as lifted, so a PR another hold still holds gets the label again. A line `holds.py` cannot split logs `LIFT-FAILED` and stays. Returns the entries the file holds after."""
        seen = self.book.load(LANDING).facts.get(LABEL_SEEN, {})
        labels = self.label_actions()
        file_held = holds.held(entries, rows)
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if row.get("state", "open") != "open" or pr not in file_held:
                continue
            latest = max(labels.get(pr, []), key=label_order, default=None)
            added = latest is not None and latest.status == "completed" and not label_order(latest)[1]
            refreshed = row.get("last_refresh", "")
            if HOLD_LABEL in row.get("labels", "").split(","):
                if not added and pr not in seen and refreshed:
                    self.book.edit(LANDING, lambda incident, pr=pr, at=refreshed: incident.facts.setdefault(LABEL_SEEN, {}).update({pr: at}))
                continue
            since = latest.response["at"] if added else seen.get(pr, "")
            if pr in seen:
                self.book.edit(LANDING, lambda incident, pr=pr: incident.facts.get(LABEL_SEEN, {}).pop(pr, None))
            if not since or refreshed <= since:
                continue
            if added:
                key = f"hold:{pr}:{label_order(latest)[0]}:lift"
                self.book.accept(LANDING, key, "label", pr, "github", None)
                self.book.attempt(LANDING, lambda incident, key=key: (incident.start(key, self.runner.now()), incident.complete(key, {"at": self.book.stamp(), "by": "github"})))
            try:
                kept = holds.removing(entries, "pr", pr)
            except holds.Refused as refusal:
                self.runner.escalate(f"lift:{pr}:{since}", "LIFT-FAILED", f"#{pr}", f"the `{HOLD_LABEL}` label on since {since} is gone from GitHub, but {refusal}; the runner puts the label back while the line holds the PR")
                continue
            if pr in holds.held(kept, rows) | records:
                continue
            lines = ", ".join(f"`{entry.line}`" for entry in holds.holding(entries, "pr", pr))
            holds.write(self.holds, kept)
            entries, file_held = kept, holds.held(kept, rows)
            self.runner.escalate(f"hold-lifted:{pr}:{since}", "HOLD-LIFTED", f"#{pr}", f"the `{HOLD_LABEL}` label on since {since} is gone from GitHub, so the runner took that as the lift and removed {lines} from {self.holds}")
        return entries

    def accept(self, tips: list[str], prefix: list[str], verdicts: dict[str, re.Match], rows: dict[str, dict]) -> None:
        """One enqueue per exact set of prefix heads, every one a PR a drive lane opened and the fidelity lane graded ok at that head; a fresh attempt only after every earlier one enqueued nothing."""
        base = enqueue_key(prefix, verdicts)
        if foreign := [pr for pr in prefix if not rows.get(pr, {}).get("ours")]:
            self.runner.escalate(f"foreign:{base}", "FOREIGN", numbered(tips), f"stack-enqueue would also enqueue {', '.join('#' + pr for pr in foreign)}, which no drive lane registered or posted opened; nothing was enqueued")
            return
        enqueues = self.book.actions(LANDING, kind="enqueue")
        ejected = {pr: sha for action in enqueues if action.response for pr, sha in action.response.get("ejected", {}).items()}
        if any(sha and ejected.get(pr) == sha for pr, sha in enqueued_heads(base).items()):
            return
        if self.awaiting_go(enqueues, enqueued_heads(base)):
            return
        attempts = [action for action in enqueues if action.action_id.split("#")[0] == base]
        if any(action.status != "completed" or action.response["outcome"] not in RETRYABLE for action in attempts):
            return
        heads = enqueued_heads(base)
        if ungraded := self.ungraded(heads, rows):
            self.refuse_ungraded(heads, ungraded, rows)
            return
        policy = self.runner.landing_policy()
        authority = f"{policy.authority_ref} {json.loads(policy.target)['revision']}" if policy else ""
        self.book.accept(LANDING, f"{base}#{len(attempts) + 1}", "enqueue", ",".join(prefix), authority, self.runner.now() + timedelta(minutes=self.runner.config.enqueue_minutes))

    def awaiting_go(self, enqueues: list[actions.Action], heads: dict[str, str]) -> bool:
        """Whether a head in `heads` the queue dropped for a failure outside its stack still waits for a root `go` record naming its PR, posted after the newest such drop."""
        dropped: dict[str, datetime] = {}
        for action in enqueues:
            for pr, sha in (action.response or {}).get("awaiting_go", {}).items():
                if sha and heads.get(pr) == sha:
                    at = actions.parse_stamp(action.response["dropped_at"])
                    dropped[pr] = max(dropped.get(pr, at), at)
        if not dropped:
            return False
        drive = self.runner.config.drive
        gos = [record for record in cci.records(self.runner.cci_tail, drive, "--lane", ROOT, "--kind=go") if (record["kind"], record["lane"], record["drive"]) == ("go", ROOT, drive)]
        return any(not any(actions.parse_stamp(record["at"]) > at and names_pr(record, pr) for record in gos) for pr, at in dropped.items())

    def enqueue(self) -> None:
        accepted = self.book.actions(LANDING, kind="enqueue", status="accepted")
        with ThreadPoolExecutor(max(1, len(accepted))) as pool:
            list(pool.map(self.enqueue_one, accepted))

    def enqueue_one(self, action: actions.Action) -> None:
        key = action.action_id
        if not self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        prefix = action.target.split(",")
        rows = self.rows() or {}
        if any(not rows.get(pr, {}).get("ours") for pr in prefix):
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "foreign", "out": "", "at": self.book.stamp()}))
            return
        if self.held(rows) is None:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "blocked", "out": f"{self.holds} carries a line the desk cannot hold by", "at": self.book.stamp()}))
            return
        accepted_at = actions.parse_stamp(action.accepted_at)
        if standing := self.holding(prefix, rows, accepted_at):
            self.refuse(key, prefix, standing)
            return
        tops = prefix_tops(prefix, rows)
        fresh = self.enqueue_run(tops, check=True).out
        would = WOULD_ENQUEUE.search(fresh)
        current = enqueue_key([number.lstrip("#") for number in would.group(1).split()], {match["pr"]: match for match in VERDICT_LINE.finditer(fresh)}) if would else ""
        if current != key.split("#")[0]:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "superseded", "out": fresh[-800:], "at": self.book.stamp()}))
            return
        if ungraded := self.ungraded(enqueued_heads(key), rows):
            self.refuse_ungraded(enqueued_heads(key), ungraded, rows)
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "blocked", "out": f"no {FIDELITY_OK} from {self.fidelity_names} at the head of {numbered(list(ungraded))}", "at": self.book.stamp()}))
            return
        done = self.enqueue_run(tops, check=False)
        outcome = ENQUEUE_OUTCOMES.get(done.code, "failed")
        if outcome == "enqueued" and "enqueue #" not in done.out:
            outcome = "noop"
        out = done.out[-1500:]
        if outcome in BELIEVED_QUEUED and (late := self.holding(prefix, rows, accepted_at)):
            outcome, out = self.dequeue(key, prefix, rows, late)
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": outcome, "out": out, "at": self.book.stamp()}))
        if outcome in ("stranded", "failed", "unsettled"):
            last = (done.out or done.err).strip().splitlines()
            self.runner.escalate(key, f"ENQUEUE-{outcome.upper()}", numbered(tops), last[-1] if last else f"exit {done.code}")

    def holding(self, prefix: list[str], rows: dict[str, dict], accepted_at: datetime) -> dict[str, str]:
        """Why each PR of `prefix` may not be in the queue right now, read fresh: a holds-file line, the `hold` label on GitHub, an open cci `hold` record, a `hold` record posted after this enqueue was accepted, or a `hold` record newer than the PR's newest `ready` record. A label read GitHub refuses holds the PR too."""
        entries = holds.read(self.holds)
        held = holds.held(entries, rows)
        why = {}
        for pr in prefix:
            if pr in held:
                line = next(entry.line for entry in entries if pr in holds.held([entry], rows))
                why[pr] = f"the holds-file line `{line}`"
            elif reason := self.label_hold(pr) or self.record_hold(pr, accepted_at):
                why[pr] = reason
        return why

    def label_hold(self, pr: str) -> str:
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "labels"])
        if done.code != 0:
            return f"GitHub refused the label read: {(done.err or done.out).strip()[:120]}"
        return f"the `{HOLD_LABEL}` label on GitHub" if any(label["name"] == HOLD_LABEL for label in json.loads(done.out)["labels"]) else ""

    def record_hold(self, pr: str, accepted_at: datetime) -> str:
        drive = self.runner.config.drive

        def about_pr(kind: str) -> list[dict]:
            found = cci.records(self.runner.cci_tail, drive, f"--kind={kind}", "--pr", pr)
            return [record for record in found if (record["kind"], record["drive"]) == (kind, drive) and pr in map(str, record["refs"].get("prs", []))]

        if not (posted := about_pr("hold")):
            return ""
        newest = posted[-1]
        if opened := [record for record in posted if record["status"] == "open"]:
            return f"open cci hold #{opened[-1]['seq']}"
        if actions.parse_stamp(newest["at"]) > accepted_at:
            return f"cci hold #{newest['seq']} was posted after this enqueue was accepted"
        if (readied := about_pr("ready")) and readied[-1]["seq"] < newest["seq"]:
            return f"cci ready #{readied[-1]['seq']} is older than cci hold #{newest['seq']}; a ready record after the hold lets it through"
        return ""

    def refuse(self, key: str, prefix: list[str], standing: dict[str, str]) -> None:
        """Complete an enqueue as `refused` and log one `REFUSED` line naming each hold, once per set of heads and holds."""
        why = "; ".join(f"#{pr} is held by {reason}" for pr, reason in standing.items())
        text = f"enqueue of {numbered(prefix)} refused: {why}; it goes through once nothing holds them and no hold is newer than their ready record"
        self.runner.escalate(f"refused:{key.split('#')[0]}:{hashlib.sha1(why.encode()).hexdigest()[:8]}", "REFUSED", numbered(prefix), text)
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "refused", "out": text, "at": self.book.stamp()}))

    def dequeue(self, key: str, prefix: list[str], rows: dict[str, dict], late: dict[str, str]) -> tuple[str, str]:
        """Take a just-enqueued prefix back out of the queue after a hold appeared during the call: every held PR and every PR stacked above one, top first, through `stack-enqueue --dequeue`. Returns the outcome, `dequeued` once the queue let go of all of them and `held-queued` otherwise, and its text."""
        covered = under_hold({pr: rows[pr] for pr in prefix}, list(late))
        why = "; ".join(f"#{pr} is held by {reason}" for pr, reason in late.items())
        failed = []
        for pr in reversed(prefix):
            if pr in covered and (done := self.shell.run([str(self.checkout / STACK_ENQUEUE), pr, "--dequeue"])).code not in DEQUEUE_RELEASED:
                failed.append(f"#{pr}: {((done.err or done.out).strip().splitlines() or [f'exit {done.code}'])[-1]}")
        if failed:
            text = f"a hold appeared while {numbered(prefix)} was being enqueued ({why}); the dequeue failed for {'; '.join(failed)}"
            self.runner.escalate(f"dequeue-failed:{key}", "DEQUEUE-FAILED", numbered(prefix), text)
            return "held-queued", text
        text = f"a hold appeared while {numbered(prefix)} was being enqueued ({why}); {numbered([pr for pr in prefix if pr in covered])} taken back out of the queue"
        self.runner.escalate(f"dequeued:{key}", "DEQUEUED", numbered(prefix), text)
        return "dequeued", text

    def reconcile(self) -> None:
        """An enqueue whose response was lost: Graphite's own status decides; a partial queue is unverifiable and nothing is re-enqueued."""
        cutoff = self.runner.now() - timedelta(minutes=self.runner.config.enqueue_minutes)
        for action in self.book.actions(LANDING, kind="enqueue", status="started"):
            if actions.parse_stamp(action.started_at) < cutoff:
                self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.lose(key))
        for action in self.book.actions(LANDING, kind="enqueue", status="unverifiable"):
            prefix = action.target.split(",")
            done = self.shell.run([str(self.checkout / STACK_ENQUEUE), *prefix, "--status"])
            statuses = {match["pr"]: match["status"] for match in STATUS_LINE.finditer(done.out)}
            if done.code != 0 or set(statuses) != set(prefix):
                continue
            queued = [pr for pr in prefix if statuses[pr] in QUEUED]
            key = action.action_id
            if len(queued) == len(prefix):
                self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "enqueued", "out": "reconciled from Graphite status", "at": self.book.stamp()}))
            else:
                self.runner.escalate(key, "UNVERIFIABLE", f"#{prefix[-1]}", f"Graphite holds {', '.join('#' + pr for pr in queued) or 'none'} of {', '.join('#' + pr for pr in prefix)}; nothing was re-enqueued")

    def verify(self, rows: dict[str, dict]) -> None:
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            prefix = action.target.split(",")
            if action.response["outcome"] not in ADMITTED or not all(rows.get(pr, {}).get("state") == "landed" for pr in prefix):
                continue
            receipt = {"landed": {pr: rows[pr].get("landed_sha", "") for pr in prefix}, "at": self.book.stamp()}
            self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.verify(key, receipt))
        for action in self.book.actions(LANDING, kind="enqueue", status="verified"):
            top = action.target.split(",")[-1]
            self.restack(top, rows)

    def releases(self, rows: dict[str, dict]) -> None:
        """A PR that lands carrying the merge-fast label and a `Release-With:` line sends the release owner and the root one `release` record in the pass that reads the landing, then one `blocker` each `deadlines.release_start_minutes` until a `release` record names the landing or the root replies. A landing older than the first pass is history."""
        desk = self.book.load(LANDING)
        since = desk.facts.get(RELEASE_SINCE)
        if since is None:
            self.book.edit(LANDING, lambda incident: incident.facts.update({RELEASE_SINCE: self.book.stamp()}))
            return
        for pr, row in rows.items():
            if row.get("state") == "landed" and MERGE_FAST in row.get("labels", "").split(",") and row.get("landed_at", "") >= since and f"release:{pr}" not in desk.actions:
                self.accept_release(pr, row)
        for action in self.book.actions(LANDING, kind="release"):
            if action.status in ("accepted", "completed"):
                self.press_release(action)

    def accept_release(self, pr: str, row: dict) -> None:
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "body"])
        if done.code != 0:
            return
        components = list(dict.fromkeys(name for line in RELEASE_WITH.findall(json.loads(done.out)["body"]) for name in line.split()))
        key = f"release:{pr}"
        self.book.accept(LANDING, key, "release", json.dumps({"pr": pr, "sha": row["landed_sha"], "components": components, "landed_at": row["landed_at"]}), f"#{pr} {MERGE_FAST}", None)
        if not components:
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"at": self.book.stamp()}))

    def press_release(self, action: actions.Action) -> None:
        """Post one record per `deadlines.release_start_minutes` step since the landing while no one has started or acknowledged its release: the `release` record first, then a `blocker` with the minutes elapsed."""
        landed, key, posted = json.loads(action.target), action.action_id, (action.response or {}).get("posted", {})
        if settled := self.release_started(landed) or (posted and self.acknowledged(set(posted.values()))):
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"cci": settled["seq"], "at": self.book.stamp()}))
            return
        pr, named, every = landed["pr"], " ".join(landed["components"]), self.runner.config.release_start_minutes
        minutes = int((self.runner.now() - actions.parse_stamp(landed["landed_at"])).total_seconds() // 60)
        step = str(max(0, minutes // every))
        if step in posted:
            return
        if posted:
            kind, text = "blocker", f"#{pr} {landed['sha'][:10]}: landed {minutes} minutes ago with {MERGE_FAST} and Release-With: {named}, and no cci `release` record since targets {named} or names #{pr}. Start the {named} release now and post that record with --pr {pr} or --target <component>; a root reply to this record with --re also stops the blocker, which repeats every {every} minutes."
        else:
            kind, text = "release", f"#{pr} landed as {landed['sha'][:10]} with {MERGE_FAST} and Release-With: {named}. Start the {named} release now, then run `cci post --drive {self.runner.config.drive} --lane <lane> --kind release --pr {pr} --target <component> --text <build>`. With no such record {every} minutes after the landing, the landing desk posts a blocker to you and the root every {every} minutes."
        seq = self.post_release(f"{key}:{step}", kind, pr, text)
        if seq is not None:
            self.book.attempt(LANDING, lambda incident: release_posted(incident, key, step, seq, self.runner.now()))

    def post_release(self, key: str, kind: str, pr: str, text: str) -> int | None:
        """Post one record about a landing's release to the release owner and the root, with `key` as its cci id so a repost returns the stored record, and return its sequence. A record cci refuses, or a post that prints no sequence, is escalated to root once per `key`, and the next pass posts it again."""
        to = f"{self.release_owner},{ROOT}"
        done = self.runner.cci_post(kind, text, to, pr, key, pr=pr, record_id=key)
        seq = POSTED.match(done.out)
        if done.code == 0 and seq:
            return int(seq[1])
        why = "refused" if done.code else "printed no sequence for"
        self.runner.escalate(key, "RELEASE-FAILED", f"#{pr}", f"cci {why} the {kind} record to {to}: {(done.err or done.out).strip()[:200]}; {text}")
        return None

    def release_started(self, landed: dict) -> dict | None:
        """The drive's first cci `release` record after the landing, from any lane but the runner's own, that names one of the landing's components as a target or its PR."""
        drive, since, names = self.runner.config.drive, actions.parse_stamp(landed["landed_at"]), (int(landed["pr"]), f"{self.repo}#{landed['pr']}")
        releases = cci.records(self.runner.cci_tail, drive, "--kind=release")
        return next(
            (
                record
                for record in releases
                if (record["kind"], record["drive"]) == ("release", drive)
                and record["lane"] != RUNNER
                and actions.parse_stamp(record["at"]) >= since
                and (set(record["refs"].get("targets", [])) & set(landed["components"]) or any(named in names for named in record["refs"].get("prs", [])))
            ),
            None,
        )

    def acknowledged(self, seqs: set[int]) -> dict | None:
        """The drive root's first cci record that carries `--re` or `--resolves` for one of the records the runner posted about a landing's release."""
        drive = self.runner.config.drive
        replies = cci.records(self.runner.cci_tail, drive, "--lane", ROOT, since=min(seqs))
        return next((record for record in replies if (record["lane"], record["drive"]) == (ROOT, drive) and seqs & {record.get("re"), record.get("resolves")}), None)

    def believed_queued(self, rows: dict[str, dict]) -> dict[str, list[str]]:
        return {
            action.action_id: [pr for pr, sha in enqueued_heads(action.action_id).items() if sha and pr in rows and rows[pr].get("state", "open") == "open" and rows[pr].get("head", "").startswith(sha)]
            for action in self.book.actions(LANDING, kind="enqueue", status="completed")
            if action.response["outcome"] in BELIEVED_QUEUED
        }

    def requeue(self, rows: dict[str, dict]) -> None:
        """A believed-queued prefix Graphite no longer holds at the heads it admitted becomes retryable, so the gate re-enqueues it once green, approved, unheld, and fidelity-ok; a drop for a merge conflict goes to the lane as a blocker instead, and a red check or a downstack failure is left to the gate's `stack-enqueue`, which re-enqueues a head a batch-mate's failure ejected and blocks one its own red check did. A drop blaming a downstack failure on a PR outside the PR's own stack is the batch's failure: it posts no blocker, tells the root once, and the same head re-enqueues after the root's next `go` record naming the PR."""
        believed = self.believed_queued(rows)
        numbers = sorted({pr for prs in believed.values() for pr in prs}, key=int)
        if not numbers:
            return
        done = self.shell.run(["ccx", "vcs", "pr", "status", "-R", self.repo, "--json", *numbers])
        if done.code != 0:
            return
        queue = {str(entry["number"]): entry["queue"] for entry in json.loads(done.out)}
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        for key, prs in believed.items():
            dropped = {pr: self.drop_reason(pr) for pr in prs if queue[pr] in OUT_OF_QUEUE}
            if not dropped or None in dropped.values():
                continue
            heads = enqueued_heads(key)
            blamed = {pr: named for pr, reason in dropped.items() if (named := foreign_failure(reason, set(heads) | downstack(open_rows, pr)))}
            blockers = {pr: reason for pr, reason in dropped.items() if pr not in blamed and DROP_BLOCKER.search(reason)}
            for pr, reason in blockers.items():
                self.route(f"dropped:{pr}:{heads[pr]}:{hashlib.sha1(reason.encode()).hexdigest()[:8]}", self.owner(pr, rows[pr]), f"#{pr} {heads[pr]}: the merge queue dropped it: {reason}. Fix and push; the desk enqueues the new head.", pr)
            for pr, named in blamed.items():
                self.runner.escalate(f"awaits-go:{pr}:{heads[pr]}", "AWAITS-GO", f"#{pr}", f"{heads[pr]}: the merge queue dropped it for downstack failures on #{named}, outside its stack; no new head is needed, and a root `go` record naming #{pr} re-enqueues this head")
            drop = {"outcome": "ejected" if blockers else "dropped", "drop": dropped, "ejected": {pr: heads[pr] for pr in blockers}, "awaiting_go": {pr: heads[pr] for pr in blamed}, "dropped_at": self.book.stamp()}
            self.book.attempt(LANDING, lambda incident, key=key, drop=drop: setattr(incident.action(key), "response", {**incident.action(key).response, **drop}))

    def drop_reason(self, pr: str) -> str | None:
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "comments"])
        if done.code != 0:
            return None
        activity = [comment["body"] for comment in json.loads(done.out)["comments"] if comment["body"].startswith(MERGE_ACTIVITY)]
        return next((line.strip() for line in reversed(activity[-1].splitlines()) if line.strip()), "") if activity else ""

    def restack(self, landed_pr: str, rows: dict[str, dict]) -> None:
        landed = rows.get(landed_pr, {})
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if row.get("state", "open") != "open" or row.get("base") != landed.get("branch"):
                continue
            text = f"#{landed_pr} landed as {landed.get('landed_sha', '')[:10]}; #{pr} at {row.get('head', '')[:10]} sits on its deleted branch. Restack now: `ccx vcs stack submit` from your worktree."
            self.route(f"restack:{pr}:{landed_pr}", self.owner(pr, row), text, pr)

    def route_blocker(self, pr: str, sha: str, blocker: str, row: dict, tips: list[str], stack: set[str], gated_red: bool) -> None:
        """Tell the lane once per head, re-reading the gate just before the send so an approval or fix that already arrived is never asked for."""
        key = f"blocker:{pr}:{sha}"
        owner = self.owner(pr, row)
        if self.routed(key, owner) or self.has_landed(pr):
            return
        fresh = {match["pr"]: match for match in VERDICT_LINE.finditer(self.enqueue_run(tips, check=True).out)}
        current = fresh.get(pr)
        if current and current["verdict"] == "BLOCKED" and routed_blocker(current["detail"], stack, gated_red) == blocker:
            self.route(key, owner, f"#{pr} {sha}: {blocker}", pr)

    def owner(self, pr: str, row: dict) -> str:
        """The lane that owns `pr` now: the poster of the newest `head`, `opened`, `ready`, or `claim` record naming it on this drive, else the lane that registered its ledger row."""
        drive = self.runner.config.drive
        posted = cci.records(self.runner.cci_tail, drive, "--pr", pr, *(f"--kind={kind}" for kind in OWNER_KINDS))
        claims = [record for record in posted if record["drive"] == drive and record["kind"] in OWNER_KINDS and int(pr) in record["refs"].get("prs", [])]
        return claims[-1]["lane"] if claims else row.get("lane", "")

    def reach(self, lane: str) -> list[str]:
        """Who a blocker for `lane` goes to: the lane, and the root too when `cci who` lists no live session for it."""
        done = self.shell.run(["cci", "who", "--drive", self.runner.config.drive, "--lane", lane, "--json"])
        if done.code != 0:
            raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return [lane] if done.out.strip() else [lane, ROOT]

    def has_landed(self, pr: str) -> bool:
        """A PR the merge queue already landed keeps its ledger row open until the branch is pruned, and the gate still reads its stale branch as conflicted; it gets no blocker."""
        if pr not in self.landed:
            done = self.shell.run(["ccx", "vcs", "pr", "status", "-R", self.repo, "--json", pr])
            if done.code == 0 and any(str(entry["number"]) == pr and entry["queue"] == "landed" for entry in json.loads(done.out)):
                self.landed.add(pr)
        return pr in self.landed

    def routed(self, key: str, lane: str) -> bool:
        container = lane_container(lane)
        return key in self.book.load(LANDING).actions or (container in self.book.store.ids() and key in self.book.load(container).actions)

    def route(self, key: str, lane: str, text: str, pr: str) -> None:
        if self.routed(key, lane):
            return
        if not lane:
            self.runner.escalate(key, "UNOWNED", f"#{pr}", text)
        elif self.runner.orca.receipt(lane):
            relay, _ = self.runner.accept_relay(key, lane, text, "", self.runner.config.start_minutes)
            if (dispatch := self.runner.orca.show(lane)) and dispatch.status not in INACTIVE:
                self.runner.send(lane_container(lane), dispatch, relay)
        else:
            self.post_blocker(key, ",".join(self.reach(lane)), text, pr)

    def post_blocker(self, key: str, to: str, text: str, pr: str) -> None:
        self.book.accept(LANDING, key, "bus", json.dumps({"lane": to, "pr": pr, "text": text}), key, None)
        self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
        done = self.runner.cci_post("blocker", text, to, pr, key)
        if done.code == 0:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"posted": done.out.strip()[:200], "at": self.book.stamp()}))
        else:
            self.book.attempt(LANDING, lambda incident: incident.fail(key, (done.err or done.out).strip()[:300]))

    def verify_restacks(self, rows: dict[str, dict]) -> None:
        """A restack route is verified once its PR's head moves or the PR lands."""
        routes = [(LANDING, action) for action in self.book.actions(LANDING, kind="bus")]
        routes += [(name, action) for name in self.book.containers(LANE_PREFIX) for action in self.book.actions(name, kind="relay")]
        for container, action in routes:
            if not action.action_id.startswith("restack:") or action.status in ("verified", "failed"):
                continue
            _, pr, landed_pr = action.action_id.split(":")
            row = rows.get(pr, {})
            if row and (row.get("state", "open") != "open" or row.get("base") != rows.get(landed_pr, {}).get("branch")):
                receipt = {"head": row.get("head", ""), "state": row.get("state", ""), "at": self.book.stamp()}
                self.book.attempt(container, lambda incident, key=action.action_id: incident.verify(key, receipt))

    def advance(self) -> bool:
        """Fast-forward the checkout to the remote trunk without writing a shared ref, so the pass gates with the trunk's `stack-enqueue`; a checkout that cannot is escalated once per error."""
        git = ["git", "-C", str(self.checkout)]
        done = self.shell.run([*git, "fetch", "-q", "--no-tags", "--refmap=", "origin", f"refs/heads/{TRUNK}"])
        if not done.code:
            done = self.shell.run([*git, "merge", "-q", "--ff-only", "FETCH_HEAD"])
        if done.code:
            self.runner.escalate(
                f"checkout:{hashlib.sha1(done.err.encode()).hexdigest()[:8]}", "CHECKOUT-STALE", "landing",
                f"{self.checkout} did not fast-forward to origin/{TRUNK}, so this pass gated and enqueued nothing: {done.err.strip()}",
            )
        return not done.code

    def run(self) -> None:
        if self.quota_low():
            return
        advanced = self.advance()
        self.ledger_py("refresh", "--repo", self.repo, "--ledger", self.ledger)
        self.ledger_py("reconcile", "--repo", self.repo, "--ledger", self.ledger, "--checkout", str(self.checkout))
        drafts = self.queue_drafts()
        rows = self.rows()
        heads = {draft["headRefOid"]: str(draft["number"]) for draft in drafts or []}
        heads |= {row["head"]: pr for pr, row in (rows or {}).items() if row.get("state", "open") == "open" and row.get("head")}
        self.read_heads(heads)
        if drafts is not None:
            self.regrade_gates(drafts)
        if rows is None:
            return
        entries = holds.read(self.holds)
        unread = self.holds_unread(entries, rows)
        if not rows:
            return
        self.reconcile()
        self.verify(rows)
        self.releases(rows)
        self.verify_restacks(rows)
        self.requeue(rows)
        self.mirror_grades(rows, entries)
        if not unread:
            self.mirror_holds(rows, entries)
        if advanced and not unread and (held := self.held(rows)) is not None:
            self.gate(rows, held)
            self.enqueue()
        self.runner.overdue(LANDING, "landing")


def seed_policy(runner: Runner) -> None:
    landing = runner.config.landing
    if landing and not runner.landing_policy():
        policy = landing["policy"]
        runner.accept_policy(f"policy:{policy['revision']}", policy["rule"], policy["revision"], policy["source"], "")


def run_orca(runner: Runner, once: bool) -> int:
    state = runner.binding()
    if not state["bound"]:
        runner.escalate(f"unbound:{state['terminal']}:{state['generation']}", "UNBOUND", "runner", f"{runner.unbound(state)}; the runner refused to start")
        runner.flush()
        print(runner.unbound(state), file=sys.stderr)
        return 3
    if failure := runner.resolve_briefs_log():
        runner.escalate(f"briefs-log:{runner.config.briefs_log or 'unset'}", "BRIEFS-LOG-FAILED", "runner", f"the briefs log did not resolve at start: {failure}; each brief attach retries it")
    swept = rotated = datetime.min.replace(tzinfo=timezone.utc)
    while True:
        if runner.now() - rotated >= ROTATE_EVERY:
            runner.rotate_inboxes()
            rotated = runner.now()
        runner.follow_coordinator()
        try:
            if runner.check():
                runner.reap()
                runner.read_inbox()
                runner.read_cci()
                runner.deliver()
                if runner.now() - swept >= SWEEP_EVERY:
                    runner.sweep()
                    swept = runner.now()
            runner.deadlines_orca()
            runner.incident_clock()
            runner.in_process()
        except OrcaUnreachable as down:
            runner.orca_down(down)
        else:
            runner.orca_back()
        runner.aged_holds()
        runner.flush()
        runner.write_view()
        runner.beat()
        if once:
            return 0
        runner.idle(runner.pass_wait())
        if runner.stop_requested():
            runner.stop_marker().unlink()
            return 0


def run_landing(runner: Runner, once: bool) -> int:
    if "fidelity_lane" not in runner.config.landing:
        why = f"{runner.config.source} names no landing.fidelity_lane, so no fidelity grade would count; the runner refused to start"
        runner.escalate(f"fidelity-lane-unset:{runner.config.source}", "FIDELITY-LANE-UNSET", "runner", why)
        runner.flush()
        print(why, file=sys.stderr)
        return 3
    seed_policy(runner)
    landing = Landing(runner, runner.config.landing)
    interval = runner.config.landing.get("interval_seconds", 180)
    while True:
        started = runner.now()
        try:
            landing.run()
        except CciTailFailed as failure:
            runner.tail_failed(failure)
        except OrcaUnreachable as down:
            runner.orca_down(down)
        runner.flush()
        if once:
            return 0
        runner.shell.sleep(max(0.0, interval - (runner.now() - started).total_seconds()))


def launch_model(model: str) -> str:
    if not MODELS.fullmatch(model):
        raise argparse.ArgumentTypeError(f"{model} is not a model orca-launch.sh starts: opus, sonnet, fable, claude-*, sol, codex, incident, astra, or gpt-*")
    return model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk-runner.py", description=__doc__.split("\n", 1)[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    relay = verbs.add_parser("relay")
    relay.add_argument("--key", required=True)
    relay.add_argument("--lane", required=True)
    relay.add_argument("--text", required=True)
    relay.add_argument("--reply-to", default="")
    relay.add_argument("--deadline-minutes", type=int)
    launch = verbs.add_parser("launch")
    launch.add_argument("--key", required=True)
    launch.add_argument("--lane", required=True)
    launch.add_argument("--model", required=True, type=launch_model)
    launch.add_argument("--effort", required=True, choices=EFFORTS)
    launch.add_argument("--brief", required=True, type=Path)
    launch.add_argument("--owner-directed", action="store_true")
    launch.add_argument("--role", default="")
    policy = verbs.add_parser("policy")
    policy.add_argument("--key", required=True)
    policy.add_argument("--landing", choices=LANDING_POLICIES, required=True)
    policy.add_argument("--revision", required=True)
    policy.add_argument("--source", required=True)
    policy.add_argument("--supersedes", default="")
    loop = verbs.add_parser("run")
    loop.add_argument("--desk", choices=("orca", "landing"), required=True)
    loop.add_argument("--once", action="store_true")
    rebind = verbs.add_parser("rebind")
    handoff = verbs.add_parser("handoff")
    show = verbs.add_parser("show")
    for sub in (relay, launch, policy, loop, rebind, handoff, show):
        sub.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.config)
    runner = Runner(shell or Shell(), config, actions.Store(config.store))
    if args.verb == "run":
        return (run_orca if args.desk == "orca" else run_landing)(runner, args.once)
    if args.verb == "show":
        sys.stdout.write(runner.render())
        return 0
    if args.verb == "rebind":
        return runner.rebind()
    if args.verb == "handoff":
        return runner.handoff()
    if args.verb == "relay":
        action, created = runner.accept_relay(args.key, args.lane, args.text, args.reply_to, args.deadline_minutes or config.start_minutes)
    elif args.verb == "launch":
        action, created = runner.accept_launch(args.key, args.lane, args.model, args.effort, str(args.brief.expanduser().resolve()), args.owner_directed, role=args.role)
    else:
        action, created = runner.accept_policy(args.key, args.landing, args.revision, args.source, args.supersedes)
    posted = runner.flush()
    print(f"{action.action_id} {action.kind} {action.status}{'' if created else ' (already accepted)'}")
    return 0 if posted else 1


if __name__ == "__main__":
    sys.exit(main())
