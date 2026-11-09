---
name: long-running
description: Run a long drive from one Claude Code session, the driver. The owner talks to it in Slack and the terminal; every order becomes a background agent that ships one deliverable end to end (code, PR, CI, merge, install, check); one watcher turns CI, merges, his PR-card taps, promised times and a 10-minute tick into events; every message he gets is short, plain and written by the driver. Use when the work will outlive one context window, spans several PRs or repos, waits on CI or merges, or when the owner says "drive", "long-running" or names a drive.
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
| Job | One background agent owning one deliverable: `long-running:builder` (one change, code to installed), `long-running:reader` (one question, read only), `codex:codex-wrapper` (review, diagnosis, incidents). `drive job …` |
| Event | A line from `drive watch`: `PR …` state changes, `TAP …` on his PR cards, `DUE …` when a promised time passes, `TICK …` when an order owes a progress line. |
| Message | A line in his thread, a question card at his DM root, or a PR card that cc-slack draws from the PR. Nothing else. [reference/messages.md](reference/messages.md) |

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
| A message from him | First call: `slack_pickup` on it, so he sees eyes and a working line. Then classify it: order, question, ruling, answer, or chatter, and do that row. Reply in the thread he wrote in; a terminal message gets its reply in Slack too. |
| An order | `drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>`. Give it to a background job now, in the order he stated them. Every order goes to a job: a past-due one, a one-line one and a session close too. Your own calls for it are reads of `drive`, `cci` and one `gh` call; a loop, an `orca` call, a build or anything else runs in the job. Reply within the minute: a bold line restating the order, one sentence on what happens, and when he hears next, set with `--due`. |
| A question | If `drive status`, one `gh` read or one transcript read answers it, answer in his thread now. Otherwise start a reader job and answer when it returns. Never make him wait on work he did not ask about. |
| A ruling on how things should work | `answer_add` with the question verbatim as title, his words as body, labels `scope:durable` and `drive:<name>`. Apply it this turn to every live job it touches (`SendMessage`) and every open card. If it changes how drives work, start a builder on yasyf/cc-skills that rewrites the section of this skill it touches, merged and installed. Reply in one line: what changes and when it is live. |
| An answer or tap on a question card | `answer_add` with the card's question and his pick. Resume the job waiting on it with `SendMessage`. Reply only if something changes for him. |
| Chatter ("ok", "thanks", "nice") | React `+1`. No reply. |
| A job finished | Read the first line of its result and do the matching row below. |
| A watch event | Do the matching row below. |
| An alert or incident | [reference/incidents.md](reference/incidents.md), before anything else. |

| Job result | Do |
|---|---|
| `DONE <proof>` | `drive order set <o> --state done --proof <proof>` once its last job is done. Line in his thread: what changed for him, with the proof link. |
| `READY <pr url>` | `drive pr set <url> --tap`, then `slack_dm_status(tap_prs=[url], text=<line>, thread_ts=<his thread>)`. cc-slack draws the PR card at his DM root from the PR itself, and the line in his thread links it. |
| `WAITING <what>` | `drive job set <j> --state waiting --said "<what>"`. Nothing to him. The watch wakes you. |
| `ASK <question>` | A question card (it waits on him, so `waits` and `recommended` are set); a link line in his thread. Resume the job when he answers. |
| `BLOCKED <what>` | Decide. A broken tool gets a builder job on that tool's repo now, and the blocked work moves to a route that works. Never hand a broken tool to him. |

| Event | Do |
|---|---|
| `PR … checks failed`, `changes requested`, `new head` | Resume the PR's job with the event line. |
| `PR … merged` | Resume the PR's job to install and prove it live, or mark the order done when nothing is left to install. |
| `PR … closed` | Resume the job; if he closed it, the order's work on it ends. |
| `TAP Approve …` | Resume the PR's job: land it. |
| `TAP Hold …` | Resume the PR's job: hold it until he says otherwise. |
| `DUE o<n> …` | Read the order's job transcripts first, then a line in his thread: what is late, why, and the new time, set with `--due`. Never call a job silent, stuck or done without reading its transcript. |
| `TICK o<n> …` | One progress line per listed order, in its thread: what is done since the last line and what is next. Then `drive order set <o> --lined`. |
| `EVENT refused …` | cc-slack would not draw that PR's card, for the reason the line gives. The card is drawn from the PR, so resume the PR's job to fix the title or body, then draw it again with `tap_prs`. |
| `EVENT unheard …` | He answered a card no session was listening to; the line carries the question, his pick and its link. Treat it as his answer to that card. |

## Jobs

Start every job with the Agent tool: `subagent_type` `long-running:builder`,
`long-running:reader` or `codex:codex-wrapper`, `run_in_background: true`, a short
`description`, and the brief as `prompt`. **Never pass `name`.** Agent teams are on here, and a
named agent becomes a teammate that never wakes on events. Record the agent id the call
returns: `drive job add <kind> --order <o> --goal "<one line>" --agent <id>`.

The brief is the job's whole context:

```
Order (his words): <verbatim>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it, e.g. installed and the installed version contains the merge>
Where: <repo, base branch, existing branch or PR to adopt>
Decisions that bind: <every ruling of his that touches this, verbatim, from answer_search>
Plan: <the plan section this implements, quoted>
Drive: <name>, order <o>, job <j>
```

- **Resume, never respawn.** Send the next event or answer to the job's agent id with
  `SendMessage`; a finished agent resumes with its history. Start a fresh job only when the
  agent is gone (a restart) or its context is spent, with a handoff of branch, PR, and last line.
- **One deliverable per job, and no job waits on another.** When B needs A merged, B starts on
  A's `DONE`.
- **A sweep over many units is one job running a Workflow**, started by you, since a
  background agent cannot start agents.
- **A job's state is its transcript.** Before telling him anything about a running job, read
  its transcript with `cc-transcript`; the agent id names its file. An absent message is
  never evidence.
- Keep at most eight builders running at once on this Mac; later orders wait as `open`, oldest due
  first.

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
  the transcript reads and `cc-slack check-post` this skill names; a loop, an `orca` call, a
  build, a close, and anything else runs in a background job, so your turn never blocks.
- Never stop, signal, or close another session unless he named it.
- Never run tests locally; CI grades.
- Never hold, freeze, or reorder work he did not hold, freeze, or reorder.
- Never re-derive state from memory: `drive status`, GitHub and transcripts are the truth.
