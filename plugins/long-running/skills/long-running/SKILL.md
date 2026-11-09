---
name: long-running
description: Run a long drive from one Claude Code session, the driver. The owner talks to it in Slack and the terminal; every order becomes a background agent that ships one deliverable end to end (code, PR, CI, merge, install, check); one watcher turns CI, merges, his PR-card taps, promised times and a 30-minute tick into events; the driver acks each of his messages at once and batches everything else into summaries, held while he is away and caught up when he writes. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
---

# Long-running drives

A drive is one session, **the driver**, and the background agents it starts. The owner talks
only to the driver. The driver turns each of his messages into an **order**, gives each order
to **jobs** (background agents that each own one deliverable end to end), routes **events**
from `drive watch`, and writes every message he sees. It never does work with a body and
never blocks, so it is always free to answer him.

| Concept | What it is |
|---|---|
| Order | One thing he asked for: his words verbatim, a state, an optional due time, its jobs, its proof. `drive order …` |
| Job | One background agent owning one deliverable: `long-running:builder` (one change, code to installed), `long-running:reader` (one question, read only), `codex:codex-wrapper` (review, diagnosis, incidents). A monorepo builder started while this Mac is over its load limit is a remote lane on a Sprite instead. `drive job …` |
| Event | A line from `drive watch`: `PR …` state changes, `TAP …` on his PR cards, `JOB …` reports from remote lanes, `DUE …` when a promised time passes, `TICK …` every 30 minutes while he is here, naming the orders with news since their last line or a promised time close. |
| Message | An ack or a summary line in his thread, a question card at his DM root, or a PR card, one per stack, that cc-slack draws from the PRs. Nothing else. [reference/messages.md](reference/messages.md) |

State lives in one file per drive, written only by the `drive` CLI on your PATH. His rulings
live in cc-notes answers. Other sessions reach the drive through cci.

## Start or resume

1. `drive start <name> --channel <his DM channel id> [--watch <channel id>]...` creates the
   drive or rebinds it to this session, and prints the channels it holds and its open orders.
   A drive watches more than his DM: each alert channel it reads is a `--watch`, kept in the
   drive across restarts, and a later `--watch` adds one. A restart, a resume and a compaction
   all land here; the `SessionStart` hook prints the same block for you after a compaction.
2. Hold his DM and every channel the `Hold with slack_watch` line names: one
   `slack_watch(channel_id=<id>)` each, with no thread. His messages and taps arrive as
   `slack.message` and `slack.action` events, and alerts as `slack.message` events from their
   channels. One session holds a channel at a time, so a channel another session holds is
   not yours until that session runs `cc-slack unfollow` ([reference/migration.md](reference/migration.md)).
3. Arm `Monitor` with `drive watch`, `timeout_ms` 1800000, and arm it again at every expiry
   notice. A second copy exits on its own, so arming twice is harmless.
4. `cci drive use <name>` and `cci subscribe --reader root`, so asks from other sessions
   arrive as channel events.
5. Read `drive status`. A restart ends every background agent, so a job whose agent is gone
   gets a fresh one only when something needs it: a `running` job now, a `waiting` job when
   its next event arrives. The fresh brief carries a handoff: branch, PR, the job's last
   `said` line. The disk is the record: never re-derive state from memory.

## What to do with each input

| Input | Do, in this turn |
|---|---|
| A message from him | First two calls: `slack_pickup` on it, then a one-line ack in the thread he wrote in, restating what he asked and what happens next. No other call comes before the ack. Then `drive heard`, with `--away` when he says he is going to sleep or stepping away; when it prints `CATCH-UP`, post the one catch-up summary [reference/messages.md](reference/messages.md) describes, for the orders it lists. Then classify the message and do its row below. Anything past one read of `drive`, `cci` or `gh` runs in a background job started for this message, which reports back for your closing line. A terminal message gets its ack in Slack too. |
| An order | `drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>`, then give it to a background job now, in the order he stated them. Every order goes to a job: a past-due one, a one-line one and a session close too. Set `--due` for when he hears next; the ack already told him. |
| A question | If `drive status`, one `gh` read or one transcript read answers it, answer in his thread now. Otherwise start a reader job and answer when it returns. Never make him wait on work he did not ask about. |
| A ruling on how things should work | `answer_add` with the question verbatim as title, his words as body, labels `scope:durable` and `drive:<name>`. A reader job started for this message finds every live job and open card the ruling touches; send each touched job the ruling with `SendMessage` when it returns. If it changes how drives work, start a builder on yasyf/cc-skills that rewrites the section of this skill it touches, merged and installed. |
| An answer or tap on a question card | `answer_add` with the card's question and his pick. Resume the job waiting on it with `SendMessage`. When he answers in the card's thread in his own words instead of tapping, call `slack_card_answer(waits=<the card's waits>, url=<the card's permalink>, label=<his words>, other=true, via=<his message's permalink>)` in the same turn: the card collapses to his answer and the asking job hears it as a tap. Reply only if something changes for him. |
| Chatter ("ok", "thanks", "nice") | React `+1`. No ack line, no reply. |
| A job finished, or a `JOB …` event | Read the first line of its result and do the matching row below. |
| A watch event | Do the matching row below. |
| An alert he sent, or one the drive's own release caused | [reference/incidents.md](reference/incidents.md), before anything else. Any other alert in a held channel gets nothing: no line, no job. |

| Job result | Do |
|---|---|
| `DONE <proof>` | `drive order set <o> --state done --proof <proof>` once its last job is done; a remote lane also gets `drive job end <j>`, run in the background, which frees its Sprite. It goes into the next summary of his thread, or his catch-up when he is away: what changed for him, with the proof link. |
| `READY <pr url>...` | `drive pr set <url> --tap` for each URL, then one `slack_dm_status(tap_prs=[<every url, bottom first>], text=<line>, thread_ts=<his thread>)`. cc-slack draws one card at his DM root for the stack the URLs form, from the PRs themselves, and the line in his thread links it. His Approve on it lands exactly those PRs, bottom first; a PR added to the stack later comes back as its own `READY` and gets its own card. |
| `WAITING <what>` | `drive job set <j> --state waiting --said "<what>"`. Nothing to him. The watch wakes you. |
| `ASK <question>` | A question card (it waits on him, so `waits` and `recommended` are set); a link line in his thread. Resume the job when he answers. |
| `BLOCKED <what>` | Decide. A broken tool gets a builder job on that tool's repo now, and the blocked work moves to a route that works. Never hand a broken tool to him. |

| Event | Do |
|---|---|
| `PR … checks failed`, `changes requested`, `new head` | Resume the PR's job with the event line. |
| `PR … merged` | Resume the PR's job to install and prove it live, or mark the order done when nothing is left to install. |
| `PR … closed` | Resume the job; if he closed it, the order's work on it ends. |
| `TAP Approve …` | Resume the PR's job: land it. A stack card's Approve arrives as one `TAP Approve` per PR, bottom first; resume the job once with all of them. |
| `TAP Hold …` | Resume the PR's job: hold it until he says otherwise. |
| `DUE o<n> …` | Read the order's job transcripts first, then a line in his thread: what is late, why, and the new time, set with `--due`. While he is away it waits for his catch-up instead. Never call a job silent, stuck or done without reading its transcript. |
| `TICK o<n> …` | The watch ticks every 30 minutes, never while he is away, and lists only orders with news: the order or one of its jobs changed since its last line, or its promised time falls before the next tick. One summary per thread, grouped by feature, covering every listed order in that thread (his main thread for orders with none): what is done since the last line and what is next, or that the promised time holds. Then `drive order set <o> --lined` for each. An order with nothing new gets no line. |
| `EVENT refused …` | cc-slack would not draw that PR's card, for the reason the line gives. The card is drawn from the PR, so resume the PR's job to fix the title or body, then draw it again with `tap_prs`. |
| `EVENT unheard …` | He answered a card no session was listening to; the line carries the question, his pick and its link. Treat it as his answer to that card. |
| `JOB <j> <o> STOPPED …` | A remote lane ended its turn without a report. Read its screen (Jobs below), then resume it with what to do next. |
| `WATCH … failed` | GitHub or Orca did not answer; the watch keeps trying and says so again in ten minutes. Nothing to him. |

## Jobs

Every job runs as a background agent on this Mac but one. His ruling, Nov 9 7:11 AM: "No I'm
saying that should be true when load is high, lanes will mostly start remote". So while this
Mac is over its load limit, a builder on Forge-AI/monorepo starts as a remote lane on a Sprite,
where its builds and checks are local to that machine; under the limit it runs here. Before
starting a monorepo builder, run `drive place`: it answers `remote` when this Mac's CPU is at
or over its load gate's limit, or when eight builders already run here, and `local`
otherwise. Readers, codex jobs, builders on any other repository, since a yasyf/* tool
installs on this Mac, and any job that touches this Mac's processes always run here.

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
say so in the order's next line. A failure after worker-start ran may have left the lane
live, so a reader checks its attempt before anything starts it again. The lane reports through Orca mail, and `drive watch` turns each
report into a `JOB <j> <o> <line>` event that opens with the `DONE`, `READY`, `WAITING`,
`ASK` or `BLOCKED` a background agent returns; `drive status --json` holds the whole report.
No `drive` CLI runs on a Sprite, so register the PR its `PR:` line links: `drive pr add <url>
--job <j>` with `--tap` on a monorepo PR, and `drive pr set <url> --exempt <class>` for an
`EXEMPT:` line.

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
- **A job's state is its transcript.** Before telling him anything about a running job, read
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
- A time you promise is set with `--due` in the same turn, so the watch holds you to it.
- Run `cc-slack check-post` on every line before it goes out, and fix what it flags.

## Tooling

A tool defect you hit gets a builder job on that tool's repo in the same turn, and the work it
blocked moves to a route that works meanwhile. It is never an excuse and never a question to
him. Before a fix, search cc-notes papercuts for the same symptom: the third fix for one
symptom is a class fix whose PR deletes the earlier patches.

## Never

- Never do an order's work yourself, a past-due order's included: every order goes to a
  background job. Your own shell calls are reads of `drive`, `cci` and one `gh` call, beside
  the transcript reads, `cc-slack check-post` and the remote-lane `drive job` verbs this skill
  names. A loop, any other `orca` call, a build, a close, a thread read, a draft, and anything
  else runs in a background job, so your turn never blocks.
- Never put work between his message and its ack: `slack_pickup`, then the ack, then the rest.
- Never post a line per event: events feed the next summary, and nothing but cards posts while
  he is away.
- Never stop, signal, or close another session unless he named it.
- Never run tests locally; CI grades.
- Never hold, freeze, or reorder work he did not hold, freeze, or reorder.
- Never re-derive state from memory: `drive status`, GitHub and transcripts are the truth.
