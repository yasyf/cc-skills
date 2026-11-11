---
name: long-running
description: Run a long drive as two Claude Code sessions. The communicator is the one the owner talks to in Slack: it acks each message of his inside 5 to 10 seconds, alone, then starts a handler that answers him in his thread and hands orders and rulings over, starts every tool fix and skill rewrite itself, and writes nothing in its terminal. The coordinator turns his orders into background agents that each ship one deliverable end to end (code, PR, CI, merge, install, check), routes the events one watcher makes of CI, merges, his PR-card taps, promised times and a 30-minute tick, and sends the communicator what to post, batched into summaries. They share the drive's state file and talk through cci. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
---

# Long-running drives

A drive is two sessions and the background agents they start. His ruling, Nov 9 7:46 AM:
"We like the coordinator / communicator split". Both sessions run Fable 5.2, `claude-fable-5-2`,
and the only other Fable is the design lane (`design`). Handlers and readers run on Opus unless the ask is very
simple (`communicator`); every other job keeps the model its agent names. His rulings, Nov 9 1:32 PM: "Our coordinator and
communicator lanes should switch to fable 5.2, released today (still no other lanes should be
fable)", and 9:38 PM, which makes the design lane the one exception (`design`).

- **The communicator** is the only session he talks to, and its turn is two messages of tool calls: a
  one-sentence ack alone, inside 5 to 10 seconds, then one `long-running:handler` for his message,
  plus a builder for tooling or a ruling, and nothing else; cc-slack has put the eyes on his message
  by then. It writes no text in its terminal: everything for him goes to Slack, and the turn ends
  on the one word `Sent.`. The handler does the work or hands it to the coordinator, answers him in
  his thread through `say`, this plugin's one call that posts to Slack and shows the post on the
  dashboard, its `{card:<waits id>}` and `{pr:<number>}` linked as each place draws them, and
  returns one line: its permalinks and at most three facts, so his next message
  never waits on the communicator. The `communicator` page quotes his rulings on both, Nov 9 4:07
  and 5:02 PM and Nov 10 9:53 and 10:20 PM.
- **The coordinator** turns his messages into **orders**, gives each order to **jobs** (background
  agents that each own one deliverable end to end), routes **events** from `drive watch`, lands
  and installs, and sends the communicator what to post. It never posts to Slack: his reply under a
  post reaches only the session that posted it.

The drive's state lives in one file, written only by the `drive` CLI and read by both
sessions. His rulings live in cc-notes answers. The two sessions talk only through cci records,
which outlive a compaction or a restart. An agent answers only to the session that started it, so
every handler and drive tooling builder is the communicator's and every other job the coordinator's.
Other sessions reach the drive through cci, addressed to `root`, the coordinator.

## Pages

The rest of this skill is pages: `drive manual <page>` prints one, `drive manual <page> <row>` its
rows naming `<row>`, and `drive manual verbs "<verb>"` a verb's flags, never `drive … --help`.
Read no page by Read, Write or Edit: a compaction reads back files, not output, by his Nov 9
9:28 PM ruling (`start`).

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
| `verbs` | Every `drive` verb and its flags. |

## Tooling

Tooling and meta work is the communicator's: a tool defect, a change to this skill, its agents, the
`drive` CLI or dashboard, a ruling's rewrite. It starts the builder that turn under its own
"Drive tooling:" order (`scope`). The coordinator starts none: a defect it, a job or another session
hits is a `defect` record to `communicator@<drive>`, and the blocked work takes a route that works;
never an excuse or a question to him. Before a fix, search cc-notes papercuts for the same symptom:
a third fix for one symptom is a class fix that deletes the earlier patches.

## Never

- The communicator's turn is two messages of tool calls: the ack alone inside 5 to 10 seconds,
  then `drive heard` and the handler, plus a builder for tooling, with no text in its terminal but
  the closing `Sent.`. Every read, report, card, due line, draft, `check-post`, share and post past
  the ack belongs to a handler. It starts no builder but a tooling one, waits on nothing, and only
  orders, alerts, rulings, his answers and a question a card's `ask` record does not answer go to
  the coordinator.
- Neither session treats his correction about how the drive works as a one-off: it is a ruling,
  recorded, and the communicator starts its skill rewrite in the turn it arrives (Tooling).
- Never build, polish or card a PR that is neither a port, his order nor drive tooling, and never
  draw a tap card for a port.
- Never start a builder on a tricky design before a design lane has drawn it and its questions are
  on cards; the builder may build the recommendation, but no PR goes up or draws his Approve until
  he has answered each, and never card a PR that makes a design decision he never answered.
- The coordinator never does an order's work itself, a past-due order's included: every order
  goes to a background job. Its own shell calls are reads of `drive`, `cci` and one `gh` call,
  beside transcript reads and the `drive` verbs page `verbs` lists. A loop, any
  other `orca` call, a build, a close, a thread read, a draft, and anything else runs in a
  background job, so its turn never blocks. His read-only questions are the communicator's, never
  the coordinator's.
- Never send a record or post a line per event: events feed the next summary, and nothing but
  cards and incident lines goes out while he is away.
- Never run a pass over a team's data as a person: a service account in its cluster runs it
  (`jobs`).
- Never stop, signal, or close another session unless he named it.
- Never run tests locally; CI grades.
- Never hold, freeze, or reorder work he did not, but for the freeze of a service a release broke
  (`incidents`). A build or release he started is his: never cancel, retry, reorder or approve it,
  and start no release while it runs.
  One of ours that must stop gets a `report` to the communicator first, then its job stops it.
- Never redraw a card his words under it answered, a direction included: they are his answer, the
  card closes showing them, and the work goes ahead on them. Never leave the fix a job's diagnosis
  points at out of its card's options (`scope`).
- Never re-derive state from memory or infer it: `drive status`, GitHub and transcripts are the
  truth, and what a release carries (its PRs, the steps it runs, which build fixes what) is read
  from that release's commit and build.
