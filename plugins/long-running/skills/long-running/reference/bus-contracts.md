# Lane bus contracts

`cci` is the drive's shared record between lanes. Each post is one record; each
lane reads deliveries from its own cci cursor. A `SendMessage` carries the record's
sequence number, and the reader checks cci before acting. Durable owner rulings,
decisions, runbooks, and design docs stay in cc-notes, linked with `--ccn <id>`.
`incident.py` posts through `bus.py` onto the same cci drive, opened with
`incident.py open --bus <cci drive>`. Its comms lane reads asks and answers with cci:

```sh
cci tail --drive <drive> --reader <comms lane> --kind ask --since 0 --json
cci post --drive <drive> --lane <comms lane> --kind answer --re <seq> --text "posted ts=<ts>"
```

## The entry

Each record has a `seq`, `drive`, `lane`, `kind`, `at`, and `text`, with optional
`topic`, `to`, `re`, and references. `cci post` prints its sequence number, such as
`#12`; sequences increase across the local store, so a drive can have gaps. Keep
text under 400 characters. Put a longer body in a file and attach it with `--path`.

| kind | means | text | to |
| --- | --- | --- | --- |
| `decision` | a call your lane made that another lane could build on or contradict | the decision; link a durable decision with `--ccn` | the lanes it binds, or broadcast |
| `head` | the current head of a PR or branch you own | the full 40-hex SHA | broadcast |
| `contract` | an interface, schema, name, or invariant another lane consumes | the contract, with a longer body attached by `--path` | broadcast |
| `blocker` | something that stops a lane and needs another lane's act | what is stuck and who can clear it | the lane that can clear it |
| `ask` | a question another lane must answer | the question | the lane asked |
| `answer` | closes an ask or blocker | the answer; `--re <seq>` | explicitly name the asker or blocked lane with `--to` |
| `withdraw` | retracts an earlier record | why; `--re <seq>` | repeat the original recipients, or broadcast if the original was broadcast |

`topic` names a PR, branch, contract, or area. Set it explicitly on related posts;
replies do not inherit topics or recipients. Use `--re` for the record being
answered or withdrawn.

Text reads show `[ANSWERED #n]` on an answered ask and
`[WITHDRAWN #n]` on a withdrawn record; `n` is the reply's sequence number. JSON
reads have no reply marks. Withdraw your own verdicts before replacing them.
Use `decide` for a decision request; `decision` announces a call already made.

An `answer`, `go`, or `withdraw` closes an ask. A `withdraw`, `answer`, or `done`
closes a blocker. Pair the later record with `--re` or the same `--topic` in the
same drive. Publishing a new head alone does not close a blocker.

## Delivery

A lane reads with `cci tail --drive <drive> --cursor <lane> --reader <lane>`.
`--reader` delivers records addressed to that lane regardless of kind, posting
lane, or topic filters, plus other lanes' broadcasts that match those filters.

A broadcast has no `--to`. Add `--topic` or `--kind` to select broadcasts; repeat
each flag for multiple values. With no filters, all other lanes' broadcasts arrive.
Your own broadcasts and records addressed only to other lanes do not arrive.
Use `--to <desk>` instead of `--reader` for a desk's addressed inbox only.

The cursor is a cci cursor named for the lane. It survives session compaction and
advances only through printed records. A new tail cursor reads the past hour.
Repeat a capped read with the same drive, cursor, and filters to continue.

`--since <seq>` reads after that sequence without reading or advancing the cursor;
`--since 0` replays retained records. Omit `--reader` for a drive-wide replay when
another lane's thread matters. Tail defaults to a 4,000-byte budget, capped at
16,000 with `--budget`.

A top-level lane watches with
`cci watch --drive <drive> --cursor <lane>-watch --reader <lane>` and the same
subscription. The separate watch cursor preserves the lane's read position.
The watch polls once a second and exits after 29 minutes; re-arm with the same
cursor. Without a saved cursor, it starts at the current head. An in-process
lane uses a foreground watch with `--for 50s`, then tails its lane cursor before
acting. Each watch line is capped at 600 characters.

## State, not asks

`cci state --drive <drive>` returns the latest non-withdrawn `head` and `contract`
per lane and topic. Read it before asking a lane for its head or interface.
Publish a `head` on every push and a `contract` whenever another lane consumes
an interface. Broadcast both by omitting `--to`. Withdraw a contract with
`cci post --drive <drive> --lane <lane> --kind withdraw --re <seq> --text "<reason>"`
before changing the interface. A withdrawal reveals the previous non-withdrawn
record in state, if one exists; publish the replacement when it is ready.
Two lanes publishing conflicting contracts on the same topic need a root ruling
before either ships.

## The root's view

`cci digest --drive <drive>` lists counts, open asks and decision requests,
blockers, holds, incidents, and the latest record per lane. It covers 24 hours by
default and counts older open items separately. Use `--since` for a longer window.
Text output is bounded; narrow a follow-up read when the digest reports truncation.
The root reads it beside the desk's summary. An ask older than its lane's cadence,
or a blocker with nobody to clear it, is the root's to dispatch.

## Worked examples from one drive

**An OK already given.** `pr-plans` asked `iam-structural` whether its stack could
land before the IAM wave, then waited while the reply sat unread. Post the ask
and address the answer back to `pr-plans`, using the ask's returned sequence:

```sh
cci post --drive <drive> --lane pr-plans --kind ask --topic iam-wave --to iam-structural --text 'Can this stack land before the IAM wave?'
cci post --drive <drive> --lane iam-structural --kind answer --topic iam-wave --to pr-plans --re <ask seq> --text 'The stack can land before the IAM wave.'
cci tail --drive <drive> --cursor pr-plans --reader pr-plans
```

The delivery includes the answer. Replay the ask with
`cci tail --drive <drive> --since 0 --kind ask --lane pr-plans --topic iam-wave`.
Its `[ANSWERED #n]` mark ends the wait even if the wake message was missed.

**A verdict already withdrawn.** `partial-release` waited on `artifact-contract`'s
verdict that a run's artifact reads were declared after its author had retracted
it by message. Post the verdict and its withdrawal to the same recipient:

```sh
cci post --drive <drive> --lane artifact-contract --kind decision --topic artifact-reads --to partial-release --text 'The run declares its artifact reads.'
cci post --drive <drive> --lane artifact-contract --kind withdraw --topic artifact-reads --to partial-release --re <decision seq> --text 'The artifact-read verdict is withdrawn.'
cci tail --drive <drive> --cursor partial-release --reader partial-release
```

The withdrawal reaches the lane even if it already read the decision. A text
read that includes the decision marks it `[WITHDRAWN #n]`.

**A red nobody saw.** The desk's `route` printed a `DESK #n <sha9>: <job>` line
while the lane was idle. Post the blocker and close it explicitly after the fix:

```sh
cci post --drive <drive> --lane landing-desk --kind blocker --topic <pr> --to <lane> --text 'DESK #<pr> <sha9>: <job>'
cci tail --drive <drive> --cursor <lane> --reader <lane>
cci post --drive <drive> --lane <lane> --kind done --topic <pr> --to landing-desk --re <blocker seq> --text 'The blocker is fixed on the new head.'
```

The lane reads the blocker at its next wake. The root's digest keeps it open
until an explicit closure, even after a new head is posted.

## What stays on `SendMessage`

A one-line wake says `bus #n` or `read the bus`. The lane report to the desk under D1,
which the desk types into the ledger. A `RULING NEEDED` line to the root. Everything
with a body goes on cci first, and the message names the record.
