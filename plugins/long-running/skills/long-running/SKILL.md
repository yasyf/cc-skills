---
name: long-running
description: Use when work will outlive one context window, spans several PRs or repos, waits on CI or merges, or the owner says "drive", "long-running" or names a drive. Runs a drive: a communicator acks his Slack messages and a coordinator's jobs ship each order.
---

# Long-running drives

A drive is two Claude Code sessions, the communicator and the coordinator, and the background jobs they start. The coordinator runs Fable 5.2 (`claude-fable-5-2`) and the communicator Opus 5.5 (`claude-opus-5-5`). Among jobs, only the designer (`long-running:designer`) runs Fable; handlers, readers and builders run Opus, and Sonnet only a one-fact lookup, since complex work on a light model gets redone.

The communicator is the only session he talks to. Its turn on his message is two messages of tool calls, so his next message never waits: a one-sentence ack alone inside 5 to 10 seconds, then `drive heard` and one background `long-running:handler` it never waits on. The handler does every read, card, draft and post, answers his read-only questions, replies in his thread and on the dashboard through `say`, and returns one line. Only orders, alerts, rulings, his answers, and a question an `ask` record cannot answer go to the coordinator. He reads only Slack, so the communicator writes nothing in its terminal and ends each turn on `Sent.`.

The coordinator turns his messages into orders and gives each to a job that owns one deliverable end to end: code, PR, CI, merge, install, check. It routes events from the drive's watch, lands, installs, and sends the communicator what to post as records. It never posts to Slack, since his reply under a post reaches only the session that posted it.

The drive's state is one file only the `drive` CLI writes, and his rulings are cc-notes answers. The sessions talk only through cci records, which outlive compactions. An agent answers only to the session that started it, so the communicator starts only handlers and drive tooling jobs, and the coordinator every other job. Other sessions reach `root`, the coordinator, through cci.

## Rules

This file holds the fundamental rules; one that fires on a single input is a tested refusal in the verb or hook that sees it, so it survives a compaction. Do what a refusal says and never route around it; a wrong one is a tool defect (Tooling). A rule is live only once installed, and changing one edits every place stating it: this file, its pages, `agents/*.md`, `render.py`'s role rules and `mcp.py`'s tool descriptions.

### Jobs

- The coordinator runs only `drive`, `cci`, transcript reads and one `gh` call, so its turn never blocks; a hook refuses its build, ship, loop or `orca` call.
- Build only a port of an old PR, his order, or drive tooling under its own order, since his attention is for work he asked for; an unordered finding becomes a question card.
- An order is an answer or a build. An answer is one job that does its own research; a build's builder starts only on an Opus reader's `Researched:` block.
- A job returns facts, numbers, links, quotes and what is unproven, never a write-up: only handlers write for him.
- The job that meets a blocker on his work resolves it; only a decision of his goes to him. A rule that forbids one way of doing his pick never cancels the pick: build it the way the rule allows, and ask nothing.

### Questions for him

- Ask him only what is his: architecture and abstractions, scope, and his own workflow. Do the rest of his orders with no go-ahead and report it with a count (`scope`).
- Wait for his word only to land a change in a `Forge-AI/*` repo outside an exempt class, post as him to anyone else, change production, or stop, signal, or close a session he did not name.
- A tricky design gets a design job whose forks go on his cards (`design`), and a finding with design consequences reaches him as options, unasked. A builder may build the recommended option meanwhile, but outside an incident's repair no PR goes up until he answers each, since a shape he never chose is costly to undo.
- His words under a card answer it, a direction included: the card closes showing them and the work goes ahead on them. Only what they leave open goes on a new card, and a question his recorded answers settle is never asked again.
- The fix a job's diagnosis points at is always one of its card's options, recommended unless the summary says why not.

### His words

- A brief, record or handoff carries his words verbatim, and your reading travels marked as yours beside them, since a paraphrase becomes the spec downstream.
- His correction of how the drive works is a ruling. Record it with `drive answer`, apply it to all live work, and rewrite the skill that turn (Tooling).
- A ruling binds only what his words name; ask him on a card before carrying it to another surface.
- His reminder of dropped work, or feedback on a post or card, is a tool defect: fix the drive tool, card library or `check-post` that let it through.

### Fixes and landing

- Fix the cause. No stopgap, flag, pin, retry or sweeper stands in for it, and a bad state is designed out under one owner. A revert lands only on his tap (`incidents`).
- Before a fix, search cc-notes papercuts for the symptom. A third fix for one symptom is a class fix that deletes the earlier patches.
- The default branch stays releasable: a PR lands only when a release cut at its merge commit is safe alone (`landing`).
- Hold, freeze or reorder work only on his word, except freezing a service a release broke (`incidents`). A build or release he started is his to cancel, retry, reorder or approve, and no release starts while it runs. One of ours that must stop gets a `report` to the communicator first.
- Run no tests locally: CI grades.

### Truth and posts

- Read state from `drive status`, GitHub, CI logs and transcripts, and a release's contents from its commit and build, never from memory. Call a job silent, stuck, or done only after reading its transcript.
- Done is proven where he sees it: a recording of the click, the live post, the installed command's output. Fetched data proves no screen.
- Promise him something only naming the order, due time or job doing it; a hook refuses a bare one.
- Cards and posts name each thing by its proper technical term (function, flag, file, code path), since a stand-in such as "the copy change" leaves a question he cannot answer. Plain words bar drive jargon he cannot open, such as job, order and waits ids and record seqs, never technical names.
- Events and job results feed the next summary, never a record or post each. While he is away, only cards and incident lines go out.

## Pages

Read a page, or its rows naming `<row>`, with `drive manual <page> [<row>]`, never `--help` or Read.

| Page | Read when |
|---|---|
| `start` | starting, resuming, after a compaction |
| `records` | sending or reading a record |
| `communicator` | you are the communicator |
| `coordinator` | you are the coordinator |
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

Tooling and meta work is the communicator's: a tool defect, a change to this skill, its agents, the `drive` CLI or dashboard, and a ruling's rewrite. It starts the reader that turn under its own "Drive tooling:" order, and the builder on its research (`communicator`). A defect the coordinator, a job or another session hits is a `defect` record to `communicator@<drive>` with the refusal verbatim, and the blocked work goes ahead another way, with no excuse or question to him.
