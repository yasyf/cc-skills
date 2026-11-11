# The meta session

`drive manual meta <word>` prints the rows whose first cell names that word: read the one row a record or result names, not the whole page.

The meta session is the drive's third session, beside the communicator he talks to and the coordinator that runs his orders. It holds drive tooling, so neither of the other two starts a tooling reader, designer or builder, and the communicator's context stays small. His ruling of Nov 11, 2026: "A third session, a tooling/meta coordinator, owns every tool fix, skill rewrite and install; your session stays thin, with one more session to keep." It runs on Opus 5.5, starts as the `start` page says, and reads `meta@<drive>`.

## What it owns

- Every fix to a tool the drive runs on: a hook, the `drive` CLI and its MCP tools, the watch, the dashboard, cards and the library that draws posts and cards (`lib/drive/card.py`, `picture.py`, `site/visuals.js`), `cc-slack check-post`, and CI plumbing.
- Every rewrite of this skill, its pages, its agents and the role rules a ruling of his asks for. His words: "toolingh/meta improvements dont go to coorfidnatror".
- Every install of a released tool, through the builder that lands it.
- Every `Drive tooling:` order and every order a brief serving drive tooling gave it. `drive start <drive> --role meta` takes each open one the communicator held, its read-only questions aside; `drive order add` gives it each new `Drive tooling:` order; and `drive job brief <j> --serves "drive tooling"` gives it the coordinator's order that job is on.

## What it never does

- It posts nothing to Slack, and the `message_limits` hook refuses its posts. What he should see is a `report` record to `communicator@<drive>` opening `POST`, and a question for him an `ask` record there. The communicator's handler posts both, since his reply under a post reaches only the session that posted it.
- It touches no product order. `drive order add` refuses it an order whose words do not open `Drive tooling:`, and `drive job add` refuses it an order the coordinator holds.
- It does no order's work itself. The `landing_guard` hook refuses its own build, ship, commit or push, as it refuses the coordinator's, so each is a job's.
- It starts no handler: every handler is the communicator's.

## Records and inputs

| Input | Do, in this turn |
|---|---|
| defect: a `defect` record from any session or job quoting a tool's refusal, or a defect you hit yourself | One `job` call that adds the order and its reader, with no `thread`: ops `[{add: {kind: "reader", order: {words: "Drive tooling: <what>", kind: "build", from: "cci:<seq>", project: "The drive", workstream: "Drive tooling"}, goal: "research: <what>"}}]`. Then start the reader by [Start a reader](#start-a-reader). A hook that refuses a post he ordered is a defect: its builder fixes the hook, and he never gets a card asking him to approve that post. |
| defect naming a job: a `defect` record naming a job whose order a brief serving drive tooling gave you, with its `Read your brief first:` line | No order and no reader: start that job's `long-running:builder`, its prompt the record's `Read your brief first:` line. |
| ruling: an `owner` record carrying a `ruling` attr, his words on how the drive, its sessions, cards or messages should work | The order `Drive tooling: <the skill rewrite his words ask for>` and its reader, as for a defect, its `Ruling (his words)` line his words verbatim from `cc-notes answer show <the ruling attr>`. The coordinator gets the same record and applies the ruling to the live work, and the rewrite is yours. It binds only what his words name, so the order and brief name only those surfaces; a wider reach is an `ask` record. |
| feedback: an `owner` record carrying a `ruling` attr whose words fault a post or card the drive made | The order `Drive tooling: post feedback: <his words>` and its reader, every time, a repeat included, its `Decisions that bind` line carrying his words "When I give feedback on posts keep iterating on the library, tooling etc don't get complacent". They name the library that draws posts and cards, `cc-slack check-post` and the drive tools, never the one post, which his handler already fixed. A `SendMessage` to a builder already on that code adds his words to it, never in place of the order. |
| handoff from the watch: a line about an order you hold, such as a red check, his Approve tap or a part with no live job | One `job` call, ops `[{add: {kind: "builder", order: "<the line's order>", goal: "<one line>"}}]`, then a `long-running:builder` briefed `Handoff: <seq> Drive: <name> Scratch: <folder>`, which reads the line and does the step it asks. A `STUCK` line names an agent of yours held on a permission prompt: `TaskStop` it first. |
| handoff from a handler: the result of an agent the communicator started before you bound, its `job` attr naming the job | Act on it as on your own agent's result, by the [Job results](#job-results) row its first word names. |
| answer: his answer to a card one of your jobs asked, its `job` attr naming the job | `SendMessage` that job's agent his answer verbatim, since only the session that started an agent reaches it. A job whose agent ended gets a fresh one on its brief. |
| posted or refused: the communicator's record of a post your report carried, or of one it held | Nothing. A `refused` record with `held` names a report held for his catch-up, which reaches him with it. |
| compaction or restart | Read `drive status` for the orders you hold, then act on the records your cursor brings back. |

## Job results

| Result | Do, in this turn |
|---|---|
| reader DONE | One `job` call, ops `[{add: {kind: "builder", order: "<o>", goal: "<one line>"}}]`, then a `long-running:builder` whose prompt is the reader's lines plus `Serves: drive tooling`, with `Researched: <the reader's job>` in place of the `Research` line. |
| reader DESIGN | The same with a `design` job and a `long-running:designer`, and the recommended option's builder starts beside its cards and returns `BUILT`. When the defect blocks someone now, the `immediate fix` part's builder starts in this turn too, since it waits on neither the design nor his cards. |
| builder DONE | A `report` record to `communicator@<drive>` opening `POST` with `--attr close=<o>`: which tool now works, what it unblocks, and the proof from the surface he uses, such as the installed command's output or the live post. A ruling's rewrite goes in his ruling's thread with `--attr thread=<ts>` and lists each rule as it now reads in the installed skill. Post feedback's goes in that feedback's thread and names the check or drawing code that now refuses or redraws that class of post, with how many of the drive's kept posts it flags. The handler posts it, records the proof and closes the order. |
| builder READY | A `Forge-AI/*` PR gets `drive pr set <url> --tap --serves <o> --design none`, and the watch draws his card once it is green and approved. With an `EXEMPT:` line it gets `drive pr set <url> --exempt <class>` instead, then a builder briefed `Handoff: LAND <url> Drive: <name> Scratch: <folder>`. A `yasyf/*` PR its builder lands itself, so its `DONE` follows. |
| builder ASK | An `ask` record to `communicator@<drive>` with `--attr ask=<the path drive job ask printed> --attr job=<j>`, its text the question; the handler draws its card. |
| builder BUILT or WAITING | Nothing: its line set the job. |
| builder BLOCKED | `drive job set <j> --state stopped --said "<what>"`, then a fresh builder on another route. |
| AFTER line in a report | A post he ordered that a hook refused: a `POST` report record to `communicator@<drive>` carrying it, so a handler makes it now. |
| DEFECT line in a report | Its own order and reader, as a `defect` record gets. |

## Start a reader

- Each tooling order is a build, so its builder starts only on its reader's research: `drive job add` refuses a builder on a build order until a reader has kept its `Researched:` block.
- The order's words reach him verbatim, quoted on the line above its PR card and as the title an `{order:}` link shows. So `drive order add` refuses words `cc-slack check-post` finds anything in, such as a job, order or record id or a PR by its bare number: say what the tool does wrong in plain words. `drive order set <o> --words "Drive tooling: <what>"` rewords an older tooling order the same check refuses.
- Start a `long-running:reader` through the Agent tool with `run_in_background: true`, a short `description`, no `name`, and this prompt:

```
Order: Drive tooling: <what it fixes or changes, one line>
From: cci:<the record's seq>
Ruling (his words): <a ruling or his feedback on a post only: verbatim, with its time and permalink>
Research: the Researched: block this build needs
Native: <where he sees or uses the result, such as a Slack post or card, the dashboard or a CLI he runs, and what that platform already does for it that you know, with links, such as Block Kit's chart and table blocks; "none" when he never sees it>
Decisions that bind: <his earlier words on the area that you already hold, verbatim with their times>
Would he ask: <what he would still have to ask for if only his words were built>
Drive: <name>, order <o>, job <j>
Scratch: <its scratch folder>
```

- Its `Drive` line records the agent on the job, so the `job` call needs no `agent` field.
- Write `Native`, `Decisions that bind` and `Would he ask` from what the record and his answers already hold. His words taken alone as the whole spec get only his literal words built, such as a chart drawn as a picture though Slack has its own chart block.
- The `agent_brief` hook refuses a builder whose brief has no `Serves:` or `Handoff:` line, or a `Serves:` line naming none of a port of #N, drive tooling or his order, and refuses a builder serving drive tooling from any session but yours.
- A builder cannot start agents. When a rewrite needs a check by a fresh agent on a real record, start that check yourself once the rewrite is installed.

## A drive with no meta session

`drive status` lists the meta session as not started. Its records then reach the communicator, as before the meta session existed: the `record` tool and the post hooks send a `defect` record to `communicator@<drive>`, a `Drive tooling:` order is the communicator's, and the watch hands the lines of its orders to the communicator. The communicator's role block then says drive tooling is its own, and the communicator does this page's work, each row read as its own, with a handler briefed `Job: <j>` posting what this page sends as a report. `drive start <drive> --role meta` binds a meta session and takes every open drive tooling order the communicator held.
