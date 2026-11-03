# Lane bus contracts

## Contents

- [Overview](#overview)
- [Record kinds and entries](#record-kinds-and-entries)
- [Delivery and cursors](#delivery-and-cursors)
- [Desk inbox rules (I1-I7)](#desk-inbox-rules-i1-i7)
- [Lane bus rules (B1-B5)](#lane-bus-rules-b1-b5)
- [The polling loop](#the-polling-loop)
- [Worked examples](#worked-examples)
- [What stays on SendMessage](#what-stays-on-sendmessage)

## Overview

`cci` is the drive's shared record between lanes. The cci drive needs no init. The root puts the cci drive name in every brief beside the ledger id, and lanes use that name as `DRIVE` from any worktree. Ephemeral communication and state live in `cci`, never in manual files or `ccn`, while durable owner rulings, decisions, runbooks, and design docs stay in cc-notes and link to bus records with `--ccn <id>`.

Each post is one record, and each lane reads deliveries from its own cci cursor. `SendMessage` can arrive while its reader is idle or mid-poll. A `SendMessage` carries the record's sequence number, and the reader checks `cci` before acting on a message that may be stale.

`incident.py` posts through `bus.py` onto the same cci drive. The root opens an incident with `incident.py open --bus <cci drive>`, and its comms lane reads asks and answers with `cci`:

```sh
cci tail --drive <drive> --reader <comms lane> --kind ask --since 0 --json
cci post --drive <drive> --lane <comms lane> --kind answer --re <seq> --text "posted ts=<ts>"
```

## Record kinds and entries

Each record has a `seq`, `drive`, `lane`, `kind`, `at`, and `text`, with optional `topic`, `to`, `re`, and references. `cci post` prints its sequence number, such as `#12`. Sequences increase across the local store, so a drive can have gaps. Keep text under 400 characters; put a longer body in a file and attach it with `--path <file>`.

| kind | means | text | to |
| --- | --- | --- | --- |
| `decision` | a call your lane made that another lane could build on or contradict | the decision; link a durable decision with `--ccn` | the lanes it binds, or broadcast |
| `head` | the current head of a PR or branch you own | the full 40-hex SHA; for a PR whose head replaced an earlier one, `<new> (was <old>): <what changed>`, saying `patch-identical` for a restack whose range-diff changes no patch, which carries the owner's board tap | broadcast |
| `contract` | an interface, schema, name, or invariant another lane consumes | the contract, with a longer body attached by `--path` | broadcast |
| `blocker` | something that stops a lane and needs another lane's act | what is stuck and who can clear it | the lane that can clear it |
| `ask` | a question another lane must answer | the question | the lane asked |
| `answer` | closes an ask or blocker | the answer; `--re <seq>` | explicitly name the asker or blocked lane with `--to` |
| `withdraw` | retracts an earlier record | why; `--re <seq>` | repeat the original recipients, or broadcast if the original was broadcast |

`topic` names a PR, branch, contract, or area. Set `topic` and recipients explicitly on related posts and replies, because replies do not inherit them. Address an `answer` to the asker and a `withdraw` to the original recipients, or leave a `withdraw` broadcast if the original was broadcast. Use `--re <seq>` for the record being answered or withdrawn. Use `decide` for a decision request, and `decision` to announce a call already made.

Text reads show `[ANSWERED #n]` on an answered ask and `[WITHDRAWN #n]` on a withdrawn record, where `n` is the reply's sequence number. JSON reads have no reply marks.

An `answer`, `go`, or `withdraw` closes an ask. A `withdraw`, `answer`, or `done` closes a blocker. Pair the later record with `--re` or the same `--topic` in the same drive. Publishing a new head alone does not close a blocker. The desk posts each `route` line in the pass that prints it so a red reaches an idle lane at its next wake:
`cci post --drive <drive> --lane <desk> --kind blocker --topic <pr> --to <lane> --text "<route line>"`.

## Delivery and cursors

A lane reads with `cci tail --drive <drive> --cursor <lane> --reader <lane>`. `--reader` delivers records addressed to that lane regardless of kind, posting lane, or topic filters, plus other lanes' broadcasts that match those filters. Use `--to <desk>` instead of `--reader` for a desk's addressed inbox only.

A broadcast has no `--to`. Add `--topic` or `--kind` to select broadcasts, repeating each flag for multiple values. With no filters, all other lanes' broadcasts arrive. Your own broadcasts and records addressed only to other lanes do not arrive.

The cursor is a cci cursor named for the lane. It survives session compaction and advances only through printed records. A new tail cursor reads the past hour. Repeat a capped read with the same drive, cursor, and filters to continue reading. Tail defaults to a 4,000-byte budget and caps at 16,000 bytes with `--budget`.

`--since <seq>` reads after that sequence without reading or advancing the cursor. `--since 0` replays retained records. Omit `--reader` for a drive-wide replay when another lane's thread matters.

A top-level lane watches with `cci watch --drive <drive> --cursor <lane>-watch --reader <lane>` and the same subscription. The separate watch cursor keeps wake events from advancing the lane's read cursor. Without a saved cursor, the watch starts at the current head. The watch polls once a second, prints an entry capped at 600 characters only when one is delivered, and exits after 29 minutes; re-arm it with the same cursor when it exits on expiry. An in-process lane runs a foreground watch with `--for 50s`, then tails its lane cursor before acting.

## Desk inbox rules (I1-I7)

These I-rules apply to the landing desk, priority desks, and shards. The root never `SendMessage`s a desk.

The orca desk reads its launch, relay, and hold lines only from cci records addressed to `orca-desk` in the `R<n>` form from the owner's CLAUDE.md Incident Turn. The root posts each one with `desk-launch.py`, which mints the `R<n>` and the clock. By hand, the root launches an incident fix lane with:
`cci post --drive <drive> --lane root --kind go --to orca-desk --text 'R<n> (<h:mm AM|PM>) orca-desk: launch <dir> NOW incident xhigh role=fix brief=<state>/<dir>/brief.md'`.
A lane launched from a `brief.md` takes its directory's name and keeps that name from launch on. The runner refuses any other name with `LAUNCH-FAILED` and prints the order that launches the lane under its directory's name.

**I1. A desk's inbox is the set of cci records addressed to it.** The root posts each ruling with `cci post --drive <drive> --lane root --kind go --to <desk> --text "<ruling>"`. Keep text under 400 characters, attach a longer body from a file with `--path <file>`, and link durable records with `--ccn <id>`.

**I2. Read from the saved cursor at the top of every iteration.** Before any other work, act on the records printed by `desk-wait.sh` or the cci Monitor. `desk-wait.sh` reads its own `<desk>-wake` cursor, so the read below prints those records again; act once per seq. Then read with `cci tail --drive <drive> --cursor <desk> --to <desk>` and act on each new record and any delivered message. Repeat a capped read with the same cursor and filters. Every report names the last sequence read as `cursor #<seq>`.

**I3. Read the inbox before reporting a wait on the root.** Check for the answer before saying an item is waiting on the root.

**I4. Never `SendMessage` a running desk.** When a desk's reported cursor stays behind the root's last addressed record for more than one iteration, the root posts one `cci post --drive <drive> --lane root --kind go --to <desk> --text "Read unread range #<first>-#<last>."` and records the stall in its progress record. The desk reads that record at the top of its next iteration under I2. Resume only a desk that has already reported and ended its loop, using Scoped resume in `reference/orca-lane-brief.md`, in place with the same identity.

**I5. Keep about 15 lanes per desk.** Split early into a second desk or a priority desk.

**I6. Standing rules have durable answers and are never done.** Standing rule registration and supersession rules are in `reference/owner-rulings.md`.

**I7. In-process teammate desks wait in the foreground.** Every Agent-spawned desk is in-process. Every in-process desk and lane names its own team mailbox as the source `mailbox:<lane name>=<cursor file>`. `desk-wait.sh` resolves that mailbox on every call from the live team roster in the root process's own `session-<id>` under `~/.claude/teams/`. `inboxes.py mailbox <lane name>` prints that path. A numbered successor names its own lane name, never the one baked into a predecessor's scripts.

Run one foreground Bash call with `timeout: 60000`:
`desk-wait.sh 50 cci:<drive>:<desk> mailbox:<desk>=<cursor file> [<other file>=<cursor file>...]`.
A foreground watch in the same loop appends `-- <command>` to that call so the mailbox ends the watch.

`desk-wait.sh` waits at most 50 seconds and returns on new addressed records, a new line in another watched file, or `MAILBOX <n> unread`, where `n` counts all unread mailbox entries. Each new unread entry follows that line as a `== <from> <timestamp>` header and its full text. `desk-wait.sh` clips displayed records and file lines to 400 characters and ends clipped lines with an ellipsis; mailbox text is never clipped. Claude Code delivers an in-process teammate's mail only after its turn ends, never at a tool-call boundary. The printed text is the lane's only copy until then, so the lane acts on it, runs step 0 on the output, and reruns the call in a loop. Run `desk-wait.sh` as the Bash call itself, or from a script that passes its whole output through; never use `>/dev/null`, `| tail`, or `| cut`.

The `cci:<drive>:<desk>` source runs `cci tail` for records addressed to the desk past the cci cursor named `<desk>-wake`, advancing `<desk>-wake` and never the desk's own cursor so a broadcast before an addressed record stays in the desk's read. The script also advances each file's cursor. Act on the printed records and file lines before reading beyond those cursors under I2. Run the 3-minute reconciliation pass, the 30-minute summary, and periodic sources such as `ledger.py watch --drive <drive> ... --once` and `ccx vcs pr watch ... --once` as foreground steps in that same loop between waits, never as background Bash or Monitor.

Only top-level session desks arm one Monitor on `cci watch --drive <drive> --cursor <desk> --to <desk>` at timeout `1800000`. The watch exits after 29 minutes; re-arm it on every exit and after compaction. It advances the desk's cci cursor as it prints records; run step 0 on those records at every wake.

## Lane bus rules (B1-B5)

**B1. Post the state, message the pointer.** Use `cci post --drive <drive> --lane <lane> --kind <kind> --text "<text>"` for `decision`, `head`, `contract`, `blocker`, `ask`, `answer`, and `withdraw` records. Post decisions other lanes build on, heads after every push, and contracts for interfaces other lanes consume. A lane whose tools include the cci channel's tools (`mcp__plugin_cc-inbox_cci__*`) posts through `post` with the same fields as structured arguments instead of through Bash, and reads through `read`, `inbox` and `find`: the tools run inside the channel server and skip the Bash call's shell and hooks. Broadcast `head` and `contract` records by omitting `--to`, or address records with `--to` when a named lane must act.

Link replies with `--re <seq>`. The `SendMessage` that wakes that lane carries the entry number and nothing else. Set `HEAD_SHA` to the full 40-hex SHA of the pushed commit. Use the ask's number as `ASK_SEQ` and the verdict being retracted as `VERDICT_SEQ`.

```sh
cci post --drive "$DRIVE" --lane pr-plans --kind head --topic 27510 --text "$HEAD_SHA"
cci post --drive "$DRIVE" --lane iam-structural --kind contract --topic iam-contract --text "grants derive from row kinds; no hand IAM"
cci post --drive "$DRIVE" --lane iam-structural --kind decision --topic iam-contract --text "IAM wave lands before the pre-hold" --to pr-plans
cci post --drive "$DRIVE" --lane pr-plans --kind ask --topic 27510 --text "clear to label before the IAM wave?" --to iam-structural
cci post --drive "$DRIVE" --lane iam-structural --kind answer --topic 27510 --to pr-plans --re "$ASK_SEQ" --text "yes, land it"
cci post --drive "$DRIVE" --lane landing-desk --kind blocker --topic 27510 --text "DESK #27510 3f3acff97: plan job red" --to pr-plans
cci post --drive "$DRIVE" --lane artifact-contract --kind withdraw --topic artifact-reads --to partial-release --re "$VERDICT_SEQ" --text "verdict retracted; contract changed"
```

**B2. Read at every wake and before every decision or report.** A lane's first tool call on any wake is `cci tail --drive <drive> --cursor <lane> --reader <lane>` with its `--topic` and `--kind` subscription filters. Read again before deciding, reporting, or asking. A read that shows `[ANSWERED #n]` or `[WITHDRAWN #n]` on an entry ends any wait on it. Never act on a message before reading `cci`.

```sh
cci tail --drive "$DRIVE" --cursor pr-plans --reader pr-plans --topic 27510 --topic iam-contract --kind decision
```

**B3. Watch while running.** Landing desks, priority desks, and shards follow I7. Other top-level lanes arm one Monitor on `cci watch --drive <drive> --cursor <lane>-watch --reader <lane>` with the same subscription at timeout `1800000`, and re-arm it when it exits after 29 minutes. Other in-process lanes run the watch in the foreground with `--for 50s`, then read their lane cursor under B2. This is the one Monitor a lane keeps; R3 in `SKILL.md` forbids one per build.

```sh
cci watch --drive "$DRIVE" --cursor pr-plans-watch --reader pr-plans --topic 27510 --topic iam-contract --kind decision
```

**B4. Withdraw, never overwrite.** A poster retracts a verdict, moves a head, or changes an interface by posting `cci post --drive <drive> --lane <lane> --kind withdraw --re <seq> --text "<reason>"` to the original recipients, then posting the new record. Withdraw only your own records, once, before replacing them. A withdrawal reveals the previous non-withdrawn record in state, if one exists; publish the replacement when it is ready.

**B5. Read `state` before asking, and `digest` for the root's view.** `cci state --drive <drive>` returns the latest non-withdrawn `head` and `contract` per lane and topic; add `--lane` or `--topic` to narrow the result. Never send a question to a lane that `cci state` already answers.

The root reads `cci digest --drive <drive>` beside the desk's summary for counts, open asks and decision requests, blockers, holds, incidents, and the latest record per lane. The digest covers 24 hours by default and counts older open items separately; pass `--since` for a longer window, and narrow a follow-up read when the digest reports truncation. An open ask older than its lane's cadence, or a blocker with nobody to clear it, is the root's to dispatch under R6 in `SKILL.md`. Two conflicting contracts on the same topic from two lanes require a root ruling before either ships.

```sh
cci state --drive "$DRIVE"
cci state --drive "$DRIVE" --lane iam-structural --topic iam-contract
cci digest --drive "$DRIVE"
cci tail --drive "$DRIVE" --reader root --since 0
```

## The polling loop

Lanes poll in the foreground. The Bash tool caps `timeout` at `600000` ms, so a call budgets about nine minutes and the lane re-runs it until a terminal state. A plain Bash wait, whether `sleep`, a watch command, or a poll loop, never blocks longer than 60 seconds. Any longer wait goes through `desk-wait.sh` on the lane's own team mailbox so a `SendMessage` ends the call.

A blocking watch runs after `--`, and the mailbox ends it within about two seconds. Run it with `timeout: 570000`:

```sh
desk-wait.sh 540 "mailbox:<lane>=<cursor file>" -- bk build watch <build>
```

`desk-wait.sh` passes the command's output and exit status through, and on `MAILBOX` or the deadline it ends the command's process group and prints `MAILBOX <n> unread` or `QUIET`. Wrap read-only watches only, never a ship, push, or apply. On `MAILBOX`, act on the message text it printed before re-running the watch.

For a custom poll loop, run with `timeout: 570000` and cover failure states in the `case` so a broken build does not poll until the deadline:

```sh
deadline=$(( SECONDS + 540 ))
while (( SECONDS < deadline )); do
  state=$(<authoritative query>)
  case "$state" in
    passed|merged|CREATE_COMPLETE|UPDATE_COMPLETE) echo "TERMINAL ok $state"; exit 0 ;;
    failed|broken|canceled|timed_out|ROLLBACK_*|*_FAILED) echo "TERMINAL bad $state"; exit 1 ;;
  esac
  wake=$(desk-wait.sh 30 "mailbox:<lane>=<cursor file>")
  case "$wake" in
    MAILBOX*) echo "$wake"; exit 0 ;;
  esac
done
echo "STILL_RUNNING $state"
```

Run anything needing an environment token in the lane's Bash call; a Monitor shell does not inherit the environment, so `BUILDKITE_API_TOKEN` and similar variables are empty there. Prefer a CLI with a stored credential over an exported token.

A nine-minute call outlasts the default 5-minute subagent prompt cache, which causes the lane's next request to rewrite its whole context at 1.25 times the input price instead of reading it at 0.1 times. Lanes need the 1h cache. The `subagentPromptCacheTtl: "1h"` setting or `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` gives the 1h cache to every subagent. Without either, the lane types' `experimental.cacheTtl: "1h"` gives it to lanes alone, except while a subscription is in overage.

## Worked examples

**An OK already given.** Post the ask and address the answer back to the asking lane with the ask's returned sequence:

```sh
cci post --drive <drive> --lane pr-plans --kind ask --topic iam-wave --to iam-structural --text 'Can this stack land before the IAM wave?'
cci post --drive <drive> --lane iam-structural --kind answer --topic iam-wave --to pr-plans --re <ask seq> --text 'The stack can land before the IAM wave.'
cci tail --drive <drive> --cursor pr-plans --reader pr-plans
```

Replay the ask with `cci tail --drive <drive> --since 0 --kind ask --lane pr-plans --topic iam-wave`. Its `[ANSWERED #n]` mark ends the wait even if the wake message was missed.

**A verdict already withdrawn.** Post the verdict and its withdrawal to the same recipient:

```sh
cci post --drive <drive> --lane artifact-contract --kind decision --topic artifact-reads --to partial-release --text 'The run declares its artifact reads.'
cci post --drive <drive> --lane artifact-contract --kind withdraw --topic artifact-reads --to partial-release --re <decision seq> --text 'The artifact-read verdict is withdrawn.'
cci tail --drive <drive> --cursor partial-release --reader partial-release
```

A text read that includes the decision marks it `[WITHDRAWN #n]`.

**A red nobody saw.** Post the blocker from the desk and close it explicitly after the fix:

```sh
cci post --drive <drive> --lane landing-desk --kind blocker --topic <pr> --to <lane> --text 'DESK #<pr> <sha9>: <job>'
cci tail --drive <drive> --cursor <lane> --reader <lane>
cci post --drive <drive> --lane <lane> --kind done --topic <pr> --to landing-desk --re <blocker seq> --text 'The blocker is fixed on the new head.'
```

The root's digest keeps the blocker open until an explicit closure, even after a new head is posted.

## What stays on SendMessage

A lane reports through a `cci` record first, such as a report, `head`, `handoff`, `fix-live`, or `done` record, and then sends one `SendMessage`. Sending a report only by `SendMessage` is a defect because messages are lost across compaction. Everything with a body goes on `cci` first. `SendMessage` carries only a one-line wake saying `bus #n` or `read the bus`, or a `RULING NEEDED` line to the root. It also carries the lane report to the desk under D1 in `reference/landing.md`, which the desk records in the ledger.
