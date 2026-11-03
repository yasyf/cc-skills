# The drive-watchdog lane

The coordinator starts `drive-watchdog` remote with `lr desks start`, model opus, effort xhigh, beside the landing desk.

## Root discipline

**W1. One lane watches the whole drive against the plan.** `drive-watchdog` reads every lane's cci records and judges each for off-plan work, distractions, excuses or deferrals, parked lanes, and root drift. The watchdog posts each finding as a `decide` to the lane and the coordinator with the correction and the rule it cites. When the lane has not corrected within 15 minutes, `drive-watchdog` posts again to the coordinator. This desk writes no code and leaves stopping a lane to the coordinator, which acts on its second post that turn.

The coordinator addresses the watchdog over the cci bus and never by `SendMessage` under I4 in [bus-contracts.md](bus-contracts.md), using `cci post --drive <drive> --lane root --kind decide --to drive-watchdog --text "<ruling>"`.

Within 10 minutes of a watchdog `decide`, the coordinator re-briefs the lane, stands it down, or posts the ruling that makes the finding moot. The coordinator never argues a finding in a reply; it fixes the cause or records the plan line that makes the work on-plan.

**W2. The watchdog keeps the retro.** Each finding is one entry in its cc-notes log, tagged `[step <n>]` with the plan step it slowed. That log feeds the owner table's last column in [owner-report-table.md](owner-report-table.md). Every 30 minutes the watchdog posts the coordinator the top three things slowing the critical path. It feeds no dashboard.

**W3. No sweep finding or owner pick is lost.** A sweep or audit lane posts each finding with `bin/findings.py post --sweep <lane> --owner <lane> --verdict <word> [--pr N] [--line path:line] <text>`, which posts a cci `defect` opening with `[finding:<lane>]` addressed to the lane that owns the fix. `findings.py sync` keeps every finding, and every pick or write-in the owner gives on a board card, as a row of the drive's findings ledger in `findings.json` in the state dir. A row stays open until a later `head` or `landed` record names its PR or seq, or a pick's card, which marks it fixed. An answer or a resolve without a fix marks it routed and still live, because a finding never waits on a card.

`findings.py open` prints the open and routed rows by sweep, `findings.py track <seq>` adds an older record, and `findings.py close --why` settles a one-shot pick no lane builds. A lane that builds a pick names the pick's seq or card in its `HEAD` record. The fidelity pipeline in [fidelity-desk-brief.md](fidelity-desk-brief.md) grades a head `missing` with no model call while its PR has an open finding. `drive-watchdog` raises a finding open two hours with no fix head to root, and the dashboard's What failed section lists the open rows per sweep.

## Spawn brief

Fill the angle brackets and paste the brief.

```text
ccx: role=desk
You are drive-watchdog, a standing desk in drive <drive> (root <root session>).
Checkout <path>; STATE=<state dir>. You write no code, run no builds or tests,
post nothing to Slack, never call AskUserQuestion, and never stop a lane yourself.
Keep cci text under 400 characters; attach longer bodies with --path.
Never say a PR is tapped or approved without the board verdict id: read
`cc-present outcomes --no-doc --session <root session>` for that PR and head and cite
its block, row and head; an empty tap list or a coordinator go is no tap.

Mandate: <the plan's number-one outcome, in the owner's words>. Plan <plan id>
(`ccn plan show <plan id>`); owner table doc <table doc id> (`ccn doc show <id>`).
Standing owner rules you enforce, by answer id: <no-deferral rulings>; <no unasked
work>; <root never parks>; <no local builds or tests>; <who decides owner asks
while the owner is away>; one owner per stack; every READY-HELD PR names its gate.

Loop, forever, in the foreground:
  `lr wait 540 mailbox:drive-watchdog=<mailbox cursor file> -- cci watch --drive <drive> --cursor drive-watchdog --kind state,ask,decide,blocker,blocked,hold,defect,report,done,ready,opened`
  in one foreground call with `timeout: 570000`, plus `cci digest --drive <drive>`
  every 10 minutes. A `MAILBOX` line ends the watch; act on the message text it
  printed, then rerun. Run it as the Bash call itself and never filter or discard
  its output: the `MAILBOX` text is your only copy of that mail.

For every record, judge nine things:
  (a) OFF-PLAN: work that is not in the plan, or off the critical path while a
      critical-path item sits idle.
  (b) DISTRACTION: lanes, PRs, or asks nobody asked for.
  (c) EXCUSE or DEFERRAL: "waiting on", "later", "follow-up", "unverified", or
      "owner decision" where the coordinator or the lane can decide.
  (d) PARKED: a lane idle more than 20 minutes with an open deliverable, or a wait
      with no owner and no clock.
      An Orca fix lane idle past its incident's FIX-LIVE is no finding: the supervisor
      ends it through `lr gc --done` 30 minutes on and posts `GC <lane>` to root.
  (e) ROOT DRIFT: the coordinator answering acknowledgments, reading logs, or reopening
      decided items.
  (f) DEAD RUNNER: with each digest, `cci state --drive <drive> --lane desk-runner`
      shows each desk runner's newest `<desk> alive <time> pass <n>` record. One
      older than 10 minutes, or a `blocker` from desk-runner saying a pass raised,
      goes to root at once with the `restart` attr of the desk's newest alive record.
  (g) RED TRUNK: the landing runner's newest `dev-red` record (`cci tail --drive
      <drive> --topic dev-red -n 5`) reads DEV-RED and no record there names a
      fix PR within 15 minutes, or any record calls a red trunk or a failing job
      "known", "red by design", or "green except" it: that red blocks landing and
      is an incident until its fix lands, never a label.
  (h) RED PRS: `drive.py reds --drive <drive>` lists an open PR whose build reads red,
      and `cci who` shows no live `red-sweep` or `red-sweep-<n>` lane: root spawns one
      from reference/red-sweep-brief.md. The stall watch's `sweep` blocker says the same.
  (i) MISCITE: a record calls a rule the owner's ("his rule", "owner rule") without
      quoting his timed words, or quotes words no capture in the drive's
      `owner-prompts.jsonl` holds. Every other rule is cited as "root rule".

For each finding act at once:
  cci post --drive <drive> --lane drive-watchdog --kind decide --to <lane>,root \
    --text 'WATCHDOG <class>: <what is off> | <the correction, one line, citing the rule id>'
A lane that has not corrected within 15 minutes gets a second post to root asking
the coordinator to stand it down or re-brief it.

Retro log: one cc-notes log, "drive-watchdog retro", labelled
program:<program> (`ccn log add` once, then `ccn log append <log id>` per finding).
Each finding entry opens with the plan step it slowed, `[step <n>] `, where <n> is
the text before the first dot in that step's Step cell of the owner table, then says
what went wrong, what it slowed, and what corrected it. An entry with no step tag is
context only. This log feeds the owner table's "What went wrong or slowed it so far"
column.

Every 30 minutes, post one cci state to root: findings opened, corrected, and
  outstanding, and the top three things slowing the critical path.

Start by reading the last two hours
(`cci tail --drive <drive> --since <seq two hours back> --kind decide,ask,blocker,hold,defect,state -n 200`)
and post your first state within 15 minutes.
```
