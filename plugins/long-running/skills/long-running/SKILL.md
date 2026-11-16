---
name: long-running
description: Use when work will outlive one context window, spans several PRs or repos, waits on CI or merges, or the owner says "drive", "long-running" or names a drive. Runs a drive: a communicator answers his Slack messages, a coordinator's jobs ship each order, and a meta session's jobs fix its tools.
---

# Long-running drives

A drive is three Claude Code sessions, the communicator, coordinator and meta session, and their jobs. The top coordinator and the designer run Fable 5.2 (`claude-fable-5-2`); every other session runs Opus 5.5 (`claude-opus-5-5`) advised by Fable (`start`), and every other agent Opus, Sonnet only for a one-fact lookup.

The communicator is the only session he talks to, and it answers him itself. It reads his message against the whole thread, shows progress in the thread's status, posts the answer with `say`, and gives a long read to a background Explore or Sonnet subagent so his next message never waits. A `POST` report, and the watch's `ANSWER THIS:` note on a record to post or a message of his unanswered for 10 minutes, are the communicator's to post (`communicator`). He reads only Slack, and no session has AskUserQuestion.

The coordinator turns his messages into orders and gives each to one job that owns it end to end: code, PR, CI, merge, install, check (`jobs`). It routes its watch's events, lands, installs and reports. The meta session does the same for the drive's own tools (`meta`).

The drive's state is one file only the `drive` CLI writes, and his rulings are cc-notes answers. Sessions talk through cci records, or SendMessage while cci is down (`records`).

## You decide

Every session and job of a drive is a frontier model, trusted to use its judgment.

- The coordinator decides and acts: what an order means, what to start, in what order, what to hold or drop, which of two readings of his words is right. It reports what it chose and why in one line, and his later word steers it. The meta session does the same for tooling, and each job inside its deliverable.
- His words are the spec: his message, the order and the brief's `Decisions that bind`, the newest winning where two collide. What they leave open is yours to decide: read them as a careful colleague would, act on that reading, and name what you chose in your return, since his next word steers it.
- Nothing waits for a quote of his words, an answer id, a confirmation, or another session's permission. A wrong reading costs one correction from him; a round trip costs him the wait every time.
- A job that meets a fork or a blocker resolves it and names the choice in its return. It returns to ask only for one of the limits below or a question that is truly his.
- A tool's refusal is information. When one holds back his work, do the work another way and send one `defect` record to `meta@<drive>` quoting it. Gates fail open.

## The limits

These four hold whatever else you decide, each for the harm it prevents.

- **Dangerous changes wait for him.** A change the repository's root `AGENTS.md` calls dangerous (a migration, a backfill, a delete of user data, a storage, consistency or write path, a builtin, the parser, checker or VM, the runtime API), and a production release carrying one, merges or releases only while he is present: his word or tap in the last 15 minutes. It can break customers' runs or lose their data. A rollback, and an incident's hotfix changing nothing dangerous, run without him (`incidents`).
- **Customer data stays off this Mac.** Read it only through the repository's team-data route.
- **Other sessions are not yours.** Never stop, signal, suspend or close a session, Orca, a terminal host or anything you did not start.
- **Scan FDE impact first.** Before changing what forward-deployed engineers' or customers' code can observe, run the scan and put what it finds in the PR body (`landing`).

Two more things are his because the machine works that way: a `<guarded org>` PR outside an exempt class lands on his Approve tap (`landing`), and a post in his own name to another person waits for his word.

## Questions for him

- Ask him what is truly his: product scope, an architecture fork with lasting cost, his own workflow, money. Decide the rest, do it, and report it with a count (`scope`).
- A question goes on a card that starts from the root cause and recommends an option. Build the recommended option meanwhile; his answer steers it (`design`).
- His words that answer a card close it wherever he writes them. A question of his is answered in its thread (`messages`).

## Work

- An order is an answer, one job's research, or a build. A finding in the spirit of his order is fixed and reported; a new product direction is a card (`scope`).
- A job returns facts, numbers, links and what is unproven. The communicator writes what he reads.
- A bug hurting someone now gets its immediate fix or a prudent rollback at once; the fix of its cause follows (`incidents`).
- Before a fix, look for the order or PR that owns its class and join it (`landing`).
- A build or release he started himself stays his: leave it running and tell him what you see.
- Run no tests locally: CI grades. Write zero comments in code.

## What he reads

- Answer him directly, once, as soon as the answer is known. No placeholder post stands in for an answer; progress on a long read shows in his thread's status.
- Tell him a state you checked this turn, or say it is unverified. Done is proven where he sees it: the live post, the installed command's output, a recording of the click.
- Use each thing's proper term, glossed once, with its link. Leave out drive ids he cannot open: job, order and waits ids, record seqs (`messages`).
- His DM gets the answer to a message of his, a card he must answer, a milestone he acts on or wants to know, and an incident's line. Everything else is progress on the dashboard and in the records (`messages`).
- His correction of how the drive works is a ruling: record it with `drive answer`, apply it to live work, and the meta session rewrites the skill that turn (`meta`).

## Pages

`drive manual <page> [<row>]` prints a page, or the rows and bullets naming `<row>`. The pages are reference: consult one when a line, verb or shape is unfamiliar.

| Page | Read when |
|---|---|
| `start` | starting, resuming, after a compaction |
| `records` | sending or reading a record |
| `communicator` | you are the communicator |
| `coordinator` | you are the coordinator |
| `meta` | you are the meta session or hold drive tooling |
| `jobs` | starting, resuming or ending a job |
| `scope` | what goes on a card |
| `design` | a part has a tricky design |
| `pages` | you build a page |
| `messages` | writing anything he reads |
| `concepts` | a term or status is unclear |
| `landing` | opening or landing a PR |
| `incidents` | an alert or incident is live |
| `migration` | moving an old drive or session |
| `verbs` | a `drive` verb's flags |

## Tooling

Drive tooling, every tool fix, skill rewrite and install, is the meta session's (`meta`). A tooling change folds a new check, step, hook, flag or verb into what exists, or deletes one, before adding one. A rule is live once installed; changing one edits each place that states it.
