---
name: long-running
description: Use when work will outlive one context window, spans several PRs or repos, waits on CI or merges, or the owner says "drive", "long-running" or names a drive. Runs a drive: a communicator acks his Slack messages, a coordinator's jobs ship each order, and a meta session's jobs fix its tools.
---

# Long-running drives

A drive is three Claude Code sessions, the communicator, coordinator and meta session, and their jobs. Only the coordinator and the designer run Fable 5.2 (`claude-fable-5-2`); the rest run Opus 5.5 (`claude-opus-5-5`), Sonnet only a one-fact lookup, since complex work on a light model gets redone.

The communicator is the only session he talks to: its turn on his message is a one-sentence ack alone inside 5 to 10 seconds, then `drive handler`, which starts `long-running:handler` as its own session, so his next message never waits. The handler does every read, card and post through `say` and ends on one line, sent back as one cci record. Each `owner` record names its `about`: his words on the drive, rulings included, reach the meta session alone, and those on his work the coordinator. He reads only Slack; no session has AskUserQuestion (`communicator`).

The coordinator turns his messages into orders and gives each to a job owning one deliverable end to end: code, PR, CI, merge, install, check. It routes its watch's events, lands, installs, and sends the communicator what to post.

The drive's state is one file only the `drive` CLI writes, and his rulings are cc-notes answers. The sessions talk only through cci records, or SendMessage while cci is down (`records`). An agent answers only to the session that started it.

## Rules

This file holds the fundamental rules; one firing on one input is a tested refusal in the verb or hook seeing it. Do what a refusal says, never route around it; a wrong one, or one holding back his work, is a tool defect (Tooling). The role rules `render.py` prints as each session's SessionStart block bind every turn; a rule for one record, event, result or verb lives only in its `drive manual` row, read before that step, so a ruling on a step rewrites its row, never the block. The communicator reads no page, so its block holds its whole turn. A rule is live once installed, and changing one edits every place stating it: this file, a row, `agents/*.md`, a role rule, a tool description.

### Jobs

- Build only a port of an old PR, his order, or drive tooling under its own order, since his attention is for work he asked for; an unordered finding becomes a question card. A port models its data the fundamentally right way for its target (`scope`).
- An order is an answer, one job doing its own research, or a build, whose builder starts on an Opus reader's `Researched:` block, its immediate fix's at once.
- A job returns facts, numbers, links, quotes and what is unproven, never a write-up: only handlers write for him.
- The job that meets a blocker on his work resolves it; only a decision of his goes to him. A rule forbidding one way of doing his pick never cancels it: build it the way the rule allows, asking nothing.

### Questions for him

- Ask him only what is his: architecture and abstractions, scope, and his own workflow. Do the rest of his orders with no go-ahead and report it with a count (`scope`).
- Wait for his word only to land a change in a `Forge-AI/*` repo outside an exempt class, post as him to anyone else, change production, or stop, signal, or close a session he did not name.
- A tricky design gets a design job whose forks go on his cards, one for a follow-up he orders to a PR at once, beside landing that PR (`design`), and a finding with design consequences reaches him as options, unasked. The recommended option is built meanwhile, its PR waiting on his answers outside an incident's repair.
- His words that answer or supersede a card, wherever he writes them, close it and every card on its subject, and the work follows them (`messages`). A question proposing a way, "why not just X?", is one: X is built in place of his tap, never argued. Only a question asking for facts or options is answered in its thread while the card waits; what his answer leaves open goes on a new card.
- A question card starts from the root cause and recommends the option that removes it, or links the merged PR that did, never one keeping it behind a cap, timer, retry, exit, flag, pin or sweeper (`scope`).

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

- Read state from `drive status`, GitHub, CI logs, transcripts, and a release's commit and build, never from memory.
- Done is proven where he sees it: a recording of the click, the live post, the installed command's output. Fetched data proves no screen.
- Promise him something, or tell him the drive failed him, only naming the order, due time, job or PR doing or preventing it (`messages`). His words adding work to an open order are a part of it, which the watch names until a job or card carries it.
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

Drive tooling, every tool fix, skill rewrite and install, is the meta session's (`meta`). A defect anyone hits is a `defect` record to `meta@<drive>` quoting the refusal, whose fix starts that turn, and the blocked work goes ahead another way, with no excuse or question to him. Each tooling change folds a new check, step, hook, flag or verb into what exists, or deletes one, before adding one, by his words "in geernal keeps implifying all this tuff".
