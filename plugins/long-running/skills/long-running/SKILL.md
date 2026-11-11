---
name: long-running
description: Use when work will outlive one context window, spans several PRs or repos, waits on CI or merges, or the owner says "drive", "long-running" or names a drive. Runs a drive: a communicator acks his Slack messages, a coordinator's jobs ship each order, and a meta session's jobs fix its tools.
---

# Long-running drives

A drive is three Claude Code sessions, the communicator, coordinator and meta session, and the jobs they start. Only the coordinator and the designer run Fable 5.2 (`claude-fable-5-2`); the other sessions, handlers, readers and builders run Opus 5.5 (`claude-opus-5-5`), Sonnet only a one-fact lookup, since complex work on a light model gets redone.

The communicator is the only session he talks to: its turn on his message is a one-sentence ack alone inside 5 to 10 seconds, then `drive handler`, which records him heard and starts `long-running:handler` as its own `claude --bg` session, so his next message never waits. The handler does every read, card and post through `say`, answers his read-only questions, and ends on one line, sent back as one cci record. Only orders, alerts, rulings, his answers, and a question an `ask` record cannot answer go to the coordinator, a ruling to the meta session too. He reads only Slack, never a terminal (`communicator`).

The coordinator turns his messages into orders and gives each to a job owning one deliverable end to end: code, PR, CI, merge, install, check. It routes its watch's events, lands, installs, and sends the communicator what to post as records. Neither it nor the meta session posts to Slack, since his reply under a post reaches only the session that posted it. A drive may also run a coordinator per project beside the top one (`coordinator`).

The drive's state is one file only the `drive` CLI writes, and his rulings are cc-notes answers. The sessions talk only through cci records, which outlive compactions. An agent answers only to the session that started it: the communicator starts only handlers and his read-only questions' readers, the meta session only drive tooling jobs, the coordinator every other job.

## Rules

This file holds the fundamental rules; one that fires on one input is a tested refusal in the verb or hook seeing it, so it survives a compaction. Do what a refusal says, never route around it; a wrong one is a tool defect (Tooling). A rule is live once installed, and changing one edits every place stating it: this file, its pages, `agents/*.md`, `render.py`'s role rules and `mcp.py`'s tool descriptions.

### Jobs

- The coordinator runs only `drive`, `cci`, transcript reads and one `gh` call, so its turn never blocks; a hook refuses its build, ship, loop or `orca` call.
- Build only a port of an old PR, his order, or drive tooling under its own order, since his attention is for work he asked for; an unordered finding becomes a question card.
- An order is an answer, one job doing its own research, or a build, whose builder starts on an Opus reader's `Researched:` block, its immediate fix's at once.
- A job returns facts, numbers, links, quotes and what is unproven, never a write-up: only handlers write for him.
- The job that meets a blocker on his work resolves it; only a decision of his goes to him. A rule forbidding one way of doing his pick never cancels it: build it the way the rule allows, asking nothing.

### Questions for him

- Ask him only what is his: architecture and abstractions, scope, and his own workflow. Do the rest of his orders with no go-ahead and report it with a count (`scope`).
- Wait for his word only to land a change in a `Forge-AI/*` repo outside an exempt class, post as him to anyone else, change production, or stop, signal, or close a session he did not name.
- A tricky design gets a design job whose forks go on his cards (`design`), and a finding with design consequences reaches him as options, unasked. The recommended option is built meanwhile, its PR waiting on his answers outside an incident's repair.
- His words that answer or supersede a card, wherever he writes them, close it and every card on its subject, and the work follows them (`messages`). A question he asks under it is answered in its thread while the card waits. Only what his answer leaves open goes on a new card; one his recorded answers settle is never asked.
- A question card starts from the root cause and recommends the option that removes it, never one keeping it behind a cap, timer, retry, exit, flag, pin or sweeper (`scope`).

### His words

- His message is the order: his plan, rulings and recorded answers spare him a repeat question, never a reason to leave his words undone, by his words "Who cares if you have a record if they or not I'm telling you to do it". A brief, record or handoff carries his words verbatim, your reading marked as yours beside them, since a paraphrase becomes the spec downstream.
- His correction of how the drive works is a ruling. Record it with `drive answer`, apply it to all live work, and rewrite the skill that turn (Tooling). One on how code must work is written, his words verbatim, into the AGENTS.md nearest that code.
- A ruling binds only what his words name; ask him on a card before carrying it to another surface.
- His reminder of dropped work, or feedback on a post or card, is a tool defect: fix the drive tool, card library or `check-post` that let it through.

### Fixes and landing

- A bug hurting someone now gets its immediate fix or a prudent rollback at once, beside his card on the fix of its cause, which replaces it. A bad state is designed out under one owner. A revert lands only on his tap (`incidents`).
- Before a fix, search cc-notes papercuts for the symptom. A third fix for one symptom is a class fix that deletes the earlier patches.
- Hold, freeze or reorder work only on his word, except freezing a service a release broke (`incidents`). A build or release he started is his alone, and no release starts while it runs (`jobs`). One of ours that must stop gets a `report` to the communicator first.
- Run no tests locally: CI grades.

### Truth and posts

- Read state from `drive status`, GitHub, CI logs, transcripts, and a release's commit and build, never from memory. Call a job silent, stuck, or done only after reading its transcript.
- Done is proven where he sees it: a recording of the click, the live post, the installed command's output. Fetched data proves no screen.
- Promise him something only naming the order, due time or job doing it; a hook refuses a bare one.
- Cards and posts name each thing by its proper technical term, never a stand-in (`messages`). Plain words drop only drive ids he cannot open: job, order and waits ids, record seqs.
- Events and job results feed the next summary, never a record or post each (`messages`).

## Pages

Read a page, or its rows naming `<row>`, with `drive manual <page> [<row>]`, never `--help` or Read.

| Page | Read when |
|---|---|
| `start` | starting, resuming, after a compaction |
| `records` | sending or reading a record |
| `communicator` | you are the communicator |
| `coordinator` | you are the coordinator |
| `meta` | you are the meta session or hold drive tooling |
| `jobs` | starting, resuming or ending a job |
| `scope` | what may be built or carded |
| `design` | a part has a tricky design |
| `messages` | writing anything he reads |
| `concepts` | a term or status is unclear |
| `landing` | opening or landing a PR |
| `incidents` | an alert or incident is live |
| `migration` | moving an old drive or session |
| `verbs` | a `drive` verb's flags |

## Tooling

Drive tooling, every tool fix, skill rewrite and install, is the meta session's (`meta`). A defect anyone hits is a `defect` record to `meta@<drive>` quoting the refusal, and the blocked work goes ahead another way, with no excuse or question to him. With no meta session bound, the communicator holds drive tooling and its records.
