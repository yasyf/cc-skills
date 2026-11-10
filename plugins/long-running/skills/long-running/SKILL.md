---
name: long-running
description: Run a long drive as two Claude Code sessions. The communicator is the one the owner talks to in Slack and the terminal; for each of his messages it puts on the eyes and acks inside 5 to 10 seconds, alone in one message, starts a Sonnet handler in the next, and does nothing else in its turn; the handler reads what it needs, answers him itself, shares what he asked in the thread he named, and hands orders and rulings over. The coordinator turns his orders into background agents that each ship one deliverable end to end (code, PR, CI, merge, install, check), routes the events one watcher makes of CI, merges, his PR-card taps, promised times and a 30-minute tick, and sends the communicator what to post, batched into summaries. They share the drive's state file and talk through cci. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
---

# Long-running drives

A drive is two sessions and the background agents one of them starts. His ruling, Nov 9 7:46 AM:
"We like the coordinator / communicator split". Both sessions run Fable 5.2, `claude-fable-5-2`,
and nothing else in the drive runs Fable: builders, readers, codex lanes and every other job keep
the model their agent names. His ruling, Nov 9 1:32 PM: "Our coordinator and communicator lanes
should switch to fable 5.2, released today (still no other lanes should be fable)".

- **The communicator** is the only session he talks to, and its turn is two messages: the eyes and a
  one-sentence ack alone, inside 5 to 10 seconds, then one `long-running:handler` for his message,
  and nothing else. The handler reads what it needs, does the work or hands it to the coordinator,
  answers him itself in his thread, and returns what it said and learned. Reports, cards, due lines,
  drafts and `check-post` all run in handlers, so his next message never sits behind the
  communicator's own work. His rulings, Nov 9 4:07 PM: "I told you you need to multiplex and yuour
  job is just spoawning sub-threads/agents to ahndle a given message, and it should be able to
  respiond while pushing context back up to you", and 5:02 PM: "youre still responding way too slow,
  so youre clearly not multiplexing enogh, you should be responding in 5-10 seconds for simple
  things liek that".
- **The coordinator** turns his messages into **orders**, gives each order to **jobs** (background
  agents that each own one deliverable end to end), routes **events** from `drive watch`, lands
  and installs, and sends the communicator what to post. It never posts to Slack: his reply under a
  post reaches only the session that posted it, so his answer to a card the coordinator posted
  waits on its bookkeeping.

| Concept | What it is |
|---|---|
| Order | One thing he asked for: his words verbatim, a state, an optional due time, its parts, its jobs, its proof. A part is one deliverable his words name, such as each stack of a design or each question; every part has a live job or a card he has yet to answer, until it is delivered. `drive order …` |
| Job | One background agent owning one deliverable: `long-running:builder` (one change, code to installed), `long-running:reader` (one question an order needs, read only, on Sonnet), `codex:codex-wrapper` (review, diagnosis, incidents). The communicator's `long-running:handler`s, one per message of his, are not jobs. A monorepo builder started while this Mac is over its load limit is a remote lane on a Sprite instead. `drive job …` |
| Event | A line from `drive watch`: `PR …` state changes, `TAP …` on his PR cards, `JOB …` reports from remote lanes, `DUE …` when a promised time passes, `ORPHAN …` the moment a part of an open order has nothing carrying it, `TICK …` every 30 minutes while he is here, naming the orders with news since their last line or a promised time close, `EVENT catch-up …` when he is back. |
| Record | A cci record on the drive between the two sessions, by [Between the two sessions](#between-the-two-sessions). |
| Message | An ack or a summary line in his thread, a question card at his DM root, or a PR card, one per stack, that cc-slack draws from the PRs. Nothing else. [reference/messages.md](reference/messages.md) |
| Status | One model of where the drive stands, `drive view`: what waits on him, highest priority first (his open question cards, PRs ready for his tap, promised times that passed), then each feature by state (blocked, running, waiting, merged, live, not started). `drive dashboard` serves it as four screens, Your queue, Status (overall progress, each feature's PR bar and its own page with his words, jobs and PRs), Work (every running or waiting job, flagged when quiet 45 minutes) and History (the last day), pushed to the page on each change to the drive's files, and `drive watch` draws it as the drive's canvas tab in his DM and stores his open cards in the drive's `cards.json` every minute. |

The drive's state lives in one file, written only by the `drive` CLI on your PATH and read by both
sessions. His rulings live in cc-notes answers. The two sessions talk only through cci records,
which outlive a compaction or a restart. An agent answers only to the session that started it, so
every handler is the communicator's and every job is the coordinator's. Other sessions reach the
drive through cci, addressed to `root`, the coordinator.

## Start or resume

Each session runs in its own Orca terminal, so he finds both in Orca and opens either with one
command. His ruling, Nov 9 4:42 PM: "ok so fix the fucking skil, also open the communicator in orca I
cant find it, thers a CLI to open it". A bare tmux session, `nohup`, `claude --bg` or any other
headless start is forbidden: Orca shows nothing for it.

Whoever starts the drive starts both sessions with these three commands, `<worktree>` being the
drive's checkout, which for a monorepo drive is the monorepo checkout's root. `orca worktree show --worktree
path:<worktree>` must find it; a repository Orca does not list is added first with `orca repo add
--path <its main checkout>`. The first command names the worktree after the drive in Orca's sidebar.

```sh
orca worktree set --worktree path:<worktree> --display-name <drive>
orca terminal create --worktree path:<worktree> --title <drive>-coordinator --command "claude -n <drive>-coordinator --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the coordinator: run Start or resume for that role'" --json
orca terminal create --worktree path:<worktree> --title <drive>-communicator --command "claude -n <drive>-communicator --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the communicator: run Start or resume for that role'" --json
```

For a drive that does not exist yet, the coordinator's prompt ends `; his DM is <channel id>`, which
its `drive start` passes as `--channel`. Each session's `drive start` records the Orca terminal it
runs in, and the start ends with the proof. `drive status` checks, read only, that Orca has a pane
for that terminal (`orca diagnostics memory` lists it with a pane key) and that the terminal's
process tree holds the session's Claude Code process (`~/.claude/sessions/<pid>.json` names the
session); only then does that role's line read `open it with: orca terminal switch --terminal
<handle>`, the command that brings the session forward in Orca. He gets a switch command from that
line alone, never from a create's output, an environment variable or a record. A line that reads
`not visible in Orca until restarted into a terminal` (a terminal with no pane) or `in no Orca
terminal` (tmux included) gets no switch command: he hears it in those words, and the session moves
into a fresh Orca terminal by [reference/migration.md](reference/migration.md#into-an-orca-terminal),
only on his word.

Then each session runs these steps for its role. A restart, a resume and a compaction land here
too; the `SessionStart` hook prints the role's block after a compaction.

1. A live session on another model switches with `/model claude-fable-5-2` at an idle prompt.
   `drive start <name> --role communicator|coordinator [--channel <his DM id>] [--watch <channel id>]...`
   creates the drive or binds that role to this session and its Orca terminal, and prints the
   role's block. His DM is the communicator's. Each alert channel the drive reads is a `--watch`,
   kept in the drive across restarts and held by the coordinator; a later `--watch` adds one.
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
6. The coordinator runs `drive dashboard`, which starts the drive's dashboard, finds it running, or
   replaces one an earlier install started, and prints its link.

A drive saved by the earlier one-session driver reads as that session coordinating;
[reference/migration.md](reference/migration.md#from-one-driver-to-two-sessions) moves it to two.

## Between the two sessions

Post with the cci `post` tool or `cci post`, naming the writer with `--lane communicator` or
`--lane root`, and address the other as `root@<drive>` or `communicator@<drive>`: a bare lane no
live session reads here can land on another drive. Each record names the thread it is about with
`--attr thread=<ts>`.

| Record | From | To | Carries | The receiver |
|---|---|---|---|---|
| `owner` | communicator | `root@<drive>` | his words verbatim, his message's permalink as `--url`, his thread, and `--attr ruling=<answer id>` on a ruling | makes them orders, or starts a skill rewrite for a ruling, by its table |
| `answer` | communicator | `root@<drive>` | `--re` the `ask` it answers, which closes it, `--attr job=<j>`, and his pick or his own words | records his answer and resumes that job |
| `defect` | communicator | `root@<drive>` | a tool defect it hit, any refusal verbatim | starts a builder on that tool's repository |
| `report` | `root` | `communicator@<drive>` | the facts of one message: the thread or alert link it goes in, what changed for him, links and proof | writes the message and posts it |
| `ready` | `root` | `communicator@<drive>` | a stack's PR URLs, bottom first | draws the stack's PR card |
| `ask` | `root` | `communicator@<drive>` | a job's `ASK` block verbatim, `--attr job=<j>`; an option that points at another by number or position is rewritten to say that option's words | posts the question card, its `waits` id `<drive>:<j>-<the record's seq>` |

## The communicator

Its turn on his message is two messages and nothing else. His rulings, Nov 9 4:07 PM: "ack and
whatot shouild be in parallel to starting", 5:02 PM: "you should be responding in 5-10 seconds for
simple things liek that", and 5:29 PM: "that took you 5 mins to respond sot hats unnaceptable
agian, figure out what regresdsed".

1. **Pickup and ack, alone**, inside 5 to 10 seconds: `slack_pickup` and the ack, and nothing else
   in that message. Calls in one message run only once the whole message is written, so a handler
   brief or a record beside the ack holds the ack behind its writing.
2. **The rest, in the next message**: `drive heard`, any record its row names, and the handler.
   Then the turn ends.

Every report, card, due line, draft, `check-post` and read of any kind, and anything that takes more
than one tool call, runs in a handler started in the background, which posts its own result. A turn
spent drafting, checking or drawing holds his next message behind it.

| Input | First message | Next message |
|---|---|---|
| A message from him | `slack_pickup` on it and an ack in the thread he wrote in: at most one sentence the communicator already knows, restating what he asked and what happens next, never read for, drafted or checked. A message typed in this terminal is the same, its ack posted at his DM root, which makes the thread. | `drive heard --thread <his thread>` (with `--away` when he says he is going to sleep or stepping away) and a handler for it. |
| A ruling: any message that says how the communicator, the coordinator, cards, messages or drives should work, a correction or a complaint about how something went included | The same. Its ack is the first of two confirmations: the skill is being updated, and what changes, in plain words. The second is the coordinator's `report` once the rewrite is merged and installed, listing each rule now in the installed skill. His ruling, Nov 9 2:08 PM: "confirm to me in the future when you're making a skill update based on a message I send". | The same. |
| Chatter ("ok", "thanks", "nice") | React `+1` in place of the ack. | `drive heard --thread <his thread>`, with no handler. |
| A tap on a question card (`slack.action` with `waits: true`) | An `answer` record with its `label` and `--re` the ask seq read from the `id`; a card with no ask record, such as one from before the split, takes `--topic card:<the card's thread_ts>` instead, with its `id` in the text. The card already shows his pick. | Nothing. |
| A tap on a PR card, its `waits` id opening `merge:` | Nothing: cc-slack records it on the drive's PR and the coordinator hears it as a `TAP` event. | Nothing. |
| A `report`, `ready` or `ask` record | A handler for each, with the record's seq as its brief. The communicator never reads, writes, checks or draws what a record carries. | Nothing. |
| The Agent tool returned a handler's id for a read-only question | One call records it, the turn's last, so the watch holds the answer to him: `drive job add reader --order "$(drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>)" --goal "<one line>" --agent <id>`. The handler runs `drive order posted` once its answer is in his thread. | Nothing. |
| A handler returned | Keep the lines it returned as context for his next message. `MISSING <path>` means a share found no reproduction file: an `owner` record to `root@<drive>` with his share words and that path, so the job that reproduced the bug saves the file, and its `report` comes back to a handler that shares it. | Nothing. |
| A compaction or a restart | The `ToolSearch` call the role's block names, which loads the Slack and cci tools: after a compaction their schemas are deferred, and loading them inside the pickup costs his ack a round trip. | One handler, briefed with each thread where he waits on a handler that has not returned, which posts one line in each: what is happening and when he hears next. |
| An `owner` record from the coordinator | His words, typed in the coordinator's terminal: handle them as a message from him, its ack at his DM root. | The same. |
| A tool defect you hit | A `defect` record. | Nothing. |

Start a handler with the Agent tool: `subagent_type` `long-running:handler`, `run_in_background:
true`, a short `description`, and never a `name`. Its `prompt` is a few lines: his words verbatim,
the permalink, the thread, the handler's scratch folder, the deliverable, and any path you already
know that answers it, such as a job's reproduction file. Never retype rules into it: the handler's
agent definition is its standing brief, ships with this skill and loads with the `subagent_type`.
The communicator's own calls are `slack_pickup`, the ack, a `+1`, `drive heard`, a cci post for a
tap, a defect or a `MISSING`, the Agent tool, the one `drive` call that records a read-only
question's handler, and the `ToolSearch` after a compaction. Everything else is a handler's, so his
next message is picked up inside 5 to 10 seconds while earlier ones are still being handled.

It compacts only at a quiet moment, never at its context limit mid-conversation, since a compaction
as his message lands holds the ack for minutes. The coordinator's `drive watch` does it: once the
communicator's context passes 120,000 tokens and both it and he have been quiet for 10 minutes, the
watch types `/compact` into the communicator's Orca terminal.

A handler reads what it needs, writes, checks and posts every line, card and share itself, and
returns what it said and learned, by its agent definition. Anything he waits on that takes it past
a minute gets one line in his thread first. A read-only question it answers itself, from the source
that owns the fact, never through the coordinator; his ruling, Nov 9 2:06 PM: "should not take 30
mins to spawn read only sonnet lanes". An order or an alert becomes an `owner` record, and a ruling
an `answer_add` and an `owner` record with `--attr ruling=<answer id>`.

A share is his order to post something in a thread he names, and that order is his permission for
one post there and nowhere else. The handler reads the path in its brief, or the reproduction file
`drive status` lists, directly, in at most 60 seconds and a handful of reads, posts once in that
thread, and puts the permalink in his thread. A missing file comes back as `MISSING <path>`, never a
rebuild from a transcript, and a share brief carries the thread and the paths, never a request for
a write-up. His ruling, Nov 9 3:50 PM: "all it should be is spawn a sonnet subagent tht does a
couiple reads and sends the slack mesage".

`drive heard` prints `CATCH-UP` when he is back after being away; the coordinator hears it as an
event and sends his catch-up as a `report`. A thread read, a transcript, a search, or anything
past the communicator's two messages is a handler's, never the coordinator's: the coordinator gets
only orders, alerts, rulings, his answers, defects and a question a card's `ask` record does not
answer.

## The coordinator

| Input | Do, in this turn |
|---|---|
| An order, in an `owner` record | `drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts> --parts "<part>"...` for each order in it, one part for each deliverable his words name (each stack, each design, each question); an order of one deliverable needs no `--parts`. Then give every part a background job now, in the order he stated them, each job naming the parts it delivers with `--part`. Every order goes to a job: a past-due one, a one-line one and a session close too. A part found later is `drive order set <o> --parts` with the whole list. Set `--due` for when he hears next. |
| A ruling, in an `owner` record: any message of his that says how the drive, its two sessions, cards or messages should work, a correction included | A ruling on arrival, in the same turn and unasked. `answer_add` it as the communicator's row says, unless the record's `ruling` attr names the answer it already added. When it changes how drives work, start a builder on yasyf/cc-skills that rewrites the section of this skill it touches, merged and installed; its `DONE` becomes a `report` for his thread listing, in plain words, each rule now in the installed skill. A reader job started for it finds every live job the ruling touches; send each the ruling with `SendMessage` when it returns. |
| A question of his, in an `owner` record, such as one a card's `ask` record does not answer | A `report` of what you have now inside 5 to 10 seconds, then the read in a background job when it takes more than one call, its result the second `report`. |
| An `answer` record | `answer_add` with the card's question as title and, as body, the option he tapped or his own words verbatim, never a paraphrase, a bot's summary, or the closest option standing in for words he wrote. A record with `maps=none` settles nothing: resume no job on it until he picks. Otherwise resume its job, or for a card from before the split the job that asked it: `SendMessage` to its agent with `drive job set <j> --state running`, or `drive job resume` for a remote lane. |
| A `defect` record | A builder on that tool's repository, by [Tooling](#tooling). |
| A message he typed in this terminal | An `owner` record of his words to `communicator@<drive>`, which acks it, marks him present and sends it back as his message. |
| A job finished, or a `JOB …` event | Read the first line of its result and do the matching row below. |
| A watch event | Do the matching row below. |
| An incident: his order with incident priority, an alert he sent in an `owner` record, or an alert in a held channel the drive's own release or change caused | [reference/incidents.md](reference/incidents.md), before anything else. After a release or a new build, in that turn the first check alone: how far the default branch is past the live build's commit, and whether its newest build works on a test machine, which makes releasing it the fix. Otherwise, or when that build fails too, a fix job whose first change stops users being blocked, a rollback job and a cause job that gates neither. He hears a line at least every 5 minutes until live. Any other alert gets nothing: no record, no job. |
| A record from another session | Answer it on cci as its kind asks. |

| Job result | Do |
|---|---|
| `DONE <proof>` | `drive order set <o> --state done --proof <proof>` once its last job is done; a remote lane also gets `drive job end <j>`, run in the background, which frees its Sprite. It goes into the next summary of his thread, or his catch-up when he is away: what changed for him, with the proof link. |
| `READY <pr url>...` | Read each PR body's `His words, step by step` list against his words in the order: every step he named sits beside the code that does it. A step missing or unmatched goes back to the job, with no card. Then `drive pr set <url> --tap` for each URL, and one `ready` record with every URL, bottom first, and the order's thread. A PR added to the stack later comes back as its own `READY` and gets its own card. |
| `WAITING <what>` | `drive job set <j> --state waiting --said "<what>"`. Nothing to him. The watch wakes a job through its open PR, its card or the order's due time; a wait on anything else, such as a release run, also gets `drive order set <o> --due` for when to look. |
| `ASK <question>` | `drive job set <j> --state waiting --said "<question>"`, then an `ask` record with the block verbatim, `--attr job=<j>` and the order's thread. Resume the job when his `answer` record arrives. |
| `BLOCKED <what>` | Decide. A broken tool gets a builder job on that tool's repo now, and the blocked work moves to a route that works. Never hand a broken tool to him. |

| Event | Do |
|---|---|
| `PR … checks failed`, `changes requested`, `new head` | Resume the PR's job with the event line. |
| `PR … merged` | Resume the PR's job to install and prove it live, or mark the order done when nothing is left to install. |
| `PR … closed` | Resume the job; if he closed it, the order's work on it ends. |
| `TAP Approve …` | Resume the PR's job: land it. A stack card's Approve arrives as one `TAP Approve` per PR, bottom first; resume the job once with all of them. |
| `TAP Hold …` | Resume the PR's job: hold it until he says otherwise. |
| `DUE o<n> …` | Read the order's job transcripts first, set the new time with `--due`, then a `report`: what is late, why, and the new time. While he is away it waits for his catch-up instead, except an incident's, which goes out at once by [reference/incidents.md](reference/incidents.md). Never call a job silent, stuck or done without reading its transcript. |
| `TICK o<n> …` | The watch ticks every 30 minutes, never while he is away, and lists only orders with news: the order or one of its jobs changed since its last line, or its promised time falls before the next tick. One `report` per thread, grouped by feature, covering every listed order in that thread (his main thread for orders with none): what is done since the last line and what is next, or that the promised time holds. Then `drive order set <o> --lined` for each. An order with nothing new gets no line. |
| `EVENT catch-up thread=… orders=…` | He is back. One `report` for that thread covering those orders, grouped by feature, with what waits on him last, each with its card's permalink so the communicator copies the card in, then `--lined` each. |
| `EVENT refused …` | cc-slack would not draw that PR's card, for the reason the line gives. The card is drawn from the PR, so resume the PR's job to fix the title or body, then send its `ready` again. |
| `EVENT unheard …` | He answered a card no session was listening to; the line carries the question, his pick and its link. Treat it as his `answer` to that card. |
| `ORPHAN o<n> "<part>": <why>` | A part of an open order has no live job and no open card: no job ever took it, its job finished with it left over, its agent or lane is gone, its card closed, a waiting job has nothing that can wake it, or a reader's answer is not posted to him. In this turn, start a job for it, resume or restart its job, or have its card drawn; an answer not yet posted goes to the communicator as a `report` read from its reader's transcript. A part already delivered or one he withdrew leaves the order with `drive order set <o> --parts` or the order closes with `--state done` or `dropped`. Never leave one standing: the watch says it again every 30 minutes and at its order's `DUE`. |
| `PR … still red for <n> minutes`, `green and approved with no card drawn for <n> minutes` | A PR that has not moved for 30 minutes. Resume its job with the line: a red one gets fixed, a green one gets its `ready` sent or, needing no tap, gets landed. |
| `JOB <j> <o> STOPPED …` | A remote lane ended its turn without a report. Read its screen (Jobs below), then resume it with what to do next. |
| `WATCH compacted the communicator …` | Nothing: the watch typed `/compact` into the communicator's Orca terminal at a quiet moment. |
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
returns: `drive job add <kind> --order <o> --goal "<one line>" --agent <id> [--part "<part>"]...`.
The watch reads the agent's transcript, never the stored state: a running job whose agent ended
its turn, wrote nothing for 30 minutes, or belonged to an earlier session makes its
parts `ORPHAN`s.

**Remote.** `drive job add builder --order <o> --goal "<one line>" --remote`, write the brief
to a file in your scratch directory, then run `drive job launch <j> <brief file>` from the
monorepo checkout's root with `run_in_background: true`. It starts the builder through the
`orca` skill's launcher as an Orca worker on a Sprite, on an Orca Run of its own that only this
drive's watch reads, so no other session takes its mail, and its notification says where it
started or why it could not. A lane that started no worker runs here instead, never
silently: start it with the Agent tool, record it with `drive job set <j> --agent <id>`, and
say so in the order's next report. A failure after worker-start ran may have left the lane
live, so a reader checks its attempt before anything starts it again. The lane reports through
Orca mail, and `drive watch` turns each report into a `JOB <j> <o> <line>` event that opens with
the `DONE`, `READY`, `WAITING`, `ASK` or `BLOCKED` a background agent returns; `drive status --json`
holds the whole report. A Sprite holds no Buildkite or AWS credentials, by his least-privilege
ruling, and no copy of the drive's cci or cc-notes records, so a lane reads through this Mac with
a blocking question: `LOG <url>` for a red Buildkite or GitHub Actions check, `READ <command>` for
a `cci` or `ccn` read the builder agent lists. The watch answers it itself, with no event: it runs
`tools/ci bk`, `ccx vcs job-log`, `cci` or `ccn` in the directory the coordinator started the
drive from and replies with the output, cut to 12,000 characters. A command off that list gets a
refusal naming the list. Its `WATCH read answer … failed` line needs nothing from you, since the
lane asks again when its wait ends. The watch registers each PR a lane's `READY` or `PR:` line
links as that job's, wanting his tap, and reads the lane's Dispatch from Orca each minute: a
lane that exited, finished, or sat at its prompt for 30 minutes makes its parts `ORPHAN`s. An
`EXEMPT:` line still gets `drive pr set <url> --exempt <class>`.

The brief is the job's whole context, the same for both:

```
Order (his words): <verbatim>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it, e.g. installed and the installed version contains the merge>
Where: <repo, base branch, existing branch or PR to adopt>
Decisions that bind: <every ruling of his that touches this, his words verbatim with their time, from answer_search>
Plan: <the plan section this implements, quoted>
Incident: <an incident job only: from the merged fix to its build nothing stops, no dry run, no cover or cause read, no comparison of open builds, no question; start the build at the fix commit with the cut reference/incidents.md names and report its link; the approve gate is the only check and reads run while it builds; return the inputs to a write-up, not the write-up>
Scratch: <the job's scratch folder>
Transcripts: <the transcript path of every earlier job on this order, so it reads instead of being told>
Drive: <name>, order <o>, job <j>
```

A job's result goes to another agent, never to him, so it is never a write-up: its first line is
`DONE`, `READY`, `WAITING`, `ASK` or `BLOCKED`, and the rest is the inputs to the message he would
get, one fact a line: links, numbers, paths, exact quotes, and what it could not prove. Only the
handler that writes to him writes prose. His words, Nov 9 4:14 PM: "agents that are responding to
agents shoudnt do full himan-quality write ups they just need to dump their context back … I mean
the inputs to their would-be wriute ups".

- **His words, never a stand-in.** A brief's `Order` and `Decisions that bind` carry what he wrote,
  verbatim, and so does every cc-notes answer: never a paraphrase, a bot's summary, or a card
  option's label standing in for words he wrote. A tap's label is his answer only to that card's
  question.
- **His approval of a release approves it, right then.** His order or card pick to ship, release or
  approve a release approves that release and its approve gate at once, migrations, deletes and
  replaces included: the job passes `--order <his answer id>` to `tools/ci release start`, or runs
  `tools/ci release approve <id> --via <agent>` the moment it holds at approve, with no second card,
  check or question in between. His words, Nov 9 5:25 PM: "what? no we want he oposite if I apprtove
  a release dont fuckl around kjust appriove it". No agent approves a release or its gate without an
  approval of his for that release, and a release he started himself is his: never cancel, retry,
  reorder or approve it. An incident's order approves its own cut and rollback, by
  [reference/incidents.md](reference/incidents.md).
- **Resume, never respawn.** Send the next event or answer to the job: `SendMessage` to its
  agent id here, `drive job resume <j> "<event or answer>"` to a remote lane, which answers the
  question it is waiting on. A finished agent resumes with its history. Start a fresh job only
  when the agent is gone (a restart) or its context is spent, with a handoff of branch, PR, and
  last line.
- **A reproduction leaves one file.** A job that reproduces a bug, of any kind, saves one
  pick-up-and-run file before it returns: the steps in order, the exact prompt and code, the build
  and versions, and the numbers measured. It records it with `drive job set <j> --repro <file>`,
  which keeps a copy in the drive, and `drive status` lists it, so a share reads that file in
  seconds instead of rebuilding the steps from a transcript. A brief for such a job says so.
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
- His answer is the option he tapped, or his own words when he writes one: a written reply that
  names no single option settles nothing, is never mapped to the closest option, and goes back to
  him under the card. No option asks a reporter or anyone else to retry or confirm.

## Messages

Every message follows [reference/messages.md](reference/messages.md). The short form:

- Lead with what changed for him, in his words, never with how the machinery got there.
- Ack each of his messages inside 5 to 10 seconds, alone with the pickup in the communicator's
  first message. A question of
  his that reaches the coordinator gets a `report` of what it has in the same time, and the read
  follows in a second `report`. Every other post is a handler's, a summary at most every 30 minutes
  per thread, grouped by feature. While he is away only cards post, and his next message gets one
  catch-up.
- A bold line and at most two sentences. A table only for three or more parallel items.
- Name each PR by its title, linked. No ids, hashes, record numbers, lane or job names,
  file paths, or code identifiers.
- Done means live and checked; say how in a few words, with the link. An incident line carries only
  a step of the fix with its time, its proof on a real machine and users having it, or what is
  running and when the next line lands, plus one cause line once a test machine reproduces it:
  nothing unproven and no side facts about a release.
- Any line that says something waits on him, in a summary, a catch-up or a link line, carries that
  card with its buttons in the same thread, never a link alone. His ruling, Nov 9 1:49 PM: "Anything
  that says waiting on me you always need to embed the card with the buttons". Nothing else has
  buttons.
- In a bug report's thread the drive posts once, after the fix ships: plain English, no jargon, and
  no asks such as "please retry" or "please confirm". His ruling, Nov 9 1:53 PM: "we have
  authorization to message once in bug fix threads after we ship the fix … it needs to be in plain
  simple English no jargon, no crazy shit, and no asks". No card option offers to ask a reporter
  anything.
- One copy of everything. A correction edits the message in place.
- A time promised to him is set with `--due` by the coordinator in the same turn, so the watch
  holds the drive to it.
- A handler runs `cc-slack check-post` on every line it posts and fixes what it flags; the ack, one
  sentence the communicator already knows, goes out unchecked.

## Tooling

A tool defect either session hits gets a builder job on that tool's repo in the same turn: the
coordinator starts it, from a `defect` record when the communicator hit it, and the work it
blocked moves to a route that works meanwhile. It is never an excuse and never a question to him.
Before a fix, search cc-notes papercuts for the same symptom: the third fix for one symptom is a
class fix whose PR deletes the earlier patches.

## Never

- The communicator's turn is two messages: the eyes and the ack alone inside 5 to 10 seconds,
  then `drive heard` and the handler. Every read, report, card, due line, draft, `check-post`,
  share and post past the ack belongs to a handler. It never starts a builder or waits on
  anything, and only orders, alerts, rulings, his answers, defects and a question a card's `ask`
  record does not answer go to the coordinator.
- Neither session treats his correction about how the drive works as a one-off: it is a ruling,
  recorded and sent for a skill rewrite in the turn it arrives.
- The coordinator never posts to Slack, and never does an order's work itself, a past-due order's
  included: every order goes to a background job. Its own shell calls are reads of `drive`, `cci`
  and one `gh` call, beside the transcript reads and the remote-lane `drive job` verbs this skill
  names. A loop, any other `orca` call, a build, a close, a thread read, a draft, and anything else
  runs in a background job, so its turn never blocks. His read-only questions are the
  communicator's, never the coordinator's.
- Never send a record or post a line per event: events feed the next summary, and nothing but
  cards and incident lines goes out while he is away.
- Never stop, signal, or close another session unless he named it.
- Never run tests locally; CI grades.
- Never hold, freeze, or reorder work he did not hold, freeze, or reorder. A build or release he
  started is his: never cancel, retry, reorder or approve it, and start no release while it runs.
  One of ours that must stop gets a `report` to the communicator first, then its job stops it.
- Never re-derive state from memory or infer it: `drive status`, GitHub and transcripts are the
  truth, and what a release carries (its PRs, the steps it runs, which build fixes what) is read
  from that release's commit and build.
