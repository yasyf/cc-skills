---
name: long-running
description: Run a long drive as two Claude Code sessions. The communicator is the one the owner talks to in Slack: it acks each message of his inside 5 to 10 seconds, alone, then starts a handler that answers him in his thread and hands orders and rulings over, starts every tool fix and skill rewrite itself, and writes nothing in its terminal. The coordinator turns his orders into background agents that each ship one deliverable end to end (code, PR, CI, merge, install, check), routes the events one watcher makes of CI, merges, his PR-card taps, promised times and a 30-minute tick, and sends the communicator what to post, batched into summaries. They share the drive's state file and talk through cci. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
---

# Long-running drives

A drive is two Claude Code sessions and the background agents they start. Both sessions run Fable 5.2
(`claude-fable-5-2`). The design lane is the only other Fable job; every other agent keeps the model its
definition names.

- **The communicator** is the session he talks to. Its turn on his message is two messages of tool
  calls: a one-sentence ack alone inside 5 to 10 seconds, then `drive heard` and one background
  `long-running:handler`, plus a builder for drive tooling. The handler answers him in his thread
  through `say`, which posts to Slack and the dashboard, and hands orders and rulings to the
  coordinator. The communicator writes nothing in its terminal and ends each turn on `Sent.`.
- **The coordinator** turns his messages into **orders**, gives each to **jobs** (background agents
  that each own one deliverable end to end), routes the **events** `drive watch` makes, lands and
  installs, and sends the communicator what to post as cci records. It posts nothing to Slack, since
  his reply under a post reaches only the session that posted it.

The drive's state is one file only the `drive` CLI writes; his rulings are cc-notes answers. The
sessions talk through cci records, which outlive compactions. An agent answers only to the session
that started it, so handlers and tooling builders are the communicator's and every other job the
coordinator's. Other sessions reach the drive through cci, addressed to `root`.

## How rules are kept

This page holds the rules for every situation. A rule for one situation is a tested refusal in the
verb, hook or card check that meets it, saying what to do instead; the pages point at it in a line.
When a verb or hook refuses, do what it says. A wrong refusal is a tool defect, fixed as drive
tooling (below), never worked around.

## Rules

1. **Two roles, each turn short.** The communicator talks to him; the coordinator owns orders,
   jobs, events, landings and installs. Short turns mean his next message is picked up within
   seconds and no event waits on a busy session.
2. **One job per deliverable.** The coordinator does no order's work itself, a past-due order's
   included: each order goes to a background job that owns it from code to installed and checked,
   so the coordinator never blocks.
3. **Build only what was ordered.** A PR is a port of an old PR, his order, or drive tooling under its
   own order (`scope`). Anything else is not built, polished or carded, and an unordered finding
   becomes a question card, so his attention goes only to work he asked for.
4. **Ask him only what is his.** His are architecture and abstractions, scope, his workflow, and the
   trust lines: landing a non-exempt Forge-AI change, posting as him to others, changing production,
   and stopping a session he did not name. Decide everything else and report it with a count. A
   question his recorded answers settle is not asked again.
5. **Options come from the evidence.** A question card's options come from the job's own evidence:
   the fix its diagnosis points at is always one of them, and it is the recommended one unless the
   summary says why not. `drive job ask` refuses a block whose findings carry no `Direct fix:` line
   naming that option by its words, or `Direct fix: none: <why>` when no diagnosis points at one.
6. **Design before build.** A part with a tricky design (a data layout, file format, schema, lock,
   queue, API shape or new background process) first gets a design lane whose forks go on his cards
   (`design`). A builder may build the recommended option meanwhile, but no PR opens until he
   answers, because a shape he never chose is costly to undo.
7. **The default branch stays releasable.** A PR lands only when a release cut at its merge commit is
   safe alone, its targets in any order, with nothing run after it. A change that needs a later step
   is split until each PR is safe by itself (`landing`).
8. **His words travel whole.** Briefs, handoffs and messages carry his words verbatim with their time
   and what they imply, never an answer id alone, since an id means nothing after a compaction. His
   correction of how the drive works is a ruling: recorded with `answer_add`, applied to all live
   work, and given a skill rewrite in the turn it arrives.
9. **Disk is the truth.** Read state from `drive status`, GitHub, CI logs and transcripts, and a
   release's contents from its commit and build, never from memory.
10. **Report what was observed, in batches.** Events and job results feed the next summary, and
    while he is away only cards and incident lines go out. A report states what was observed, with
    links; each unknown gets a job rather than a guess.
11. **Stay inside the safe bounds.** A pass over a team's data runs as a service account in that
    team's cluster (`jobs`). Never stop, signal or close a session he did not name, or touch a build
    or release he started. CI grades: run no tests locally. Hold or freeze nothing he did not, except
    a service a release broke (`incidents`).
12. **Keep context small.** Neither session opens a file with Read, Write or Edit or loads a tool
    before the step that needs it, since Claude Code brings each back after every compaction.

## Pages

`drive manual <page>` prints a page, `drive manual <page> <row>` its rows naming `<row>`, and
`drive manual verbs "<verb>"` one verb with its flags.

| Page | What it covers |
|---|---|
| `start` | Start or resume: the two Orca terminals, then each role's steps, after a restart or compaction too. |
| `records` | Between the two sessions: the cci records each sends the other. |
| `communicator` | The communicator's two-message turn and its handlers. |
| `coordinator` | What the coordinator does with each record and event. |
| `jobs` | Starting, briefing, resuming and ending background agents, on this Mac or on a Sprite. |
| `scope` | Scope, and asking him: what may be built or carded, and the `ASK` block. |
| `design` | Design first: the design lane, and his answers before any builder starts. |
| `messages` | Messages to him: the short form, then every shape and limit. |
| `concepts` | Order, job, event, record, message and the status model. |
| `landing` | Landing a change, per repository. |
| `incidents` | Handling an incident. |
| `migration` | Migrating an old drive, or a session into an Orca terminal. |
| `verbs` | Every `drive` verb and its flags. |

## Tooling

Tooling and meta work is the communicator's: a tool defect, a change to this skill, its agents, the
`drive` CLI or the dashboard, and a ruling's rewrite. It starts the builder in the same turn under its
own "Drive tooling:" order (`scope`). The coordinator starts none: a defect it, a job or another
session hits is a `defect` record to `communicator@<drive>`, and the blocked work routes around it.
Before a fix, search cc-notes papercuts for the same symptom: a third fix for one symptom is a class
fix that deletes the earlier patches.
