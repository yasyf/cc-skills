# Lane bus contracts

## Contents

- [Overview](#overview)
- [Record kinds and entries](#record-kinds-and-entries)
- [Delivery and cursors](#delivery-and-cursors)
- [Desk inbox rules (I1-I7)](#desk-inbox-rules-i1-i7)
- [Lane bus rules (B1-B5)](#lane-bus-rules-b1-b5)
- [How a lane waits](#how-a-lane-waits)
- [Worked examples](#worked-examples)
- [What stays on SendMessage](#what-stays-on-sendmessage)

## Overview

`cci` is the drive's shared record between lanes. The cci drive needs no init. The coordinator puts the cci drive name in every brief beside the ledger id, and lanes use that name as `DRIVE` from any worktree. Ephemeral communication and state live in `cci`, never in manual files or `ccn`, while durable owner rulings, decisions, runbooks, and design docs stay in cc-notes and link to bus records with `--ccn <id>`.

Each post is one record, and each lane reads deliveries from its own cci cursor. `SendMessage` can arrive while its reader is idle or mid-poll. A `SendMessage` carries the record's sequence number, and the reader checks `cci` before acting on a message that may be stale.

An incident's comms lane reads its asks and answers them with `cci`:

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

A text read prints one line per record, `<seq> <kind> <lane>[><to>] [^<re>] [x<closed>] <text>`, under an `@<clock>` line each time the minute changes. Text reads show `[ANSWERED #n]` on an answered ask and `[WITHDRAWN #n]` on a withdrawn record, where `n` is the reply's sequence number. JSON reads have no reply marks.

An `answer`, `go`, or `withdraw` closes an ask. A `withdraw`, `answer`, or `done` closes a blocker. Pair the later record with `--re` or the same `--topic` in the same drive. Publishing a new head alone does not close a blocker. The desk posts each `route` line in the pass that prints it so a red reaches an idle lane at its next wake:
`cci post --drive <drive> --lane <desk> --kind blocker --topic <pr> --to <lane> --text "<route line>"`.

## Delivery and cursors

A lane reads with `cci tail --drive <drive> --cursor <lane> --reader <lane>`. `--reader` delivers records addressed to that lane regardless of kind, posting lane, or topic filters, plus other lanes' broadcasts that match those filters. Use `--to <desk>` instead of `--reader` for a desk's addressed inbox only.

A broadcast has no `--to`. Add `--topic` or `--kind` to select broadcasts, repeating each flag for multiple values. With no filters, all other lanes' broadcasts arrive. Your own broadcasts and records addressed only to other lanes do not arrive.

The cursor is a cci cursor named for the lane. It survives session compaction and advances only through printed records. A new tail cursor reads the past hour. Repeat a capped read with the same drive, cursor, and filters to continue reading. Tail defaults to a 4,000-byte budget and caps at 16,000 bytes with `--budget`.

`--since <seq>` reads after that sequence without reading or advancing the cursor. `--since 0` replays retained records. Omit `--reader` for a drive-wide replay when another lane's thread matters.

A lane waits with `Monitor` on its record stream, by ending its turn on a typed record or an Agent or `Monitor` notification, or with `lr wait 540 cci:<drive>:<lane>` as its Bash call at `timeout: 570000`, re-run when it returns; only an in-process teammate adds its mailbox, under I7. The call follows the cci daemon's stream and any file source, so it polls nothing; its `<lane>-wake` cursor keeps a wake from advancing the lane's read cursor. Only a record stamped at or after the wait's start wakes it. An older one, such as a reply's parent that relay sync fetches on a Sprite, prints with the next record that wakes the wait, or before `QUIET` at the deadline.

## Desk inbox rules (I1-I7)

These I-rules apply to the landing desk, priority desks, and shards. The coordinator never `SendMessage`s a desk.

The orca desk reads its launch, relay, and hold lines only from cci records addressed to `orca-desk`. The coordinator posts each one with `lr launch`, and the record's seq is its key. By hand, the coordinator launches an incident fix lane with:
`cci post --drive <drive> --lane root --kind go --to orca-desk --text 'orca-desk: launch <dir> NOW incident xhigh role=fix brief=<state>/<dir>/brief.md'`.
A lane launched from a `brief.md` takes its directory's name and keeps that name from launch on. The runner refuses any other name with `LAUNCH-FAILED` and prints the order that launches the lane under its directory's name.

**I1. A desk's inbox is the set of cci records addressed to it.** The coordinator posts each ruling with `cci post --drive <drive> --lane root --kind go --to <desk> --text "<ruling>"`. Keep text under 400 characters, attach a longer body from a file with `--path <file>`, and link durable records with `--ccn <id>`.

**I2. Read from the saved cursor at the top of every iteration.** Before any other work, act on the records printed by `lr wait` or the cci Monitor. `lr wait` reads its own `<desk>-wake` cursor, so the read below prints those records again; act once per seq. Then read with `cci tail --drive <drive> --cursor <desk> --to <desk>` and act on each new record and any delivered message. Repeat a capped read with the same cursor and filters. Every report names the last sequence read as `cursor #<seq>`.

**I3. Read the inbox before reporting a wait on the coordinator.** Check for the answer before saying an item is waiting on the coordinator.

**I4. Never `SendMessage` a running desk.** When a desk's reported cursor stays behind the coordinator's last addressed record for more than one iteration, the coordinator posts one `cci post --drive <drive> --lane root --kind go --to <desk> --text "Read unread range #<first>-#<last>."` and records the stall in its progress record. The desk reads that record at the top of its next iteration under I2. Resume only a desk that has already reported and ended its loop, using Scoped resume in `reference/orca-lane-brief.md`, in place with the same identity.

**I5. Keep about 15 lanes per desk.** Split early into a second desk or a priority desk.

**I6. Standing rules have durable answers and are never done.** Standing rule registration and supersession rules are in `reference/owner-rulings.md`.

**I7. Desks wait on cci in the foreground.** Every standing desk is an lr-launched lane, and no team roster lists one, so a `mailbox:` source exits `lr wait` 1 with `no team roster lists <desk>` and `lr launch` refuses a brief that waits on its own mailbox. Run one foreground Bash call with `timeout: 570000`:
`lr wait 540 cci:<drive>:<desk> [<other file>=<cursor file>...]`.
A foreground watch in the same loop appends `-- <command>` to that call so an addressed record ends the watch.

Only an in-process teammate, a `long-running:lane` or `lane-ship` spawned with `Agent` such as a landing shard, has a team mailbox. It adds the source `mailbox:<lane name>=<cursor file>` to the same call. `lr wait` resolves that mailbox on every call from the live team rosters under `~/.claude/teams/`, and `inboxes.py mailbox <lane name>` prints that path. A numbered successor names its own lane name, never the one baked into a predecessor's scripts.

`lr wait` returns on new addressed records, a new line in another watched file, or, on a mailbox source, `MAILBOX <n> unread`, where `n` counts all unread mailbox entries. Each new unread entry follows that line as a `== <from> <timestamp>` header and its full text. `lr wait` clips displayed records and file lines to 400 characters and ends clipped lines with an ellipsis; mailbox text is never clipped. Claude Code delivers an in-process teammate's mail only after its turn ends, never at a tool-call boundary. The printed text is the lane's only copy until then, so the lane acts on it, runs step 0 on the output, and reruns the call in a loop. Run `lr wait` as the Bash call itself, or from a script that passes its whole output through; never use `>/dev/null`, `| tail`, or `| cut`.

The `cci:<drive>:<desk>` source runs `cci tail` for records addressed to the desk past the cci cursor named `<desk>-wake`, advancing `<desk>-wake` and never the desk's own cursor so a broadcast before an addressed record stays in the desk's read. A desk's first wait starts `<desk>-wake` where the desk's own cursor stands, or at the newest record when the desk has none, so it never replays what the desk already read. The script also advances each file's cursor. Act on the printed records and file lines before reading beyond those cursors under I2. Run the 3-minute reconciliation pass, the 30-minute summary, and periodic sources such as `ledger.py watch --drive <drive> ... --once` and `ccx vcs pr watch ... --once` as foreground steps in that same loop between waits, never as background Bash or Monitor.

A desk never arms a `cci watch` Monitor in place of that call, and re-runs it on every return and after compaction.

## Lane bus rules (B1-B5)

**B1. Post the state, message the pointer.** Use `cci post --drive <drive> --lane <lane> --kind <kind> --text "<text>"` for `decision`, `head`, `contract`, `blocker`, `ask`, `answer`, and `withdraw` records. Post decisions other lanes build on, heads after every push, and contracts for interfaces other lanes consume. A lane whose tools include the cci channel's tools (`mcp__plugin_cc-inbox_cci__*`) posts through `post` with the same fields as structured arguments instead of through Bash, and reads through `read`, `inbox` and `find`: the tools run inside the channel server and skip the Bash call's shell and hooks. The coordinator and the communicator post the same way once their session lists `mcp__plugin_cc-inbox_cci__post`, and a hook refuses their Bash `cci post` whenever the tool carries every flag it uses. Broadcast `head` and `contract` records by omitting `--to`, or address records with `--to` when a named lane must act.

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

**B2. Read at every wake and before every decision, report, or merge.** A lane's first tool call on any wake is `cci tail --drive <drive> --cursor <lane> --reader <lane>` with its `--topic` and `--kind` subscription filters. Read again before deciding, reporting, asking, or merging; a lane parked on a CI watch reads before it acts on the watch's return. `merge_own_red` refuses a lane's merge while a record another lane addressed to it sits past its cursor with no closing reply, unresolved and unanswered by the lane. Its refusal is that read: every such record prints in one refusal, with the newest other records and a line naming how to read the rest, and the cursor moves past them. A lane's own records never count. A read that shows `[ANSWERED #n]` or `[WITHDRAWN #n]` on an entry ends any wait on it. Never act on a message before reading `cci`.

```sh
cci tail --drive "$DRIVE" --cursor pr-plans --reader pr-plans --topic 27510 --topic iam-contract --kind decision
```

**B3. Watch while running.** Landing desks, priority desks, and shards follow I7. Every other lane waits the same way, in the foreground, and reads its lane cursor under B2 when the call returns. A lane keeps no Monitor on the bus; R3 in `SKILL.md` forbids one per build.

```sh
lr wait 540 cci:"$DRIVE":pr-plans
```

**B4. Withdraw, never overwrite.** A poster retracts a verdict, moves a head, or changes an interface by posting `cci post --drive <drive> --lane <lane> --kind withdraw --re <seq> --text "<reason>"` to the original recipients, then posting the new record. Withdraw only your own records, once, before replacing them. A withdrawal reveals the previous non-withdrawn record in state, if one exists; publish the replacement when it is ready.

**B5. Read `state` before asking, and `digest` for the coordinator's view.** `cci state --drive <drive>` returns the latest non-withdrawn `head` and `contract` per lane and topic; add `--lane` or `--topic` to narrow the result. Never send a question to a lane that `cci state` already answers.

The coordinator reads `cci digest --drive <drive>` beside the desk's summary for counts, open asks and decision requests, blockers, holds, incidents, and the latest record per lane. The digest covers 24 hours by default and counts older open items separately; pass `--since` for a longer window, and narrow a follow-up read when the digest reports truncation. An open ask older than its lane's cadence, or a blocker with nobody to clear it, is the coordinator's to dispatch under R6 in `SKILL.md`. Two conflicting contracts on the same topic from two lanes require a coordinator ruling before either ships.

```sh
cci state --drive "$DRIVE"
cci state --drive "$DRIVE" --lane iam-structural --topic iam-contract
cci digest --drive "$DRIVE"
cci tail --drive "$DRIVE" --reader root --since 0
```

## How a lane waits

A wait goes through the harness: end the turn on an Agent or `Monitor` notification, run an event stream as a `Monitor` command, and use `CronCreate` for a clock. The `shell_waits` hook blocks a Bash call that waits on a shell loop, a sleep of a minute or more, or a sleeper script, foreground or `run_in_background`. A binary that follows its source, `lr wait` and `lr coordinator next`, is no shell script: the Bash tool caps `timeout` at `600000` ms, so such a call budgets about nine minutes and the lane re-runs it until a terminal state. A lane never backgrounds a process with `&`, `nohup` or `setsid` or wraps a shell in `timeout`: the `lane_waits` hook warns on each and `lr wait` ends whatever a guarded command leaves behind, because on Nov 2 2026 one desk's nested `timeout zsh loop.sh` chains reached 513 copies and 3,286 processes and the launch gate refused every lane for an hour. Any longer wait goes through `lr wait` on the lane's own cci source so an addressed record ends the call; `lr wait` is one process that follows its sources and exits.

A blocking watch runs after `--`, and an addressed record ends it the moment it lands. Run it with `timeout: 570000`:

```sh
lr wait 540 cci:<drive>:<lane> -- bk build watch <build>
```

`lr wait` passes the command's output and exit status through, and on a wake or the deadline it ends the command's process group and prints the records that woke it or `QUIET`. Wrap read-only watches only, never a ship, push, or apply. On a wake, act on the records it printed before re-running the watch.

A state with no watch verb runs as a `Monitor` command that emits on change and exits at a terminal state, or is read once per call: query it, then wait, and re-run the call until the state is terminal, covering failure states so a broken build is not re-read until the deadline:

```sh
state=$(<authoritative query>)
case "$state" in
  passed|merged|CREATE_COMPLETE|UPDATE_COMPLETE) echo "TERMINAL ok $state"; exit 0 ;;
  failed|broken|canceled|timed_out|ROLLBACK_*|*_FAILED) echo "TERMINAL bad $state"; exit 1 ;;
esac
echo "STILL_RUNNING $state"
lr wait 30 cci:<drive>:<lane>
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

The coordinator's digest keeps the blocker open until an explicit closure, even after a new head is posted.

## What stays on SendMessage

A lane has no `SendMessage`: it reports through a `cci` record, such as a report, `head`, `handoff`, `fix-live`, or `done` record, and the record's `--to` reaches its reader. Everything with a body goes on `cci`. Only the coordinator and the communicator hold the tool, and they use it for a one-line wake saying `bus #n` or `read the bus`.
