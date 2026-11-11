---
name: long-running
description: Run a long drive as two Claude Code sessions. The communicator is the one the owner talks to in Slack and the terminal; cc-slack puts the eyes on each of his messages the moment it lands, and the communicator acks inside 5 to 10 seconds, alone in one message, starts a handler in the next, and does nothing else in its turn; the handler reads what it needs, answers him itself, shares what he asked in the thread he named, and hands orders and rulings over. The coordinator turns his orders into background agents that each ship one deliverable end to end (code, PR, CI, merge, install, check), routes the events one watcher makes of CI, merges, his PR-card taps, promised times and a 30-minute tick, and sends the communicator what to post, batched into summaries. They share the drive's state file and talk through cci. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
---

# Long-running drives

A drive is two sessions and the background agents one of them starts. His ruling, Nov 9 7:46 AM:
"We like the coordinator / communicator split". Both sessions run Fable 5.2, `claude-fable-5-2`,
and the only other Fable in the drive is the light design lane that designs a tricky order before
any builder starts, by the `design` page. Handlers and readers run on Opus unless the ask is very
simple (`communicator`); every other job keeps the model its agent names. His rulings, Nov 9 1:32 PM: "Our coordinator and
communicator lanes should switch to fable 5.2, released today (still no other lanes should be
fable)", and 9:38 PM, which makes the design lane the one exception: "when you have a tricky design
like this to make, you should 1) spawn a light fable lane just for the initial design and 2) ask
the user early and often".

- **The communicator** is the only session he talks to, and its turn is two messages: a
  one-sentence ack alone, inside 5 to 10 seconds, then one `long-running:handler` for his message,
  and nothing else; cc-slack has put the eyes on his message by then. The handler reads what it
  needs, does the work or hands it to the coordinator, answers him itself in his thread, and
  returns one line: its permalinks and at most three facts. Everything past the ack runs in
  handlers, so his next message never sits behind the communicator's own work. His rulings, Nov 9 4:07 PM: "I told you you need to multiplex and yuour
  job is just spoawning sub-threads/agents to ahndle a given message, and it should be able to
  respiond while pushing context back up to you", and 5:02 PM: "youre still responding way too slow,
  so youre clearly not multiplexing enogh, you should be responding in 5-10 seconds for simple
  things liek that".
- **The coordinator** turns his messages into **orders**, gives each order to **jobs** (background
  agents that each own one deliverable end to end), routes **events** from `drive watch`, lands
  and installs, and sends the communicator what to post. It never posts to Slack: his reply under a
  post reaches only the session that posted it, so his answer to a card the coordinator posted
  waits on its bookkeeping.

The drive's state lives in one file, written only by the `drive` CLI and read by both
sessions. His rulings live in cc-notes answers. The two sessions talk only through cci records,
which outlive a compaction or a restart. An agent answers only to the session that started it, so
every handler is the communicator's and every job is the coordinator's. Other sessions reach the
drive through cci, addressed to `root`, the coordinator.

## Pages

The rest of this skill is pages, each printed whole by `drive manual <page>`. Both sessions read a
page that way and use Read, Write or Edit on no file: a compaction reads back the files a session
last read or wrote, never a command's output. His ruling, Nov 9 9:28 PM: "make sure we
asap install something that makes compactions result in very minimal context, and even the state on
disk needs to be maximally compact and very token efficient".

| Page | What it covers |
|---|---|
| `start` | Start or resume: the two Orca terminals, then each role's steps. A restart, a resume and a compaction land here too. |
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

## Tooling

A tool defect either session hits gets a builder job on that tool's repo in the same turn, under
its own "Drive tooling:" order (`scope`): the coordinator starts it, from a `defect` record when the
communicator hit it, and the work it blocked moves to a route that works meanwhile. It is never an
excuse or a question to him. Before a fix, search cc-notes papercuts for the same symptom:
the third fix for one symptom is a class fix whose PR deletes the earlier patches.

## Never

- The communicator's turn is two messages: the ack alone inside 5 to 10 seconds,
  then `drive heard` and the handler. Every read, report, card, due line, draft, `check-post`,
  share and post past the ack belongs to a handler. It never starts a builder or waits on
  anything, and only orders, alerts, rulings, his answers, defects and a question a card's `ask`
  record does not answer go to the coordinator.
- Neither session treats his correction about how the drive works as a one-off: it is a ruling,
  recorded and sent for a skill rewrite in the turn it arrives.
- Never build, polish or card a PR that is neither a port, his order nor drive tooling, and never
  draw a tap card for a port.
- Never start a builder on a tricky design before a design lane has drawn it and its questions are
  on cards; the builder may build the recommendation, but no PR goes up or draws his Approve until
  he has answered each, and never card a PR that makes a design decision he never answered.
- The coordinator never does an order's work itself, a past-due order's included: every order
  goes to a background job. Its own shell calls are reads of `drive`, `cci` and one `gh` call,
  beside the transcript reads and the remote-lane `drive job` verbs this skill names. A loop, any
  other `orca` call, a build, a close, a thread read, a draft, and anything else runs in a
  background job, so its turn never blocks. His read-only questions are the communicator's, never
  the coordinator's.
- Never send a record or post a line per event: events feed the next summary, and nothing but
  cards and incident lines goes out while he is away.
- Never run a pass over a team's data as a person: a service account in its cluster runs it
  (`jobs`).
- Never stop, signal, or close another session unless he named it.
- Never run tests locally; CI grades.
- Never hold, freeze, or reorder work he did not hold, freeze, or reorder. A build or release he
  started is his: never cancel, retry, reorder or approve it, and start no release while it runs.
  One of ours that must stop gets a `report` to the communicator first, then its job stops it.
- Never re-derive state from memory or infer it: `drive status`, GitHub and transcripts are the
  truth, and what a release carries (its PRs, the steps it runs, which build fixes what) is read
  from that release's commit and build.
