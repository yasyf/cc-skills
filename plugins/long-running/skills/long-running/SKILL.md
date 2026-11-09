---
name: long-running
description: Run a long drive as two Claude Code sessions. The communicator is the one the owner talks to in Slack and the terminal; it acks each of his messages within seconds, hands the work over, and posts every line and card he sees. The coordinator turns his orders into background agents that each ship one deliverable end to end (code, PR, CI, merge, install, check), routes the events one watcher makes of CI, merges, his PR-card taps, promised times and a 30-minute tick, and sends the communicator what to post, batched into summaries. They share the drive's state file and talk through cci. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
---

# Long-running drives

A drive is two sessions and the background agents one of them starts. His ruling, Nov 9 7:46 AM:
"We like the coordinator / communicator split".

- **The communicator** is the only session he talks to. It picks up and acks each of his messages
  within seconds, hands the work to the coordinator, and writes every message he sees. It never
  works and never waits, so his next message never sits behind bookkeeping.
- **The coordinator** turns his messages into **orders**, gives each order to **jobs** (background
  agents that each own one deliverable end to end), routes **events** from `drive watch`, lands
  and installs, and sends the communicator what to post. It never posts to Slack: his reply under a
  post reaches only the session that posted it, so his answer to a card the coordinator posted
  waits on its bookkeeping.

| Concept | What it is |
|---|---|
| Order | One thing he asked for: his words verbatim, a state, an optional due time, its jobs, its proof. `drive order …` |
| Job | One background agent owning one deliverable: `long-running:builder` (one change, code to installed), `long-running:reader` (one question, read only), `codex:codex-wrapper` (review, diagnosis, incidents). A monorepo builder started while this Mac is over its load limit is a remote lane on a Sprite instead. `drive job …` |
| Event | A line from `drive watch`: `PR …` state changes, `TAP …` on his PR cards, `JOB …` reports from remote lanes, `DUE …` when a promised time passes, `TICK …` every 30 minutes while he is here, naming the orders with news since their last line or a promised time close, `EVENT catch-up …` when he is back. |
| Record | A cci record on the drive between the two sessions, by [Between the two sessions](#between-the-two-sessions). |
| Message | An ack or a summary line in his thread, a question card at his DM root, or a PR card, one per stack, that cc-slack draws from the PRs. Nothing else. [reference/messages.md](reference/messages.md) |
| Status | One model of where the drive stands, `drive view`: what waits on him, highest priority first (his open question cards, PRs ready for his tap, promised times that passed), then each feature by state (blocked, running, waiting, merged, live, not started). `drive dashboard` serves it as two screens, Your queue and Status, and `drive watch` draws it as the drive's canvas tab in his DM. |

The drive's state lives in one file, written only by the `drive` CLI on your PATH and read by both
sessions. His rulings live in cc-notes answers. The two sessions talk only through cci records,
which outlive a compaction or a restart. A job's agent answers only to the session that started it,
so every job is the coordinator's. Other sessions reach the drive through cci, addressed to `root`,
the coordinator.

## Start or resume

Each session runs these steps for its role. A restart, a resume and a compaction land here too; the
`SessionStart` hook prints the role's block after a compaction.

1. `drive start <name> --role communicator|coordinator [--channel <his DM id>] [--watch <channel id>]...`
   creates the drive or binds that role to this session, and prints the role's block. His DM is the
   communicator's. Each alert channel the drive reads is a `--watch`, kept in the drive across
   restarts and held by the coordinator; a later `--watch` adds one.
2. Hold every channel the block's `Hold with slack_watch` line names: one
   `slack_watch(channel_id=<id>)` each, with no thread. One session holds a channel at a time, so a
   channel another session holds is not yours until that session runs `cc-slack unfollow`
   ([reference/migration.md](reference/migration.md)).
3. `cci drive use <name>`, then the block's `Subscribe:` line: `cci subscribe --reader communicator
   --cursor communicator`, or `cci subscribe --reader root --cursor coordinator`. The named cursor
   resumes where the role's last session stopped, so a record sent while it was down arrives now.
4. The communicator handles each reply `cc-slack dm-owed` lists by its table: he wrote them while
   no session held his DM.
5. The coordinator arms `Monitor` with `drive watch`, `timeout_ms` 1800000, and arms it again at
   every expiry notice; a second copy, or one in the communicator's session, exits on its own. Then
   it reads `drive status`. A restart ends every background agent, so a job whose agent is gone gets
   a fresh one only when something needs it: a `running` job now, a `waiting` job when its next
   event arrives. The fresh brief carries a handoff: branch, PR, the job's last `said` line. The
   disk is the record: never re-derive state from memory.
6. The coordinator runs `drive dashboard`, which starts the drive's dashboard, or finds it running,
   and prints its link.

A drive saved by the earlier one-session driver reads as that session coordinating;
[reference/migration.md](reference/migration.md#from-one-driver-to-two-sessions) moves it to two.

## Between the two sessions

Post with the cci `post` tool or `cci post`, naming the writer with `--lane communicator` or
`--lane root`, and address the other as `root@<drive>` or `communicator@<drive>`: a bare lane no
live session reads here can land on another drive. Each record names the thread it is about with
`--attr thread=<ts>`.

| Record | From | To | Carries | The receiver |
|---|---|---|---|---|
| `owner` | communicator | `root@<drive>` | his words verbatim, his message's permalink as `--url`, his thread | makes them orders or a question, by its table |
| `answer` | communicator | `root@<drive>` | `--re` the `ask` it answers, which closes it, `--attr job=<j>`, and his pick or his own words | records his answer and resumes that job |
| `defect` | communicator | `root@<drive>` | a tool defect it hit, any refusal verbatim | starts a builder on that tool's repository |
| `report` | `root` | `communicator@<drive>` | the facts of one message: the thread or alert link it goes in, what changed for him, links and proof | writes the message and posts it |
| `ready` | `root` | `communicator@<drive>` | a stack's PR URLs, bottom first | draws the stack's PR card |
| `ask` | `root` | `communicator@<drive>` | a job's `ASK` block verbatim, `--attr job=<j>` | posts the question card, its `waits` id `<drive>:<j>-<the record's seq>` |

## The communicator

Each input is one short turn. Calls that do not depend on each other go out together in one
message, and none of them waits on work.

| Input | Do, in this turn |
|---|---|
| A message from him | First `slack_pickup` on it, then a one-line ack in the thread he wrote in, restating what he asked and what happens next. No call comes before the ack. Then, together, `drive heard --thread <his thread>`, with `--away` when he says he is going to sleep or stepping away, and the row below for what he wrote. A message typed in this terminal is the same, its ack posted at his DM root, which makes the thread. |
| An order, a ruling, an alert, or a question one read cannot answer | An `owner` record. |
| A question one `drive status` read answers | Answer it in his thread from that read. |
| His words in a question card's thread (the message's `thread_card` names the card) | `slack_card_answer(waits=<thread_card>, url=<the card's permalink>, label=<his words>, other=true, via=<his message's permalink>)`, which collapses the card to his answer as if he had tapped, and an `answer` record, its job and seq read from the `waits` id. |
| Chatter ("ok", "thanks", "nice") | React `+1` in place of the ack, run `drive heard --thread <his thread>`, and nothing else. |
| A tap on a question card (`slack.action` with `waits: true`) | An `answer` record with its `label`, its job and seq read from the `id`; a card from before the split has neither, so its `id` goes in the text. The card already shows his pick. |
| Explain more on a question card | Reply under it with `slack_dm_status` text and `about` set to its id, in two or three sentences from the `ask` record that made it. When that record does not answer it, an `owner` record with his question. |
| A tap on a PR card | Nothing: the coordinator hears it as a `TAP` event. |
| A `report` | Write the message it carries by [reference/messages.md](reference/messages.md), run `cc-slack check-post` on it, and post it in its thread, at his DM root when it names none, or in an alert's thread in that channel. Then redraw his status card: `drive rollup`'s card, posted with `slack_dm_status(card=…, root_only=true)`. |
| A `ready` | `slack_dm_status(tap_prs=[<every url, bottom first>], text=<one line>, thread_ts=<its thread>)`. cc-slack draws one card at his DM root for the stack the URLs form, from the PRs themselves, and the line links it. His Approve on it lands exactly those PRs, bottom first. |
| An `ask` | A question card at his DM root by [reference/messages.md](reference/messages.md), with its `waits` id, `recommended` and `drive` set, then a line in its thread linking the card. |
| A tool defect you hit | A `defect` record, and the post moves to a route that works meanwhile. |
| An `owner` record from the coordinator | His words, typed in the coordinator's terminal: handle them as a message from him, its ack at his DM root. |

`drive heard` prints `CATCH-UP` when he is back after being away; the coordinator hears it as an
event and sends his catch-up as a `report`. Your own calls are the Slack tools, cci posts,
`drive heard`, `drive status`, `drive rollup` and `cc-slack check-post`. A thread read, a transcript,
a search, or anything past one `drive status` read goes to the coordinator as an `owner` record.

## The coordinator

| Input | Do, in this turn |
|---|---|
| An order, in an `owner` record | `drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>` for each order in it, then give each to a background job now, in the order he stated them. Every order goes to a job: a past-due one, a one-line one and a session close too. Set `--due` for when he hears next. |
| A question, in an `owner` record | An order and a reader job; its answer goes back as a `report` for his thread. |
| A ruling on how things should work, in an `owner` record | `answer_add` with the question verbatim as title, his words as body, labels `scope:durable` and `drive:<name>`. A reader job started for it finds every live job the ruling touches; send each the ruling with `SendMessage` when it returns. If it changes how drives work, start a builder on yasyf/cc-skills that rewrites the section of this skill it touches, merged and installed. |
| An `answer` record | `answer_add` with the card's question and his pick. Resume its job, or for a card from before the split the job that asked it: `SendMessage` to its agent, or `drive job resume` for a remote lane. |
| A `defect` record | A builder on that tool's repository, by [Tooling](#tooling). |
| A message he typed in this terminal | An `owner` record of his words to `communicator@<drive>`, which acks it, marks him present and sends it back as his message. |
| A job finished, or a `JOB …` event | Read the first line of its result and do the matching row below. |
| A watch event | Do the matching row below. |
| An alert in a held channel | The drive's own release or change caused it, or he sent it in an `owner` record: [reference/incidents.md](reference/incidents.md), before anything else. Any other alert gets nothing: no record, no job. |
| A record from another session | Answer it on cci as its kind asks. |

| Job result | Do |
|---|---|
| `DONE <proof>` | `drive order set <o> --state done --proof <proof>` once its last job is done; a remote lane also gets `drive job end <j>`, run in the background, which frees its Sprite. It goes into the next summary of his thread, or his catch-up when he is away: what changed for him, with the proof link. |
| `READY <pr url>...` | `drive pr set <url> --tap` for each URL, then one `ready` record with every URL, bottom first, and the order's thread. A PR added to the stack later comes back as its own `READY` and gets its own card. |
| `WAITING <what>` | `drive job set <j> --state waiting --said "<what>"`. Nothing to him. The watch wakes you. |
| `ASK <question>` | An `ask` record with the block verbatim, `--attr job=<j>` and the order's thread. Resume the job when his `answer` record arrives. |
| `BLOCKED <what>` | Decide. A broken tool gets a builder job on that tool's repo now, and the blocked work moves to a route that works. Never hand a broken tool to him. |

| Event | Do |
|---|---|
| `PR … checks failed`, `changes requested`, `new head` | Resume the PR's job with the event line. |
| `PR … merged` | Resume the PR's job to install and prove it live, or mark the order done when nothing is left to install. |
| `PR … closed` | Resume the job; if he closed it, the order's work on it ends. |
| `TAP Approve …` | Resume the PR's job: land it. A stack card's Approve arrives as one `TAP Approve` per PR, bottom first; resume the job once with all of them. |
| `TAP Hold …` | Resume the PR's job: hold it until he says otherwise. |
| `DUE o<n> …` | Read the order's job transcripts first, set the new time with `--due`, then a `report`: what is late, why, and the new time. While he is away it waits for his catch-up instead. Never call a job silent, stuck or done without reading its transcript. |
| `TICK o<n> …` | The watch ticks every 30 minutes, never while he is away, and lists only orders with news: the order or one of its jobs changed since its last line, or its promised time falls before the next tick. One `report` per thread, grouped by feature, covering every listed order in that thread (his main thread for orders with none): what is done since the last line and what is next, or that the promised time holds. Then `drive order set <o> --lined` for each. An order with nothing new gets no line. |
| `EVENT catch-up thread=… orders=…` | He is back. One `report` for that thread covering those orders, grouped by feature, with what waits on him last, then `--lined` each. |
| `EVENT refused …` | cc-slack would not draw that PR's card, for the reason the line gives. The card is drawn from the PR, so resume the PR's job to fix the title or body, then send its `ready` again. |
| `EVENT unheard …` | He answered a card no session was listening to; the line carries the question, his pick and its link. Treat it as his `answer` to that card. |
| `JOB <j> <o> STOPPED …` | A remote lane ended its turn without a report. Read its screen (Jobs below), then resume it with what to do next. |
| `WATCH … failed` | GitHub or Orca did not answer, or cc-slack could not draw the status page; the watch keeps trying and says so again in ten minutes. Nothing to him; a failed page draw that persists gets a builder on cc-slack. |

While he is away, send only `ready` and `ask` records and the incident lines
[reference/incidents.md](reference/incidents.md) calls for; everything else waits for his catch-up.

## Jobs

Every job is the coordinator's, and runs as a background agent on this Mac but one. His ruling,
Nov 9 7:11 AM: "No I'm saying that should be true when load is high, lanes will mostly start
remote". So while this Mac is over its load limit, a builder on Forge-AI/monorepo starts as a remote
lane on a Sprite, where its builds and checks are local to that machine; under the limit it runs
here. Before starting a monorepo builder, run `drive place`: it answers `remote` when this Mac's CPU
is at or over its load gate's limit, or when eight builders already run here, and `local`
otherwise. Readers, codex jobs, builders on any other repository, since a yasyf/* tool installs on
this Mac, and any job that touches this Mac's processes always run here.

**On this Mac.** Start the job with the Agent tool: `subagent_type` `long-running:builder`,
`long-running:reader` or `codex:codex-wrapper`, `run_in_background: true`, a short
`description`, and the brief as `prompt`. **Never pass `name`.** Agent teams are on here, and a
named agent becomes a teammate that never wakes on events. Record the agent id the call
returns: `drive job add <kind> --order <o> --goal "<one line>" --agent <id>`.

**Remote.** `drive job add builder --order <o> --goal "<one line>" --remote`, write the brief
to a file in your scratch directory, then run `drive job launch <j> <brief file>` from the
monorepo checkout's root with `run_in_background: true`. It starts the builder through the
`orca` skill's launcher as an Orca worker on a Sprite, and its notification says where it
started or why it could not. A lane that started no worker runs here instead, never
silently: start it with the Agent tool, record it with `drive job set <j> --agent <id>`, and
say so in the order's next report. A failure after worker-start ran may have left the lane
live, so a reader checks its attempt before anything starts it again. The lane reports through
Orca mail, and `drive watch` turns each report into a `JOB <j> <o> <line>` event that opens with
the `DONE`, `READY`, `WAITING`, `ASK` or `BLOCKED` a background agent returns; `drive status --json`
holds the whole report. No `drive` CLI runs on a Sprite, so register the PR its `PR:` line links:
`drive pr add <url> --job <j>` with `--tap` on a monorepo PR, and `drive pr set <url> --exempt
<class>` for an `EXEMPT:` line.

The brief is the job's whole context, the same for both:

```
Order (his words): <verbatim>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it, e.g. installed and the installed version contains the merge>
Where: <repo, base branch, existing branch or PR to adopt>
Decisions that bind: <every ruling of his that touches this, verbatim, from answer_search>
Plan: <the plan section this implements, quoted>
Drive: <name>, order <o>, job <j>
```

- **Resume, never respawn.** Send the next event or answer to the job: `SendMessage` to its
  agent id here, `drive job resume <j> "<event or answer>"` to a remote lane, which answers the
  question it is waiting on. A finished agent resumes with its history. Start a fresh job only
  when the agent is gone (a restart) or its context is spent, with a handoff of branch, PR, and
  last line.
- **One deliverable per job, and no job waits on another.** When B needs A merged, B starts on
  A's `DONE`.
- **A sweep over many units is one job running a Workflow**, started by you, since a
  background agent cannot start agents.
- **A job's state is its transcript.** Before reporting anything about a running job, read
  it: `cc-transcript` on the file its agent id names, or for a remote lane
  `orca orchestration worker-read --dispatch <its dispatch> --limit 50 --full --json`. An
  absent message is never evidence.
- At most eight builders run on this Mac at once, and remote lanes do not count. Past eight, a
  monorepo builder starts remote, by his Nov 2 rule "Workers that are refused and enqueues
  should instead spill to being started remotely automatically"; any other builder waits as
  `open`, oldest due first.
- **A stack is at most four PRs.** His card for a stack lists every PR with its own lines and picture,
  so a fifth makes it too long to read on his phone. A change that needs more is split into separate
  stacks that each land on their own, or folded into fewer PRs.

## Ask him

Ask only what is his to decide: architecture and abstractions, scope, and his own workflow.
Do everything else and report the result with a count: ports, closes, restarts, backfills,
CI fixes, cleanups, tool fixes.

- Before any question, `answer_search` his recorded answers. A question he has answered is
  never asked again, in any wording.
- A trust line waits for him: landing a change in a `Forge-AI/*` repo that is not an exempt
  class ([reference/landing.md](reference/landing.md)), posting as him to anyone else,
  changing production, and stopping or closing sessions he did not name.
- A design question gets two to four options with what each does, a recommendation, and a
  picture when the shape matters. Keep building on the recommendation; his tap steers.

## Messages

Every message follows [reference/messages.md](reference/messages.md). The short form:

- Lead with what changed for him, in his words, never with how the machinery got there.
- Ack each of his messages at once; everything else is a summary at most every 30 minutes per
  thread, grouped by feature. While he is away only cards post, and his next message gets one
  catch-up.
- A bold line and at most two sentences. A table only for three or more parallel items.
- Name each PR by its title, linked. No ids, hashes, record numbers, lane or job names,
  file paths, or code identifiers.
- Done means live and checked; say how in a few words, with the link.
- Something that waits on him carries its buttons in the same post; nothing else has buttons.
- One copy of everything. A correction edits the message in place.
- A time promised to him is set with `--due` by the coordinator in the same turn, so the watch
  holds the drive to it.
- Run `cc-slack check-post` on every line but the ack before it goes out, and fix what it flags.

## Tooling

A tool defect either session hits gets a builder job on that tool's repo in the same turn: the
coordinator starts it, from a `defect` record when the communicator hit it, and the work it
blocked moves to a route that works meanwhile. It is never an excuse and never a question to him.
Before a fix, search cc-notes papercuts for the same symptom: the third fix for one symptom is a
class fix whose PR deletes the earlier patches.

## Never

- The communicator never puts work between his message and its ack: `slack_pickup`, then the
  ack, then the rest. It never starts a job, reads past one `drive status`, or waits on anything.
- The coordinator never posts to Slack, and never does an order's work itself, a past-due order's
  included: every order goes to a background job. Its own shell calls are reads of `drive`, `cci`
  and one `gh` call, beside the transcript reads and the remote-lane `drive job` verbs this skill
  names. A loop, any other `orca` call, a build, a close, a thread read, a draft, and anything else
  runs in a background job, so its turn never blocks.
- Never send a record or post a line per event: events feed the next summary, and nothing but
  cards and incident lines goes out while he is away.
- Never stop, signal, or close another session unless he named it.
- Never run tests locally; CI grades.
- Never hold, freeze, or reorder work he did not hold, freeze, or reorder.
- Never re-derive state from memory: `drive status`, GitHub and transcripts are the truth.
