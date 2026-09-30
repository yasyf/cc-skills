# Lane bus contracts

`scripts/bus.py` is the drive's shared record between lanes: one cc-notes log, one
entry per post, read by every lane from its own cursor. It exists because `SendMessage`
is fire-and-forget into an inbox: a message lands while its reader is idle or mid-task,
and the reader acts on the state the message described, not the state that holds now.
On the bus, a message is a pointer and the log is the state.

## The entry

Every post is one log entry holding JSON: `kind`, `topic`, `from`, `to`, `text`, `re`.
Its number is its position in the log, `#12`, and the log is append-only, so `#12` is
`#12` for the whole drive. `bus.py post` prints it; that number is the only thing a
`SendMessage` about it needs to carry.

| kind | means | text | to |
| --- | --- | --- | --- |
| `decision` | a call your lane made that another lane could build on or contradict | the decision, one line | the lanes it binds, or broadcast |
| `head` | the current head of a PR or branch you own | the full 40-hex sha | broadcast |
| `contract` | an interface, schema, name, or invariant another lane consumes | the contract, one line, or a path to it | broadcast |
| `blocker` | something that stops a lane and needs another lane's act | what is stuck and on whom | the lane that can clear it |
| `ask` | a question another lane must answer | the question | the lane asked |
| `answer` | closes an ask | the answer | defaults to the asker; `--re <ask>` |
| `withdraw` | retracts your own earlier entry | why | defaults to the target's; `--re <entry>` |

`topic` is what other lanes subscribe on: a PR number, a branch prefix, a contract name,
an area. A reply inherits its target's topic. Only the poster withdraws an entry, and
only once; a withdrawn entry stays in the log and renders `[WITHDRAWN #n]` wherever it
is read, so a lane that already acted on it learns that it was retracted, and a lane
that has not yet acted never acts on it. An answered ask renders `[ANSWERED #n]`.

## Delivery

`bus.py read --lane <you>` returns the entries since your cursor that reach you, then
moves the cursor. An entry reaches you when it is addressed to you; a broadcast entry
reaches you when you subscribe to its topic or its kind, or when you filter on nothing.
Your own posts never come back to you. An entry addressed to someone else never reaches
you, however you filter; read it with `--all` and no `--lane` filter when a foreign
thread matters, or read `state` and `summary`, which are unfiltered.

The cursor is `~/.cache/ccn-bus/<bus>/<lane>.cursor`. After compaction, the same lane
continues from it; nothing is re-delivered and nothing is lost.
`--peek` reads without moving it; `--since <seq>` and `--all` re-read from a point.

`bus.py watch` is the same read in a loop, for a Monitor: it prints each newly
delivered entry the moment it lands, prints nothing otherwise, and exits after `--for`
seconds so the Monitor's own deadline never cuts it mid-write. It says
`bus unreachable: ...` and exits 1 when cc-notes stops answering; silence means only
that nothing arrived.

## State, not asks

`bus.py state` folds the log into the live head and contract per lane and topic: the
latest of each, minus the withdrawn. Before asking a lane what its head is or what its
interface says, read `state`. A lane publishes a `head` on every push and a `contract`
whenever it exposes something another lane consumes, and withdraws the contract before
it changes the interface. Two lanes building contradicting models show up here as two
contracts on one topic from two lanes, which is the collision a reader can see before
either lane ships it.

## The root's view

`bus.py summary` is at most ten lines: the counts, then every open ask and open blocker
with its age, then the latest decisions in the window. The root reads it beside the
desk's summary and never the log. An open ask older than the lane's cadence, or a
blocker with nobody to clear it, is the root's to dispatch.

## Worked examples from one drive

**An OK already given.** `pr-plans` asked `iam-structural` whether its stack could land
before the IAM wave, and waited on a reply that had already gone to its inbox while it
was mid-poll. On the bus: `pr-plans` posts `ask --to iam-structural`, keeps working, and
at its next wake or watch event reads `answer #n re #m`. Had it read the bus before
waiting, the `[ANSWERED #n]` mark on its own ask would have ended the wait.

**A verdict already withdrawn.** `partial-release` waited on `artifact-contract`'s
verdict that a run's artifact reads were declared; `artifact-contract` had retracted it
by message an hour before. On the bus: the verdict is a `decision --to partial-release`,
its retraction a `withdraw --re <that decision>`, and `partial-release`'s read shows the
decision with `[WITHDRAWN #n]` and the withdraw itself, in one read.

**A red nobody saw.** The desk's `route` printed a `DESK #n <sha9>: <job>` line to send
by `SendMessage`, and the lane was idle. On the bus, the desk also posts it as
`blocker --topic <pr> --to <lane>`; the lane's wake read delivers it whatever became of
the message, and the root's summary counts it open until the lane withdraws it or
reports a new head.

## What stays on `SendMessage`

The one-line wake: `bus #n` or `read the bus`. The lane report to the desk under D1,
which the desk types into the ledger. A `RULING NEEDED` line to the root. Everything
with a body goes on the bus first, and the message names the entry.
